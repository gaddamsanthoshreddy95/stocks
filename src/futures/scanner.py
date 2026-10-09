"""Full-universe discovery and separate directional execution approval; no orders."""
from datetime import datetime
from copy import deepcopy
from src.futures.sessions import normalise_candles, completed_session_check
from src.futures.execution_safety import finalize
from src.futures.runtime import ScanRuntime
from src.futures.trade_ledger import FuturesTradeLedger, unknown
from math import isfinite
from zoneinfo import ZoneInfo
from time import perf_counter
import logging
import pandas as pd

from src.application.errors import DataUnavailableError, ValidationError
from src.event_risk.service import EventRiskService
from src.futures.backtest import FuturesIntradayBacktester
from src.futures.config import FuturesScanConfig
from src.futures.costs import FuturesCosts, trade_plan
from src.futures.scoring import score_both, prepare, directional_setup
from src.futures.research import CompanyResearch
from src.news.analysis_service import NewsAnalysisService
from src.news.today import INDEX_FUTURES, TodayNewsService
from src.quality.futures_execution import assess_execution, check, clean, FuturesExecutionConfig
from src.quality.futures_selection import active_contracts, session_vwap_quality
from src.quality.public_fundamentals import PublicFundamentalProvider
from src.sector.sector_mapper import SectorMapper
from src.sector.sector_strength import SectorStrength

LOGGER = logging.getLogger(__name__)
REQUIRED_EXECUTION = ('futures_atr_quality', 'futures_vwap_quality', 'futures_rvol_quality',
                      'futures_rsi_quality', 'futures_oi_quality', 'futures_spread_quality',
                      'futures_depth_quality', 'futures_event_quality')
DECISION_PRIORITY = {'APPROVED': 5, 'WAIT': 4, 'UNKNOWN': 3, 'TOO LATE': 2, 'REJECT': 1}


def rank_key(item):
    return (DECISION_PRIORITY.get(item.get('final_decision'), 0),
            item.get('technical_score') or 0, (item.get('plan') or {}).get('net_risk_reward') or 0)


def short_priority(item):
    evidence = item.get('evidence', {})
    if item.get('timing') == 'TOO LATE':
        return 0
    return (3 if evidence.get('fresh_support_breakdown') else
            2 if evidence.get('pullback_rejection_confirmed') else
            1 if item.get('setup_type') == 'BEARISH_TREND_CONTINUATION' and item.get('confirmed') else 0)


class FuturesOpportunityScanner:
    def __init__(self, platform, *, config=None, costs=None, news_provider=None, event_provider=None, fundamental_provider=None):
        self.platform = platform
        self.provider = platform.provider
        self.config = config or FuturesScanConfig.from_env()
        self.costs = costs or FuturesCosts.from_env()
        self.news_provider = news_provider or (lambda symbol: NewsAnalysisService.analyze(symbol, force_refresh=True, limit=16))
        self.event_provider = event_provider
        self.mapper = SectorMapper()
        self.context_cache = {}
        self._report_e_inputs = {}
        self._report_e_history = {}
        self.company_research = CompanyResearch(platform.settings.quality_config,
            fundamental_provider if fundamental_provider is not None else PublicFundamentalProvider())

    def _history(self, symbol):
        if symbol not in self.context_cache:
            try:
                raw = self.provider.get_data(symbol)
                self.context_cache[symbol] = normalise_candles(raw, 'day', getattr(self, 'research_as_of', self._clock()), allow_live_daily=True)
            except Exception as exc:
                LOGGER.warning('Directional history failed for %s: %s', symbol, type(exc).__name__)
                self.context_cache[symbol] = None
        return self.context_cache[symbol]

    def scan(self, limit=5, *, now=None, include_backtest=True, mode=None, runtime=None):
        from src.futures.rejected_analysis import append_report_d
        from src.futures.report_e import append_report_e
        if mode is not None:
            from src.futures.preparation import PreparedScanner
            report = PreparedScanner(self, runtime=runtime).run(mode, limit, now=now)
            report = append_report_d(report)
            return append_report_e(report,contexts=getattr(self,'_report_e_inputs',{}),histories=getattr(self,'_report_e_history',{}),limit=limit)
        if not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValidationError('limit must be between 1 and 50')
        self.runtime = runtime or getattr(self, 'runtime', None) or ScanRuntime.from_env()
        fixed_now = now
        now = pd.Timestamp(now or datetime.now(ZoneInfo('Asia/Kolkata')))
        now = now.tz_localize('Asia/Kolkata') if now.tz is None else now.tz_convert('Asia/Kolkata')
        self._fixed_clock = fixed_now is not None
        self._clock = (lambda: now) if fixed_now is not None else (lambda: pd.Timestamp.now(tz='Asia/Kolkata'))
        symbols = sorted(set(self.platform._universe_symbols()) - INDEX_FUTURES)
        if not symbols:
            raise DataUnavailableError('No configured NSE futures stock universe is available')
        self.context_cache.clear()
        self._report_e_inputs = {}
        self._report_e_history = {}
        self._final_frames = {}
        begin = getattr(self.provider, 'begin_live_refresh', None)
        end = getattr(self.provider, 'end_live_refresh', None)
        refresh_started = begin is not None and not bool(getattr(self.provider, 'live_refresh_active', False))
        if refresh_started:
            begin(symbols)
        try:
            report = self._scan(symbols, limit, now, include_backtest)
        finally:
            if refresh_started and end:
                end()
        report = append_report_d(report)
        if getattr(self,'defer_report_e',False):
            return report
        return append_report_e(report,contexts=self._report_e_inputs,histories=self._report_e_history,limit=limit)

    def _read_execution_budget(self, instruments):
        try:
            from src.futures.gateway import KiteGateway
            gateway = getattr(self, 'account_gateway', None) or KiteGateway(self.provider, self.runtime)
            snapshot = gateway.account_snapshot(self._clock() if self._fixed_clock else None)
            return FuturesTradeLedger(self.runtime.ledger_path, self.config.maximum_daily_entries,
                self.runtime.reconciliation_max_age_seconds).reconcile(snapshot, self._clock(), instruments)
        except Exception as exc:
            return unknown('BROKER_RECONCILIATION_FAILED:'+type(exc).__name__, self._clock())

    def _scan(self, symbols, limit, now, include_backtest):
        total_started = perf_counter()
        stage_started = total_started
        timings = {}
        benchmark = self._history('NIFTY 50')
        all_items, histories, failures = [], {}, []
        for symbol in symbols:
            history = self._history(symbol)
            sector = self.mapper.get_sector(symbol)
            sector_symbol = SectorStrength.KITE_INDEX_SYMBOLS.get(sector)
            sector_history = self._history(sector_symbol) if sector_symbol else None
            try:
                both = score_both(history, config=self.config, benchmark=benchmark, sector=sector_history)
            except (ValueError, TypeError, KeyError, IndexError) as exc:
                failures.append({'symbol': symbol, 'reason': type(exc).__name__, 'detail':str(exc), 'history_rows':len(history) if history is not None else 0})
                both = {side: {'side': side, 'technical_score': None, 'timing': 'UNKNOWN',
                               'setup_type': 'UNVERIFIED', 'reason_codes': ['DISCOVERY_DATA_UNAVAILABLE'],
                               'evidence': {}} for side in ('LONG', 'SHORT')}
            histories[symbol] = (history, sector_history)
            for side, item in both.items():
                item['daily_discovery'] = {**deepcopy(item), 'basis': 'DAILY_UNDERLYING_RESEARCH',
                    'provisional_daily_candle': bool(history is not None and not history.empty and pd.notna(history.iloc[-1].get('IS_LIVE_CANDLE')) and history.iloc[-1].get('IS_LIVE_CANDLE', False)),
                    'execution_confirmation': False}
                item['legacy_field_provenance'] = {'confirmed': 'DAILY_DISCOVERY', 'timing': 'DAILY_DISCOVERY', 'evidence': 'DAILY_DISCOVERY',
                    'technical_score': 'FUTURES_5_MINUTE_WHEN_REVIEWED_OTHERWISE_DAILY', 'BULLISH_SCORE': 'DAILY_DISCOVERY', 'BEARISH_SCORE': 'DAILY_DISCOVERY'}
                item.update({'symbol': symbol, 'sector': sector, 'BULLISH_SCORE': both['LONG']['technical_score'],
                             'BEARISH_SCORE': both['SHORT']['technical_score'], 'final_decision': 'WAIT',
                             'execution_reviewed': False, 'generated_at': now.isoformat(),
                             'historical': {'status': 'NOT_EVALUATED', 'target_first_percent': None}})
                if item['technical_score'] is None:
                    item['final_decision'] = 'UNKNOWN'
                elif item['technical_score'] < self.config.watch_score:
                    item['final_decision'] = 'REJECT'
                    item['reason_codes'].append('LOW_DIRECTIONAL_SCORE')
                elif item['timing'] == 'TOO LATE':
                    item['final_decision'] = 'TOO LATE'
                all_items.append(item)
        # Each direction has its own full-universe pool. No bullish shortlist is reused.
        selected = []
        for side in ('LONG', 'SHORT'):
            pool = [item for item in all_items if item['side'] == side and
                    item['technical_score'] is not None and item['technical_score'] >= self.config.watch_score
                    and item['final_decision'] != 'TOO LATE']
            selected.extend(sorted(pool, key=lambda item: (short_priority(item) if side == 'SHORT' else 0,
                                   item['technical_score']), reverse=True)[:self.config.review_per_direction])
        selected_ids = {(item['symbol'], item['side']) for item in selected}
        for item in all_items:
            if (item['symbol'], item['side']) not in selected_ids and item['final_decision'] == 'WAIT':
                item['reason_codes'].append('OUTSIDE_DIRECTIONAL_EXECUTION_REVIEW_LIMIT')
        timings['discovery_seconds'] = perf_counter()-stage_started
        stage_started = perf_counter()
        try:
            instruments = self.provider.get_nfo_instruments()
        except Exception:
            instruments = None
        initial_reconciliation = self._read_execution_budget(instruments)
        service, context = None, None
        if self.event_provider is None:
            try:
                service = EventRiskService(self.platform.settings)
                context = service.build_daily_context(as_of=now.to_pydatetime())
            except Exception as exc:
                LOGGER.warning('Directional event context unavailable: %s', type(exc).__name__)
        # Shared per-symbol news/history; fresh execution quotes are fetched after news work.
        news_cache, event_cache, backtests = {}, {}, {}
        # Complete company/news research is independent of score and review budget.
        # Every discovered symbol is researched once, then shared by both directions.
        prefetch = getattr(self.company_research.engine.fundamental_provider, 'prefetch', None)
        if prefetch:
            try:
                prefetch(symbols)
            except Exception as exc:
                LOGGER.warning('Company prefetch incomplete: %s', type(exc).__name__)
        research_inputs = {}
        for item in all_items:
            symbol = item['symbol']
            if symbol not in news_cache:
                try:
                    news_cache[symbol] = self.news_provider(symbol)
                except Exception as exc:
                    news_cache[symbol] = {'news_state': 'FETCH_FAILED', 'reason': type(exc).__name__}
                try:
                    event_cache[symbol] = (self.event_provider(symbol, item['sector'], news_cache[symbol], now)
                        if self.event_provider else service.assess_candidate(
                            item, context, news_context=news_cache[symbol], as_of=now.to_pydatetime()).to_dict())
                except Exception:
                    event_cache[symbol] = {'event_data_availability_state': 'UNAVAILABLE', 'hard_block': True}
                stock, sector_history = histories[symbol]
                try:
                    stock = self.provider.get_annual_history(symbol) if hasattr(self.provider, 'get_annual_history') else stock
                except Exception:
                    pass
                try:
                    sector_symbol = SectorStrength.KITE_INDEX_SYMBOLS.get(item['sector'])
                    if sector_symbol and hasattr(self.provider, 'get_annual_history'):
                        sector_history = self.provider.get_annual_history(sector_symbol)
                except Exception:
                    pass
                try:
                    bars = normalise_candles(self.provider.get_session_intraday(symbol), '5minute', getattr(self, 'research_as_of', now))
                    stock_vwap = session_vwap_quality(bars, item.get('evidence', {}).get('price'), now=getattr(self,'research_as_of',now).to_pydatetime())
                except Exception:
                    stock_vwap = None
                try:
                    stock = normalise_candles(stock, 'day', getattr(self, 'research_as_of', now), allow_live_daily=True) if stock is not None and not stock.empty else stock
                except (ValueError, TypeError, AttributeError) as exc:
                    stock = None
                    failures.append({'symbol': symbol, 'source': 'annual_equity_history', 'detail': str(exc)})
                try:
                    sector_history = normalise_candles(sector_history, 'day', getattr(self, 'research_as_of', now), allow_live_daily=True) if sector_history is not None and not sector_history.empty else sector_history
                except (ValueError, TypeError, AttributeError) as exc:
                    sector_history = None
                    failures.append({'symbol': symbol, 'source': 'annual_sector_history', 'detail': str(exc)})
                research_inputs[symbol] = (stock, sector_history, stock_vwap)
            stock, sector_history, stock_vwap = research_inputs[symbol]
            item['news'] = news_cache[symbol]
            item['event_risk'] = event_cache[symbol]
            item['company_research'] = self.company_research.assess(symbol, item['sector'], item['side'],
                news=news_cache[symbol], stock_history=stock, sector_history=sector_history, session_vwap=stock_vwap)
            research = item['company_research']
            if not research['eligible'] and item['final_decision'] == 'WAIT':
                item['final_decision'] = ('UNKNOWN' if research['unavailable_checks'] else
                                          'WAIT' if research['policy_review_required'] else 'REJECT')
                item['reason_codes'].extend('RESEARCH_UNKNOWN:'+key for key in research['unavailable_checks'])
                item['reason_codes'].extend('RESEARCH_FAIL:'+key for key in research['failed_checks'])
        prepared_execution = []
        timings['research_news_events_seconds'] = perf_counter()-stage_started
        stage_started = perf_counter()
        for item in selected:
            symbol = item['symbol']
            contracts = active_contracts(symbol, instruments, now.date()) if instruments is not None else []
            if not contracts:
                item.update({'final_decision': 'UNKNOWN', 'news': news_cache[symbol], 'event_risk': event_cache[symbol]})
                item['reason_codes'].append('ACTIVE_FUTURES_CONTRACT_UNVERIFIED')
                continue
            contract = contracts[0]
            try:
                data = self.provider.get_futures_execution_data(symbol, contract)
            except Exception:
                data = {}
            if not include_backtest and getattr(self, 'historical_provider', None):
                backtests[symbol] = self.historical_provider(symbol, contract)
            if include_backtest and symbol not in backtests and data.get('intraday') is not None and not data['intraday'].empty:
                try:
                    completed = clean(normalise_candles(data['intraday'], '5minute', self._clock()))
                    completed = completed.loc[completed.index+pd.Timedelta(minutes=5) <= self._clock()]
                    backtests[symbol] = FuturesIntradayBacktester(self.config, self.costs).run(
                        completed, contract['lot_size'], daily_history=data.get('daily'), benchmark_history=benchmark)
                except (ValueError, TypeError, KeyError, IndexError) as exc:
                    backtests[symbol] = {'status': 'UNAVAILABLE', 'reason': type(exc).__name__}
            prepared_execution.append((item, data, contract))
        timings['historical_validation_seconds'] = perf_counter()-stage_started
        stage_started = perf_counter()
        # Finish expensive historical work for BOTH directions before final approval.
        # Otherwise the first approval could age while later stocks are backtested.
        for item, data, contract in prepared_execution:
            symbol = item['symbol']
            evaluation_now = self._clock()
            try:
                news_stamp = pd.Timestamp(news_cache[symbol].get('checked_at'))
                if news_stamp.tz is None or not 0 <= (evaluation_now-news_stamp).total_seconds() <= 900:
                    news_cache[symbol] = self.news_provider(symbol)
            except Exception:
                news_cache[symbol] = {'news_state': 'FETCH_FAILED'}
            if include_backtest:
                try:
                    data = self.provider.get_futures_execution_data(symbol, contract)
                except Exception:
                    data = {}
            evaluation_now = self._clock()
            try:
                session_bars = normalise_candles(self.provider.get_session_intraday(symbol), '5minute', getattr(self, 'research_as_of', evaluation_now))
                reference_price = data.get('spot_price')
                if getattr(self,'research_as_of',None) is not None:
                    reference_history = self.provider.get_data(symbol)
                    reference_price = float(reference_history.Close.iloc[-1]) if reference_history is not None and not reference_history.empty else None
                refreshed_vwap = session_vwap_quality(session_bars, reference_price, now=getattr(self,'research_as_of',evaluation_now).to_pydatetime())
            except Exception:
                refreshed_vwap = None
            annual_stock, annual_sector, _ = research_inputs[symbol]
            item['company_research'] = self.company_research.assess(symbol, item['sector'], item['side'],
                news=news_cache[symbol], stock_history=annual_stock, sector_history=annual_sector, session_vwap=refreshed_vwap)
            item['company_research']['checked_at'] = evaluation_now.isoformat()
            item['reason_codes'] = [reason for reason in item['reason_codes'] if not reason.startswith('RESEARCH_')]
            if service is not None and context is not None:
                try:
                    event_cache[symbol] = service.assess_candidate(item, context,
                        news_context=news_cache[symbol], as_of=evaluation_now.to_pydatetime()).to_dict()
                except Exception:
                    event_cache[symbol] = {'event_data_availability_state': 'UNAVAILABLE', 'hard_block': True}
            self._execution(item, data, contract, histories[symbol], news_cache[symbol], event_cache[symbol], evaluation_now,
                            backtests.get(symbol))
        timings['execution_validation_seconds'] = perf_counter()-stage_started
        served_at = self._clock()
        max_age = FuturesExecutionConfig.from_env().maximum_quote_age_seconds
        for item in all_items:
            if item['final_decision'] != 'APPROVED':
                continue
            stamp = pd.Timestamp(item['futures_quote']['timestamp'])
            stamp = stamp.tz_localize('Asia/Kolkata') if stamp.tz is None else stamp.tz_convert('Asia/Kolkata')
            if not 0 <= (served_at-stamp).total_seconds() <= max_age:
                item['final_decision'] = 'UNKNOWN'
                item['reason_codes'].append('QUOTE_EXPIRED_BEFORE_REPORT_COMPLETED')
                item['missing_execution_checks'].append('FINAL_REPORT_QUOTE_FRESHNESS')
                if item.get('short_entry'):
                    item['short_entry'].update(state='UNVERIFIED', execution_approved=False)
        stage_started = perf_counter()
        reconciliation = self._read_execution_budget(instruments)
        self._reconciliation = reconciliation
        temporary = {'reviewed': all_items, 'report_c': []}
        finalize(temporary, self._final_frames, reconciliation, self._clock(), self.config, self.runtime.reconciliation_max_age_seconds)
        self._execution_safety = temporary['execution_safety']
        self._execution_safety['initial_reconciliation'] = initial_reconciliation
        timings['read_only_reconciliation_and_final_safety_seconds'] = perf_counter()-stage_started
        served_at = self._clock()
        long_items = sorted((item for item in all_items if item['side'] == 'LONG' and item.get('technical_score') is not None), key=rank_key, reverse=True)
        short_items = sorted((item for item in all_items if item['side'] == 'SHORT' and item.get('technical_score') is not None),
            key=lambda item: (DECISION_PRIORITY.get(item.get('final_decision'), 0), short_priority(item), *rank_key(item)[1:]), reverse=True)
        eligible = sorted((item for item in all_items if item['final_decision'] == 'APPROVED'), key=rank_key, reverse=True)
        # Additional summaries reuse backtests already completed by this execution.
        # No existing trade, metric, group or candidate is modified.
        from src.futures.report_e_config import ReportEConfig
        from src.futures.report_e_history import build_history_evidence
        try:
            e_config=ReportEConfig.from_env()
            for item in all_items:
                contract=item.get('contract',{}).get('tradingsymbol')
                historical=backtests.get(item['symbol'])
                if contract and historical and 'trades' in historical and contract not in self._report_e_history:
                    self._report_e_history[contract]=build_history_evidence(historical,self.config,e_config,instrument=contract)
        except (ValueError,TypeError,KeyError):
            pass  # Report E reports unavailable evidence; A-D remain independent.
        timings['total_seconds'] = perf_counter()-total_started
        return {'report_type': 'bidirectional_futures', 'generated_at': served_at.isoformat(), 'started_at': now.isoformat(),
                'timings': timings, 'execution_safety': self._execution_safety,
                'universe_size': len(symbols), 'long_discovery_count': sum(i['side']=='LONG' for i in all_items),
                'short_discovery_count': sum(i['side']=='SHORT' for i in all_items),
                'rankable_long_count':len(long_items), 'rankable_short_count':len(short_items),
                'execution_review_count': sum(item['execution_reviewed'] for item in all_items),
                'report_a': long_items[:limit], 'report_b': short_items[:limit], 'report_c': eligible[:limit],
                'reviewed': all_items, 'failures': failures, 'approved_count': len(eligible),
                'config': self.config.__dict__, 'cost_assumptions': self.costs.__dict__,
                'historical_validation': 'Weights are hypotheses. Holdout metrics validate technical signals, not missing historical news/depth.',
                'execution_policy': 'Scanner only; no orders. Margin displayed only when supplied; no automatic margin lookup.'}

    def _execution(self, item, data, contract, histories, news, event, now, historical):
        item.update({'contract': contract, 'news': news, 'event_risk': event, 'execution_reviewed': True,
                     'checked_at': now.isoformat()})
        if not hasattr(self, '_final_frames'):
            self._final_frames = {}
        direction = 'BULLISH' if item['side'] == 'LONG' else 'BEARISH'
        future_setup = None
        daily_atr = None
        try:
            daily = prepare(normalise_candles(data.get('daily'), 'day', now))
            daily = daily.loc[daily.index.date < now.date()]
            daily_atr = float(daily.ATR.iloc[-1])
            normalized = normalise_candles(data.get('intraday'), '5minute', now)
            self._final_frames[item['symbol']+':'+item['side']] = normalized
            frame = prepare(normalized)
            frame = frame.loc[frame.index+pd.Timedelta(minutes=5) <= now]
            future_setup = directional_setup(frame, item['side'], config=self.config, daily_atr=daily_atr, prepared=True)
            # Optional retention cannot alter the original execution path.
            try:
                if not frame.empty:
                    fields=('Open','High','Low','Close','Volume','RSI','MACD_HISTOGRAM','EMA9','EMA21','ADX')
                    rows=[{'timestamp':stamp.isoformat(),**{field:float(row[field]) if isfinite(row[field]) else None for field in fields}}
                          for stamp,row in frame.tail(3).iterrows()]
                    last=frame.index[-1]
                    end=min(now.floor('5min')-pd.Timedelta(minutes=5),now.normalize()+pd.Timedelta(hours=15,minutes=25))
                    expected=pd.date_range(now.normalize()+pd.Timedelta(hours=9,minutes=15),end,freq='5min')
                    self._report_e_inputs[item['symbol']+':'+item['side']]={'completed_candles':rows,
                        'missing_candles':[stamp.isoformat() for stamp in expected.difference(frame.index)],
                        'last_candle_at':last.isoformat(),'basis':'COMPLETED_FUTURES_5_MINUTE'}
            except (ValueError, TypeError, KeyError, AttributeError):
                pass
            item['futures_setup'] = future_setup
            if future_setup.get('technical_score') is not None:
                item['discovery_technical_score'] = item['technical_score']
                item['discovery_setup_type'] = item['setup_type']
                item['technical_score'] = future_setup['technical_score']
                item['setup_type'] = future_setup['setup_type']
                item['technical_score_timeframe'] = 'COMPLETED_FUTURES_5_MINUTE'
        except (ValueError, TypeError, KeyError, IndexError):
            pass
        levels = future_setup.get('evidence', {}) if future_setup else {}
        # assess_execution's target-space proxy is not used: below, compare actual futures levels to futures entry.
        checks = assess_execution(data, stock_history=histories[0], sector_history=histories[1],
                                  levels={}, event=event, direction=direction, now=now)
        oi = checks['futures_oi_quality']
        known_regimes = {'LONG_BUILDUP', 'SHORT_BUILDUP', 'LONG_UNWINDING', 'SHORT_COVERING', 'UNCHANGED'}
        if oi.status != 'UNKNOWN' and oi.reason_codes and oi.reason_codes[0] in known_regimes:
            pc = oi.factors.get('price_change_percent', 0)
            directional = pc > 0 if item['side'] == 'LONG' else pc < 0
            checks['futures_oi_quality'] = check(True, {**oi.factors, 'directional_alignment': directional}, oi.reason_codes[0])
            if item['side'] == 'SHORT' and oi.reason_codes[0] == 'SHORT_COVERING':
                item['reason_codes'].append('SHORT_COVERING_EVIDENCE_REQUIRE_CONFIRMATION')
        item['execution_checks'] = {key: value.to_dict() for key, value in checks.items()}
        # The new scanner uses actual futures levels, not the legacy underlying proxy.
        item['execution_checks'].pop('futures_target_space_quality', None)
        item['execution_checks']['listed_futures_contract'] = {
            'status': 'PASS', 'factors': contract, 'reason_codes': ['EXACT_ACTIVE_NSE_FUTURES_EXPIRY']}
        quote = data.get('quote') or {}
        sudden_covering = False
        if item['side'] == 'SHORT':
            try:
                bars = clean(normalise_candles(data.get('intraday'), '5minute', now))
                bars = bars.loc[bars.index+pd.Timedelta(minutes=5) <= now]
                previous = bars.iloc[-1]
                oi_before, oi_now = float(previous.OI), float(quote['oi'])
                if not all(isfinite(value) and value > 0 for value in (oi_before, oi_now)):
                    raise ValueError('Invalid intraday OI')
                price_change = (float(quote['last_price'])/float(previous.Close)-1)*100
                oi_change = (oi_now/oi_before-1)*100
                sudden_covering = price_change >= self.config.short_covering_price_percent and oi_change <= -self.config.short_covering_oi_percent
                item['intraday_oi_change'] = {'price_change_percent': price_change, 'oi_change_percent': oi_change,
                                              'baseline_timestamp': bars.index[-1].isoformat()}
            except (ValueError, TypeError, KeyError, IndexError, AttributeError, ZeroDivisionError):
                item['intraday_oi_change'] = {'status': 'UNKNOWN'}
        item['futures_quote'] = {key: quote.get(key) for key in ('last_price', 'timestamp', 'oi', 'volume')}
        missing = [key for key in REQUIRED_EXECUTION if checks[key].status == 'UNKNOWN']
        failed = [key for key in REQUIRED_EXECUTION if checks[key].status == 'FAIL']
        reasons = [*item['reason_codes'], *missing, *failed]
        if self.config.target_fraction != .003 or self.config.stop_fraction != .002 or self.config.minimum_net_rr < 1:
            failed.append('UNAPPROVED_STRATEGY_CONFIGURATION')
            reasons.append('UNAPPROVED_STRATEGY_CONFIGURATION')
        margin = data.get('margin_by_side', {}).get(item['side'], data.get('margin_per_lot'))
        available_capital = data.get('available_capital', self.platform.settings.capital)
        if data.get('require_margin'):
            try:
                stamp = pd.Timestamp(data['margin_checked_at'])
                stamp = stamp.tz_localize('Asia/Kolkata') if stamp.tz is None else stamp
                if (margin is None or not isfinite(margin) or margin <= 0 or available_capital is None
                        or not isfinite(available_capital) or available_capital < 0 or not 0 <= (now-stamp).total_seconds() <= 120):
                    raise ValueError('Margin or funds missing/stale')
            except (ValueError, KeyError, TypeError):
                missing.append('FUTURES_MARGIN_OR_AVAILABLE_FUNDS_UNVERIFIED')
                reasons.append('FUTURES_MARGIN_OR_AVAILABLE_FUNDS_UNVERIFIED')
        research = item.get('company_research')
        research_failed = []
        review_required = False
        if research is None:
            missing.append('COMPANY_RESEARCH_UNVERIFIED')
            reasons.append('COMPANY_RESEARCH_UNVERIFIED')
        else:
            for name in research['unavailable_checks']:
                missing.append('RESEARCH_UNKNOWN:'+name)
                reasons.append('RESEARCH_UNKNOWN:'+name)
            research_failed = research['failed_checks']
            reasons.extend('RESEARCH_FAIL:'+name for name in research_failed)
            review_required = research['policy_review_required']
            if review_required:
                reasons.append('RESEARCH_POLICY_REVIEW_REQUIRED_NO_AUTOMATIC_WAIVER')
        if future_setup is None or future_setup.get('technical_score') is None:
            missing.append('FUTURES_SETUP_UNVERIFIED')
            reasons.append('FUTURES_SETUP_UNVERIFIED')
        if future_setup:
            reasons.extend(future_setup['reason_codes'])
        news_check = TodayNewsService.direction_check(news,
            checks['futures_oi_quality'].factors.get('price_change_percent'), direction, now=now.to_pydatetime())
        item['news_alignment'] = news_check
        if not news_check['approved']:
            reasons.extend(news_check['reason_codes'])
            if news_check['status'] == 'UNVERIFIED':
                missing.append('NEWS_UNVERIFIED')
            else:
                failed.append('NEWS_CONFLICT')
        plan = None
        try:
            quote_stamp = pd.Timestamp(quote['timestamp'])
            quote_stamp = quote_stamp.tz_localize('Asia/Kolkata') if quote_stamp.tz is None else quote_stamp.tz_convert('Asia/Kolkata')
            if not 0 <= (now-quote_stamp).total_seconds() <= FuturesExecutionConfig.from_env().maximum_quote_age_seconds:
                raise ValueError('Futures executable quote stale')
            if completed_session_check(data.get('intraday'), now)['status'] != 'PASS':
                raise ValueError('Latest completed Futures candle unavailable')
            lot_size = contract['lot_size']
            depth = quote['depth']
            entry_rows = depth['sell' if item['side'] == 'LONG' else 'buy']
            exit_rows = depth['buy' if item['side'] == 'LONG' else 'sell']
            entry_prices = [float(row['price']) for row in entry_rows if float(row['quantity']) > 0 and float(row['price']) > 0]
            entry = min(entry_prices) if item['side'] == 'LONG' else max(entry_prices)
            initial_plan = trade_plan(entry, lot_size, item['side'], config=self.config, costs=self.costs,
                risk_budget=self.platform.settings.capital*self.platform.settings.risk_percent/100,
                trade_date=now.date(), margin_per_lot=margin, available_capital=available_capital,
                movement_reference_price=data.get('spot_price'))
            available = sum(float(row['quantity']) for row in entry_rows)
            requested = lot_size * max(1, initial_plan['number_of_lots'])
            if min(available, sum(float(row['quantity']) for row in exit_rows)) < requested/self.config.maximum_participation:
                failed.append('INSUFFICIENT_EXECUTION_LIQUIDITY')
                reasons.append('INSUFFICIENT_EXECUTION_LIQUIDITY')
            remaining = requested
            total = 0
            ordered = sorted(entry_rows, key=lambda row: float(row['price']), reverse=item['side'] == 'SHORT')
            for row in ordered:
                if float(row['price']) <= 0:
                    continue
                take = min(remaining, max(0, float(row['quantity'])))
                total += take*float(row['price'])
                remaining -= take
            if remaining > 0:
                raise ValueError('Insufficient depth for modeled fill')
            weighted = total/requested
            impact_bps = abs(weighted/entry-1)*10000
            spread_bps = checks['futures_spread_quality'].factors.get('spread_percent', 0)*100
            item['expected_slippage_bps'] = impact_bps + self.costs.slippage_bps + spread_bps/2
            if item['expected_slippage_bps'] > self.config.maximum_slippage_bps:
                failed.append('EXCESSIVE_EXECUTION_SLIPPAGE')
                reasons.append('EXCESSIVE_EXECUTION_SLIPPAGE')
            plan = trade_plan(weighted, lot_size, item['side'], config=self.config, costs=self.costs,
                risk_budget=self.platform.settings.capital*self.platform.settings.risk_percent/100,
                trade_date=now.date(), margin_per_lot=margin, available_capital=available_capital,
                exit_spread_bps=spread_bps, movement_reference_price=data.get('spot_price'))
            plan.update(entry_reference_type='DEPTH_WEIGHTED_QUOTE_ESTIMATE_NOT_AN_EXECUTED_FILL',
                quote_timestamp=quote.get('timestamp'), price_basis='ACTUAL_FUTURES_ENTRY_REFERENCE',
                tick_size=contract.get('tick_size'), fixed_levels_are_order_prices=False)
            item['plan'] = plan
            if item['side'] == 'SHORT':
                trigger = levels.get('short_trigger_price')
                invalidation = levels.get('setup_invalidation_price')
                entry_state = 'TRIGGER_CROSSED'
                if trigger is None or invalidation is None:
                    missing.append('SHORT_TRIGGER_OR_INVALIDATION_UNVERIFIED')
                    reasons.append('SHORT_TRIGGER_OR_INVALIDATION_UNVERIFIED')
                    entry_state = 'UNKNOWN'
                elif weighted > trigger:
                    entry_state = 'WAIT_FOR_SHORT_TRIGGER'
                    reasons.append('SHORT_TRIGGER_NOT_CROSSED')
                elif (trigger-weighted)/trigger*100 > self.config.short_maximum_trigger_distance_percent:
                    entry_state = 'TOO_LATE'
                    reasons.append('SHORT_ENTRY_TOO_FAR_BELOW_TRIGGER')
                if invalidation is not None and plan['stop_loss'] <= invalidation:
                    failed.append('SHORT_STOP_DOES_NOT_COVER_SETUP_INVALIDATION')
                    reasons.append('SHORT_STOP_DOES_NOT_COVER_SETUP_INVALIDATION')
                reference = weighted
                if ((weighted-plan['target'])/reference < .003-1e-10
                        or abs((plan['stop_loss']-weighted)/reference-.002) > 1e-10):
                    failed.append('SHORT_TARGET_OR_STOP_DOES_NOT_MATCH_REQUIRED_MOVEMENT')
                    reasons.append('SHORT_TARGET_OR_STOP_DOES_NOT_MATCH_REQUIRED_MOVEMENT')
                item['short_entry'] = {'state': entry_state, 'trigger_price': trigger,
                    'actual_futures_entry': weighted, 'target': plan['target'], 'stop_loss': plan['stop_loss'],
                    'setup_invalidation_price': invalidation,
                    'setup_type': item['setup_type'], 'checked_at': now.isoformat()}
            obstacle = levels.get('resistance' if item['side'] == 'LONG' else 'support')
            if obstacle is None:
                missing.append('FUTURES_TARGET_SPACE_UNVERIFIED')
                reasons.append('FUTURES_TARGET_SPACE_UNVERIFIED')
            else:
                room = (obstacle-weighted) * (1 if item['side'] == 'LONG' else -1)
                required = weighted*self.config.target_fraction + weighted*item['expected_slippage_bps']/10000
                item['futures_target_space'] = {'available_points': room, 'required_points': required, 'level': obstacle}
                if item['side'] == 'SHORT':
                    item['short_entry'].update({'remaining_downside_points': room,
                                                'remaining_downside_percent': room/weighted*100})
                item['execution_checks']['futures_target_space_quality'] = {
                    'status': 'PASS' if room >= required else 'FAIL', 'factors': item['futures_target_space'],
                    'reason_codes': ['EXACT_CONTRACT_TARGET_SPACE_AFTER_COST_BUFFER']}
                if room < required:
                    failed.append('INSUFFICIENT_FUTURES_TARGET_SPACE')
                    reasons.append('INSUFFICIENT_FUTURES_TARGET_SPACE')
            if plan['number_of_lots'] < 1 or plan['net_profit_at_target'] <= 0 or plan['net_risk_reward'] < self.config.minimum_net_rr:
                failed.append('NET_ECONOMICS_OR_RISK_BUDGET_FAILED')
                reasons.append('NET_ECONOMICS_OR_RISK_BUDGET_FAILED')
        except (ValueError, TypeError, KeyError, IndexError, ZeroDivisionError):
            missing.append('EXECUTION_PLAN_UNVERIFIED')
            reasons.append('EXECUTION_PLAN_UNVERIFIED')
        item['historical'] = {'status': 'UNAVAILABLE', 'target_first_percent': None,
                              'reason': 'No completed exact-contract backtest result',
                              'minimum_signals': self.config.minimum_historical_signals}
        if historical and 'groups' in historical:
            group = f"side_setup:{item['side']}:{item['setup_type']}:OUT_OF_SAMPLE"
            item['historical'] = historical['groups'].get(group, {
                'status': 'INSUFFICIENT', 'historical_signals': 0, 'target_first_percent': None,
                'reason': 'No matching direction/setup trades in the chronological holdout',
                'minimum_signals': self.config.minimum_historical_signals})
            item['historical_validation'] = historical['validation_status']
        elif historical:
            item['historical'].update(historical)
        timing = future_setup.get('timing') if future_setup else 'UNKNOWN'
        short_entry_state = item.get('short_entry', {}).get('state')
        if missing:
            decision = 'UNKNOWN'
        elif timing == 'TOO LATE' or short_entry_state == 'TOO_LATE':
            decision = 'TOO LATE'
        elif failed:
            decision = 'REJECT'
        elif review_required:
            decision = 'WAIT'
        elif research_failed:
            decision = 'REJECT'
        elif (short_entry_state == 'WAIT_FOR_SHORT_TRIGGER' or sudden_covering or item['technical_score'] < self.config.minimum_score or
              future_setup['technical_score'] < self.config.minimum_score or timing != 'READY' or not future_setup.get('confirmed')):
            decision = 'WAIT'
            reasons.append('DIRECTIONAL_SETUP_CONFIRMATION_PENDING')
            if sudden_covering:
                reasons.append('SUDDEN_INTRADAY_SHORT_COVERING_WAIT_FOR_REJECTION')
        else:
            decision = 'APPROVED'
            reasons.append('SETUP_AND_MANDATORY_EXECUTION_GATES_PASSED')
        item['final_decision'] = decision
        item['reason_codes'] = list(dict.fromkeys(reasons))
        item['missing_execution_checks'] = missing
        item['failed_execution_checks'] = failed
        if item.get('short_entry'):
            entry = item['short_entry']
            entry['trigger_status'] = entry['state']
            entry['setup_confirmed'] = bool(future_setup and future_setup.get('confirmed'))
            entry['execution_approved'] = decision == 'APPROVED'
            entry['state'] = ('VALID_SHORT_ENTRY' if decision == 'APPROVED' else 'TOO_LATE' if decision == 'TOO LATE'
                              else 'UNVERIFIED' if decision == 'UNKNOWN' else 'REJECTED' if decision == 'REJECT'
                              else 'WAIT_FOR_SHORT_TRIGGER' if entry['trigger_status'] == 'WAIT_FOR_SHORT_TRIGGER'
                              else 'WAIT_FOR_CONFIRMATION_OR_RESEARCH')
