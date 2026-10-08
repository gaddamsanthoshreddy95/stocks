"""Prepared-data modes feeding the existing scanner and unchanged research gates."""
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import asdict, replace
from datetime import datetime
from time import monotonic, perf_counter
from zoneinfo import ZoneInfo
import os
import pandas as pd

from src.application.errors import DataUnavailableError, ValidationError
from src.event_risk.service import EventRiskService
from src.futures.backtest import FuturesIntradayBacktester
from src.futures.cache import PreparationCache, fingerprint, candle_fingerprint
from src.futures.gateway import KiteGateway
from src.futures.data_recovery import recover_existing_data, source_failure, completed_frame, completed_session_cutoff
from src.futures.live_news import collect, fresh_result
from src.futures.runtime import ScanRuntime, MODES
from src.futures.sessions import normalise_candles, CALCULATION_VERSION
from src.futures.execution_safety import finalize
from src.news.analysis_service import NewsAnalysisService
from src.news.today import stock_aliases, INDEX_FUTURES
from src.quality.models import FundamentalSnapshot
from src.quality.public_fundamentals import PublicFundamentalProvider
from src.quality.futures_selection import active_contracts
from src.sector.sector_strength import SectorStrength


def bounded_map(operation, identities, workers, budget=None):
    """Never wait on a failed or over-budget source indefinitely."""
    pool = ThreadPoolExecutor(max_workers=workers)
    futures = {pool.submit(operation, identity): identity for identity in identities}
    done, pending = wait(futures, timeout=budget)
    results, errors = {}, {}
    for future in done:
        key = futures[future]
        try:
            results[key] = future.result()
        except Exception as exc:
            errors[key] = source_failure(exc)
    for future in pending:
        future.cancel()
        errors[futures[future]] = 'SOURCE_DEADLINE_EXCEEDED'
    pool.shutdown(wait=not pending, cancel_futures=True)
    return results, errors


def next_session(day):
    holidays = {value.strip() for value in os.getenv('MARKET_HOLIDAYS_IST', '').split(',')}
    following = day+pd.Timedelta(days=1)
    while following.weekday() >= 5 or following.date().isoformat() in holidays:
        following += pd.Timedelta(days=1)
    return following.date().isoformat()


class CachedCompanies:
    def __init__(self, manager):
        self.manager = manager

    def prefetch(self, symbols):
        return None  # Preparation already did this; never start research in LIVE_SCAN.

    def get_fundamentals(self, symbol):
        record = self.manager.cache.get('company', symbol, now=self.manager.clock())
        if record is None:
            return None
        data = dict(record['payload'])
        for field in ('quarterly_revenue_growth_pct','quarterly_profit_growth_pct'):
            if data.get(field) is not None:
                data[field] = tuple(data[field])
        market = self.manager.cache.get('market_fields',symbol,now=self.manager.clock())
        if market:
            data.update(market['payload']['values'])
            data['evidence']={**data.get('evidence',{}),**market['payload'].get('evidence',{})}
        else:
            for field in ('pe_ratio','sector_pe','delivery_percent','monthly_delivery_percent'):
                data[field]=None
        return FundamentalSnapshot(**data)


class PreparedDataProvider:
    """Incremental historical reads and batched quote snapshots, not a second scanner."""
    def __init__(self, manager):
        self.manager = manager
        self.original = manager.scanner.provider
        self.quotes = {}
        self.frames = {}
        self.execution = {}
        self.quote_fetched_at = None
        self.margin_rows, self.funds = {}, None
        self.live_refresh_active = False

    def get_nfo_instruments(self):
        return self.manager.instruments

    def begin_live_refresh(self, symbols):
        self.live_refresh_active = True
        keys = ['NSE:'+symbol for symbol in symbols]
        keys += ['NFO:'+contract['tradingsymbol'] for contract in self.manager.contracts.values()]
        keys += ['NSE:'+index for index in set(SectorStrength.KITE_INDEX_SYMBOLS.values())|{'NIFTY 50'}]
        self.refresh_quotes(keys)

    def end_live_refresh(self):
        self.live_refresh_active = False

    def refresh_quotes(self, keys=None):
        keys = keys or list(self.quotes)
        try:
            self.quotes = self.manager.gateway.quotes(keys)
            self.quote_fetched_at = self.manager.clock()
        except Exception as exc:
            self.quotes = {}
            self.manager.errors['quotes'] = source_failure(exc)

    def get_data(self, symbol):
        identity = 'NSE:'+symbol+':day'
        if identity not in self.frames:
            self.frames[identity]=self.manager.cache.frame(identity, now=self.manager.now, allow_expired=True)
        frame = self.frames[identity]
        if frame is None or frame.empty:
            self.manager.errors.setdefault(identity, {'type':'CANDLES_MISSING', 'missing_fields':['Open','High','Low','Close','Volume'], 'cache_path':str(self.manager.cache.path)})
            return None
        if getattr(self.manager,'mode',None) == 'AFTER_MARKET_RESEARCH':
            return completed_frame(frame, self.manager.data_as_of, 'day')
        frame = frame.copy()
        quote = self.quotes.get('NSE:'+symbol)
        if not quote:
            return frame
        today = self.manager.clock().normalize()
        ohlc = quote.get('ohlc') or {}
        try:
            for column, key in [('Open','open'), ('High','high'), ('Low','low')]:
                frame.loc[today, column] = float(ohlc[key])
            frame.loc[today, 'Close'] = float(quote['last_price'])
            frame.loc[today, 'Volume'] = float(quote.get('volume',0))
            frame.loc[today, 'IS_LIVE_CANDLE'] = True
            elapsed = (today.replace(hour=self.manager.clock().hour, minute=self.manager.clock().minute)-today.replace(hour=9,minute=15)).total_seconds()/22500
            frame.loc[today, 'LIVE_SESSION_PROGRESS'] = min(1, max(1/375, elapsed))
        except (ValueError, KeyError, TypeError):
            return None
        return frame.sort_index()

    def get_annual_history(self, symbol):
        identity='NSE:'+symbol+':annual'
        if identity not in self.frames:
            self.frames[identity]=self.manager.cache.frame(identity,now=self.manager.now,allow_expired=True)
        longer = self.frames[identity]
        recent = self.get_data(symbol)
        if longer is None:
            return recent
        merged = pd.concat([longer,recent]) if recent is not None else longer
        return merged.loc[~merged.index.duplicated(keep='last')].sort_index()

    def get_session_intraday(self, symbol):
        identity = 'NSE:'+symbol+':5minute'
        if identity not in self.frames:
            self.frames[identity] = self.manager.update_candles(identity, live=True)
        return self.frames[identity]

    def preload_sessions(self, symbols):
        identities = ['NSE:'+symbol+':5minute' for symbol in symbols]
        values, errors = bounded_map(lambda key: self.manager.update_candles(key, live=True), identities,
            self.manager.runtime.workers, max(0, self.manager.deadline-monotonic()-15) if self.manager.deadline else None)
        self.frames.update(values)
        for key in errors:
            self.frames[key] = None
        self.manager.errors.update(errors)

    def get_futures_execution_data(self, symbol, contract):
        key = 'NFO:'+contract['tradingsymbol']
        if key not in self.execution:
            intraday = self.manager.update_candles(key+':5minute', live=True)
            daily = self.manager.update_candles(key+':day', live=True)
            master = self.manager.master.get(contract['tradingsymbol'], {})
            self.execution[key] = {'intraday': intraday, 'daily': daily, 'contract': contract,
                                   'instrument_token': master.get('instrument_token')}
        if self.quote_fetched_at is not None and (self.manager.clock()-self.quote_fetched_at).total_seconds() > 60:
            self.refresh_quotes()
        result = dict(self.execution[key])
        result.update(quote=self.quotes.get(key, {}), spot_price=self.quotes.get('NSE:'+symbol, {}).get('last_price'),
                      underlying_intraday=self.get_session_intraday(symbol),
                      spot_quote=self.quotes.get('NSE:'+symbol, {}), fetched_at=str(self.quote_fetched_at),
                      movement_basis=self.manager.runtime.movement_basis, require_margin=self.manager.runtime.require_margin,
                      margin_by_side={side: self.margin_rows.get((contract['tradingsymbol'], side)) for side in ('LONG','SHORT')},
                      available_capital=self.funds, margin_checked_at=str(self.margin_checked_at) if hasattr(self,'margin_checked_at') else None)
        return result

    def fetch_margins(self):
        orders = []
        for contract in self.manager.contracts.values():
            for side in ('BUY','SELL'):
                orders.append({'exchange':'NFO', 'tradingsymbol':contract['tradingsymbol'], 'transaction_type':side,
                    'variety':'regular','product':'MIS','order_type':'MARKET','quantity':contract['lot_size'],'price':0,'trigger_price':0})
        try:
            rows, funds = self.manager.gateway.margins(orders)
            # Response order is specified by the request order; never guess missing rows.
            if len(rows) != len(orders):
                raise ValueError('Incomplete margin response')
            for order, row in zip(orders, rows):
                if row.get('tradingsymbol') != order['tradingsymbol']:
                    raise ValueError('Wrong margin contract')
                self.margin_rows[(order['tradingsymbol'], 'LONG' if order['transaction_type']=='BUY' else 'SHORT')] = float(row['total'])
            available = funds.get('available', {})
            self.funds = float(available.get('live_balance', available.get('cash')))
            self.margin_checked_at = self.manager.clock()
        except Exception as exc:
            self.margin_rows, self.funds = {}, None
            self.manager.errors['margin'] = source_failure(exc)


class PreparedScanner:
    def __init__(self, scanner, runtime=None):
        self.scanner = scanner
        self.runtime = runtime or ScanRuntime.from_env()
        self.cache = PreparationCache(self.runtime.cache_directory)
        self.errors = {}
        self.deadline = None

    def backtest_policy(self):
        return fingerprint({'config': asdict(self.scanner.config), 'costs': asdict(self.scanner.costs),
                            'movement_basis': self.runtime.movement_basis, 'algorithm_version': CALCULATION_VERSION})

    def run(self, mode, limit, now=None):
        if mode not in MODES:
            raise ValidationError('mode must be one of '+', '.join(MODES))
        started = perf_counter()
        self.mode = mode
        self.now = pd.Timestamp(now or datetime.now(ZoneInfo('Asia/Kolkata')))
        self.now = self.now.tz_localize('Asia/Kolkata') if self.now.tz is None else self.now.tz_convert('Asia/Kolkata')
        self.data_as_of = completed_session_cutoff(self.now) if mode=='AFTER_MARKET_RESEARCH' else self.now
        self.clock = (lambda: self.now) if now is not None else (lambda: pd.Timestamp.now(tz='Asia/Kolkata'))
        if mode in ('LIVE_SCAN','AFTER_MARKET_RESEARCH'):
            self.deadline = monotonic()+(self.runtime.live_deadline_seconds if mode=='LIVE_SCAN' else self.runtime.after_market_deadline_seconds)
        self.gateway = KiteGateway(self.scanner.provider, self.runtime, self.deadline)
        try:
            self.instruments = self.scanner.provider.get_nfo_instruments()
            symbols = sorted(set(self.scanner.platform._universe_symbols())-INDEX_FUTURES)
            self.cache.put('universe','current',{'symbols':symbols,'instruments':self.instruments},now=self.now,ttl=86400*7)
        except Exception as exc:
            cached = self.cache.get('universe','current',now=self.now)
            if cached is None:
                raise DataUnavailableError('Universe unavailable and no prepared universe exists; run FULL_RESEARCH') from exc
            symbols, self.instruments = cached['payload']['symbols'], cached['payload']['instruments']
            self.errors['universe_verification'] = source_failure(exc)
        if not symbols:
            raise DataUnavailableError('Empty F&O universe')
        self.symbols = symbols
        self.master = {row['tradingsymbol']:row for row in self.instruments}
        self.contracts = {symbol: rows[0] for symbol in symbols if (rows:=active_contracts(symbol,self.instruments,self.now.date()))}
        self.aliases = stock_aliases(symbols)
        timings = {}
        step = perf_counter()
        recovery = recover_existing_data(self)
        timings['cache_recovery_seconds'] = perf_counter()-step
        if mode in ('FULL_RESEARCH','DAILY_PREP'):
            step = perf_counter()
            self.prepare_research(mode)
            timings['preparation_research_seconds'] = perf_counter()-step
            step = perf_counter()
            self.prepare_history(mode)
            timings['preparation_candles_backtests_seconds'] = perf_counter()-step
        if mode == 'AFTER_MARKET_RESEARCH':
            # Existing native candles are imported first. Download only misses.
            identities = ['NSE:'+s+':day' for s in sorted(set(symbols)|set(SectorStrength.KITE_INDEX_SYMBOLS.values())|{'NIFTY 50'})
                          if self.cache.frame('NSE:'+s+':day',now=self.now,allow_expired=True) is None]
            _, errors = bounded_map(self.update_candles, identities, self.runtime.workers, max(0,self.deadline-monotonic()-15))
            self.errors.update(errors)
            missing_companies = [s for s in symbols if self.cache.get('company',s,now=self.now) is None]
            def missing_company(symbol):
                facts = self.scanner.company_research.engine.fundamental_provider.get_fundamentals(symbol)
                if facts is not None:
                    from src.futures.data_recovery import record_company
                    record_company(self,asdict(facts),self.clock(),'existing company research provider')
            _, errors = bounded_map(missing_company,missing_companies,self.runtime.workers,self.runtime.live_news_budget_seconds)
            self.errors.update({'research:'+k:v for k,v in errors.items()})
        step = perf_counter()
        self.news = self.load_live_news(mode)
        timings['fresh_news_seconds'] = perf_counter()-step
        service = EventRiskService(self.scanner.platform.settings)
        context = service.build_daily_context(as_of=self.clock().to_pydatetime()) if self.scanner.event_provider is None else None
        proxy = PreparedDataProvider(self)
        step = perf_counter()
        proxy.preload_sessions(symbols)
        timings['incremental_session_updates_seconds'] = perf_counter()-step
        step = perf_counter()
        if self.runtime.require_margin:
            proxy.fetch_margins()
        timings['margin_validation_seconds'] = perf_counter()-step
        from src.futures.scanner import FuturesOpportunityScanner
        from src.futures.research import CompanyResearch
        runner = FuturesOpportunityScanner(self.scanner.platform, config=self.scanner.config, costs=self.scanner.costs,
            fundamental_provider=CachedCompanies(self), news_provider=lambda symbol:self.news.get(symbol, {'news_state':'FETCH_FAILED'}),
            event_provider=self.scanner.event_provider or (lambda symbol,sector,news,stamp:service.assess_candidate({'symbol':symbol,'sector':sector}, context,news_context=news,as_of=stamp.to_pydatetime()).to_dict()))
        runner.provider = proxy
        runner.runtime = self.runtime
        runner.account_gateway = self.gateway
        runner.defer_report_e = True
        if mode=='AFTER_MARKET_RESEARCH':
            runner.research_as_of = self.data_as_of
        runner.historical_provider = lambda symbol,contract:self.cached_backtest(contract)
        if self.errors.get('universe_verification'):
            proxy.manager.contracts = {}  # No unverified master may approve a listed contract.
            proxy.get_nfo_instruments = lambda: []
        step = perf_counter()
        report = runner.scan(limit, now=now, include_backtest=False)
        timings['scanner_seconds'] = perf_counter()-step
        timings.update({'scanner_'+key:value for key,value in report['timings'].items()})
        timings['total_seconds'] = perf_counter()-started
        report.update(scan_mode=mode, timings=timings, request_counts=self.gateway.counts, source_failures=self.errors,
                      prepared_for_session=next_session(self.now.normalize()) if mode in ('FULL_RESEARCH','DAILY_PREP') else self.now.date().isoformat(),
                      cache_recovery=recovery, research_only=mode=='AFTER_MARKET_RESEARCH',
                      completed_session_as_of=self.data_as_of.isoformat() if mode=='AFTER_MARKET_RESEARCH' else None,
                      cache_directory=str(self.cache.path), execution_time_target_seconds=[30,120],
                      performance_target_met=timings['total_seconds']<=120 if mode=='LIVE_SCAN' else None)
        for item in report['reviewed']:
            record=self.cache.get('company',item['symbol'],now=self.clock())
            market_record=self.cache.get('market_fields',item['symbol'],now=self.clock())
            if item['final_decision']=='APPROVED' and (record is None or market_record is None):
                item['final_decision']='UNKNOWN'
                item['reason_codes'].append('COMPANY_CACHE_EXPIRED_BEFORE_REPORT')
            candles = proxy.frames.get('NSE:'+item['symbol']+':day')
            intraday = proxy.frames.get('NSE:'+item['symbol']+':5minute')
            provenance = self.cache.get('provenance','company:'+item['symbol'],now=self.now,allow_expired=True)
            contract_name = item.get('contract',{}).get('tradingsymbol')
            future_bars = proxy.execution.get('NFO:'+contract_name,{}).get('intraday') if contract_name else None
            missing_bars=[]
            if mode=='AFTER_MARKET_RESEARCH' and intraday is not None and not intraday.empty:
                expected=pd.date_range(self.data_as_of.normalize()+pd.Timedelta(hours=9,minutes=15),self.data_as_of-pd.Timedelta(minutes=5),freq='5min')
                missing_bars=[stamp.isoformat() for stamp in expected.difference(intraday.index)]
                if missing_bars:
                    self.errors['NSE:'+item['symbol']+':session_completeness']={'type':'COMPLETED_SESSION_BARS_MISSING', 'missing_candle_timestamps':missing_bars, 'last_available_candle':intraday.index[-1].isoformat()}
            item['source_failures']={k:v for k,v in self.errors.items() if item['symbol'] in k or k in ('quotes','margin','universe_verification')}
            item['missing_fields']={name:check.get('reason_codes',[]) for name,check in item.get('company_research',{}).get('checks',{}).items() if check.get('status')=='UNKNOWN'}
            item['data_freshness']={'daily_candle_at':candles.index[-1].isoformat() if candles is not None and not candles.empty else None,
                'session_candle_at':intraday.index[-1].isoformat() if intraday is not None and not intraday.empty else None,
                'futures_candle_at':future_bars.index[-1].isoformat() if future_bars is not None and not future_bars.empty else None,
                'historical_candles_available':candles is not None and not candles.empty,
                'missing_completed_session_candles':missing_bars,
                'research_source':provenance['payload'] if provenance else None,
                'completed_session_as_of':self.data_as_of.isoformat() if mode=='AFTER_MARKET_RESEARCH' else None,
                'company_saved_at':record['saved_at'] if record else None,
                'market_fields_saved_at':market_record['saved_at'] if market_record else None,
                'company_expires_at':record['expires_at'] if record else None,'quote_timestamp':item.get('futures_quote',{}).get('timestamp'),
                'news_checked_at':item.get('news',{}).get('checked_at'), 'history_basis':self.runtime.movement_basis}
            hist=item.get('historical',{})
            item['probability_estimate']={'kind':'Historical target-first frequency, not a forecast',
                'percent':hist.get('target_first_percent'),'sample_size':hist.get('historical_signals',0),
                'net_expectancy':hist.get('average_net_pnl'),'guaranteed':False}
        if mode == 'AFTER_MARKET_RESEARCH':
            for item in report['reviewed']:
                item['research_only'] = True
                evidence=(item.get('futures_setup') or {}).get('evidence') or item.get('evidence',{})
                item['research_entry_reference']={'price':evidence.get('price'),'candle_at':evidence.get('signal_timestamp'),
                    'basis':item.get('technical_score_timeframe','DAILY_DISCOVERY'), 'live_entry_verified':False}
                item['reason_codes'].append('AFTER_MARKET_RESEARCH_NO_LIVE_APPROVAL')
                if item['final_decision']=='APPROVED':
                    item['final_decision']='WAIT'
                if item.get('short_entry'):
                    item['short_entry']['execution_approved']=False
            report['report_c']=[]
            # Research ranks valid completed-session evidence using unchanged scores.
            for side,key in (('LONG','report_a'),('SHORT','report_b')):
                report[key]=sorted((i for i in report['reviewed'] if i['side']==side and i.get('technical_score') is not None),key=lambda i:i['technical_score'],reverse=True)[:limit]
        report['report_c']=[item for item in report['report_c'] if item['final_decision']=='APPROVED']
        report['approved_count']=sum(item['final_decision']=='APPROVED' for item in report['reviewed'])
        self.scanner._report_e_inputs=getattr(runner,'_report_e_inputs',{})
        self.scanner._report_e_history={}
        # Read-only evidence cache. LIVE_SCAN never calls a Report E backtester.
        from src.futures.report_e_config import ReportEConfig
        from src.futures.report_e_history import policy_key, inputs_match
        try:
            e_config=ReportEConfig.from_env()
            for contract in self.contracts.values():
                record=self.cache.get('report_e_history',contract['tradingsymbol'],now=self.clock())
                if record and record['payload'].get('policy_key')==policy_key(self.scanner.config,self.scanner.costs,e_config,contract):
                    prefix='NFO:'+contract['tradingsymbol']
                    frames={'candles':self.cache.frame(prefix+':5minute',now=self.now,allow_expired=True),
                            'daily':self.cache.frame(prefix+':day',now=self.now,allow_expired=True),
                            'benchmark':self.cache.frame('NSE:NIFTY 50:day',now=self.now,allow_expired=True)}
                    if inputs_match(record['payload'],frames):
                        self.scanner._report_e_history[contract['tradingsymbol']]={**record['payload'],'saved_at':record['saved_at'],'expires_at':record['expires_at']}
        except (ValueError,TypeError,KeyError):
            pass
        finalize(report, runner._final_frames, runner._reconciliation, self.clock(), self.scanner.config, self.runtime.reconciliation_max_age_seconds)
        return report

    def update_candles(self, identity, live=False):
        exchange, symbol, interval=identity.split(':')
        cached=self.cache.frame(identity,now=self.now,allow_expired=True)
        record=self.cache.get('candles',identity,now=self.now,allow_expired=True)
        if live and cached is None and getattr(self,'mode','LIVE_SCAN')=='LIVE_SCAN':
            underlying = symbol if exchange=='NSE' else self.master.get(symbol,{}).get('name')
            if not underlying or self.cache.frame('NSE:'+underlying+':day',now=self.now,allow_expired=True) is None:
                self.errors[identity]={'type':'PREPARED_CANDLES_MISSING','missing_fields':['Open','High','Low','Close','Volume'], 'cache_path':str(self.cache.path), 'recovery':'No base history available; run DAILY_PREP or AFTER_MARKET_RESEARCH.'}
                return None
        stamp = self.data_as_of if getattr(self,'mode',None)=='AFTER_MARKET_RESEARCH' else self.clock()
        expected=min(stamp.floor('5min')-pd.Timedelta(minutes=5), stamp.normalize()+pd.Timedelta(hours=15,minutes=25))
        if getattr(self,'mode',None)=='AFTER_MARKET_RESEARCH' and cached is not None and not cached.empty:
            complete = cached.index[-1].normalize() >= stamp.normalize() if interval=='day' else cached.index[-1]>=expected
            if complete:
                return completed_frame(cached,stamp,interval)
        if record and (self.clock()-pd.Timestamp(record['saved_at'])).total_seconds()<self.runtime.candle_refresh_seconds:
            stamp=self.clock()
            expected=min(stamp.floor('5min')-pd.Timedelta(minutes=5),stamp.normalize()+pd.Timedelta(hours=15,minutes=25))
            if interval!='5minute' or (stamp.hour,stamp.minute)<(9,20) or (cached is not None and not cached.empty and cached.index[-1]>=expected):
                return completed_frame(cached, self.data_as_of if getattr(self,'mode',None)=='AFTER_MARKET_RESEARCH' else self.clock(), interval) if cached is not None and not cached.empty else cached
        if self.deadline and monotonic()+self.runtime.request_timeout_seconds>self.deadline:
            self.errors[identity]='LIVE_REQUEST_DEADLINE'
            return completed_frame(cached, self.data_as_of if getattr(self,'mode',None)=='AFTER_MARKET_RESEARCH' else self.clock(), interval) if cached is not None and not cached.empty else cached
        try:
            token=self.master[symbol]['instrument_token'] if exchange=='NFO' else self.scanner.provider.provider._instrument_token(symbol)
            lookback=730 if interval=='day' and exchange=='NSE' else 120 if interval=='day' else 45
            # Re-fetch the last completed interval too: exchanges can revise it.
            start=max(cached.index[-1]-pd.Timedelta(days=1),self.now-pd.Timedelta(days=lookback)) if cached is not None and not cached.empty else self.now-pd.Timedelta(days=lookback)
            if getattr(self,'mode',None)=='AFTER_MARKET_RESEARCH' and interval=='5minute' and cached is not None and not cached.empty and cached.index[-1].date()==self.data_as_of.date():
                start=cached.index[-1]-pd.Timedelta(minutes=5)
            history_end=self.data_as_of if getattr(self,'mode',None)=='AFTER_MARKET_RESEARCH' else self.clock()
            rows=self.gateway.history(token,start.to_pydatetime(),history_end.to_pydatetime(),interval,oi=exchange=='NFO')
            if not rows:
                raise ValueError('No incremental candles supplied')
            fresh=pd.DataFrame(rows).set_index('date').rename(columns={'open':'Open','high':'High','low':'Low','close':'Close','volume':'Volume','oi':'OI'})
            index=pd.DatetimeIndex(pd.to_datetime(fresh.index))
            fresh.index=index.tz_localize('Asia/Kolkata') if index.tz is None else index.tz_convert('Asia/Kolkata')
            if interval=='5minute':
                fresh=fresh.loc[fresh.index+pd.Timedelta(minutes=5)<=history_end]
            elif (history_end.hour,history_end.minute)<(15,30):
                fresh=fresh.loc[fresh.index.date<history_end.date()]
            merged=pd.concat([cached,fresh]) if cached is not None else fresh
            merged=merged.loc[~merged.index.duplicated(keep='last')].sort_index()
            merged=merged.loc[merged.index>=self.now-pd.Timedelta(days=lookback)]
            self.cache.put_frame(identity,merged,now=self.clock(),ttl=self.runtime.history_ttl_seconds)
            return completed_frame(merged, history_end, interval)
        except Exception as exc:
            self.errors[identity]=source_failure(exc)
            # Never relabel old candles as refreshed; downstream validators see their original dates.
            return completed_frame(cached, self.data_as_of if getattr(self,'mode',None)=='AFTER_MARKET_RESEARCH' else self.clock(), interval) if cached is not None and not cached.empty else cached

    def prepare_research(self, mode):
        provider=self.scanner.company_research.engine.fundamental_provider
        if isinstance(provider,PublicFundamentalProvider):
            provider.timeout=self.runtime.request_timeout_seconds
            provider.today=self.now.date()
            provider.include_current_session=(self.now.hour,self.now.minute)>=(15,30)
            provider._archives=None
        def prepare_one(symbol):
            old=self.cache.get('company',symbol,now=self.now)
            prior_news=self.cache.get('news',symbol,now=self.clock(),allow_expired=True)
            old_news=self.cache.get('news',symbol,now=self.clock())
            if old_news and old_news['payload'].get('news_state') in ('ANALYZED','NO_RELEVANT_NEWS'):
                news=old_news['payload']
            else:
                options={}
                if old_news and old_news['payload'].get('news_state')=='UNANALYSED_NEW_HEADLINES':
                    options['collected_articles']=old_news['payload'].get('headlines',[])
                news=NewsAnalysisService.analyze(symbol,timeout=self.runtime.request_timeout_seconds,limit=16,
                    force_refresh=True,company_aliases=self.aliases,**options)
            self.cache.put('news',symbol,news,now=self.clock(),ttl=self.runtime.news_ttl_seconds)
            if news.get('news_state') in ('ANALYZED','NO_RELEVANT_NEWS'):
                self.cache.invalidate('pending_news',symbol)
            # Material updates invalidate static company data; expiry never falls back silently.
            changed=prior_news is None or prior_news['payload'].get('articles_fingerprint')!=news.get('articles_fingerprint')
            material=changed and (bool(news.get('events')) or news.get('trade_impact')=='BLOCK')
            if old is None or material:
                if hasattr(provider,'invalidate_snapshot'):
                    provider.invalidate_snapshot(symbol)
                facts=self.scanner.company_research.engine.fundamental_provider.get_fundamentals(symbol)
            else:
                facts=FundamentalSnapshot(**old['payload'])
                if mode=='DAILY_PREP' and hasattr(provider,'_delivery'):
                    update=provider._delivery(symbol)
                    if update:
                        facts=replace(facts,**update[0],evidence={**facts.evidence,**update[1]})
            if facts is not None:
                market_fields=('pe_ratio','sector_pe','delivery_percent','monthly_delivery_percent')
                values={field:getattr(facts,field) for field in market_fields}
                evidence={field:source for field,source in facts.evidence.items() if field in market_fields}
                if mode=='DAILY_PREP' and isinstance(provider,PublicFundamentalProvider) and old is not None and not material:
                    # Only price-sensitive fields are re-requested; quarterly filings remain cached.
                    url=facts.evidence.get('sector_pe',{}).get('url')
                    if url and url.startswith('https://www.moneycontrol.com/'):
                        try:
                            parsed,_=provider.parse_moneycontrol(provider._fetch(url),today=self.now.date())
                            for field in ('pe_ratio','sector_pe'):
                                values[field]=parsed.get(field)
                                evidence[field]={'url':url,'period':self.now.date().isoformat(),'basis':'Daily published TTM valuation'}
                        except Exception:
                            values['pe_ratio']=values['sector_pe']=None
                    else:
                        values['pe_ratio']=values['sector_pe']=None
                expires=pd.Timestamp(next_session(self.now.normalize()),tz='Asia/Kolkata')+pd.Timedelta(hours=16)
                self.cache.put('market_fields',symbol,{'values':values,'evidence':evidence},now=self.clock(),ttl=max(1,(expires-self.clock()).total_seconds()))
                # Updating market fields must not extend the financial-filing expiry.
                saved_at=self.clock() if old is None or material else pd.Timestamp(old['saved_at'])
                self.cache.put('company',symbol,asdict(facts),now=saved_at,ttl=self.runtime.research_ttl_seconds)
        _, errors=bounded_map(prepare_one,self.symbols,self.runtime.workers)
        self.errors.update({'research:'+key:value for key,value in errors.items()})

    def prepare_history(self, mode):
        indices=set(SectorStrength.KITE_INDEX_SYMBOLS.values())|{'NIFTY 50'}
        identities=['NSE:'+symbol+':day' for symbol in sorted(set(self.symbols)|indices)]
        identities += ['NSE:'+symbol+':5minute' for symbol in self.symbols]
        for contract in self.contracts.values():
            identities += ['NFO:'+contract['tradingsymbol']+':day','NFO:'+contract['tradingsymbol']+':5minute']
        _,errors=bounded_map(self.update_candles,identities,self.runtime.workers)
        self.errors.update(errors)
        def backtest_one(symbol):
            contract=self.contracts[symbol]
            prefix='NFO:'+contract['tradingsymbol']
            candles=self.cache.frame(prefix+':5minute',now=self.now)
            daily=self.cache.frame(prefix+':day',now=self.now)
            underlying=self.cache.frame('NSE:'+symbol+':5minute',now=self.now)
            benchmark=self.cache.frame('NSE:NIFTY 50:day',now=self.now)
            identity=self.backtest_policy()
            inputs=fingerprint([identity,candle_fingerprint(candles),candle_fingerprint(daily),candle_fingerprint(underlying),candle_fingerprint(benchmark),contract])
            existing=self.cache.get('backtest',contract['tradingsymbol'],now=self.now,expected_fingerprint=inputs)
            if existing:
                self.prepare_report_e_history(contract,candles,daily,benchmark)
                return
            if candles is None or candles.empty:
                self.errors[prefix]='BACKTEST_DATA_MISSING'
                return
            result=FuturesIntradayBacktester(self.scanner.config,self.scanner.costs).run(candles,contract['lot_size'],
                daily_history=daily,benchmark_history=benchmark)
            result['policy_fingerprint']=identity
            self.cache.put('backtest',contract['tradingsymbol'],result,now=self.clock(),ttl=self.runtime.history_ttl_seconds,input_fingerprint=inputs)
            self.prepare_report_e_history(contract,candles,daily,benchmark)
        _,errors=bounded_map(backtest_one,list(self.contracts),min(2,self.runtime.workers))
        self.errors.update({'backtest:'+key:value for key,value in errors.items()})

    def prepare_report_e_history(self,contract,candles,daily,benchmark):
        """Separate cached evidence work in preparation only, never in live/reporting."""
        from src.futures.report_e_config import ReportEConfig
        from src.futures.report_e_history import ReportEBacktester, policy_key, build_history_evidence, input_windows
        try:
            e_config=ReportEConfig.from_env()
            if not e_config.prepare_history or candles is None or candles.empty:
                return
            policy=policy_key(self.scanner.config,self.scanner.costs,e_config,contract)
            inputs=fingerprint([policy,candle_fingerprint(candles),candle_fingerprint(daily),candle_fingerprint(benchmark)])
            existing=self.cache.get('report_e_history',contract['tradingsymbol'],now=self.clock(),expected_fingerprint=inputs)
            if existing:
                return
            result=ReportEBacktester(self.scanner.config,self.scanner.costs).run(candles,contract['lot_size'],
                daily_history=daily,benchmark_history=benchmark)
            result['report_e_timing_validated']=True
            result.setdefault('report_e_entry_definition','Existing prefix READY plus reconstructible Report E RSI/EMA/MACD/ADX/VWAP/ATR/matched-volume and available historical OI; next-bar execution')
            evidence=build_history_evidence(result,self.scanner.config,e_config,instrument=contract['tradingsymbol'],
                period={'start':candles.index[0].isoformat(),'end':candles.index[-1].isoformat()})
            evidence['policy_key']=policy
            frames={'candles':candles,'daily':daily,'benchmark':benchmark}
            evidence['input_windows']=input_windows(frames)
            evidence['input_fingerprints']={name:candle_fingerprint(frame) for name,frame in frames.items() if frame is not None and not frame.empty}
            self.cache.put('report_e_history',contract['tradingsymbol'],evidence,now=self.clock(),ttl=self.runtime.history_ttl_seconds,input_fingerprint=inputs)
        except Exception as exc:
            # Evidence errors are isolated in Report E; legacy preparation and
            # source_failures/timings/results retain their existing semantics.
            if not hasattr(self,'report_e_history_errors'):
                self.report_e_history_errors={}
            self.report_e_history_errors[contract['tradingsymbol']]=source_failure(exc)

    def cached_backtest(self, contract):
        record=self.cache.get('backtest',contract['tradingsymbol'],now=self.now)
        if (record and record['payload'].get('policy_fingerprint')==self.backtest_policy()
                and record['payload'].get('movement_basis')==self.runtime.movement_basis):
            return {**record['payload'], 'cached_at':record['saved_at'], 'expires_at':record['expires_at']}
        return {'status':'UNKNOWN','reason':'Prepared backtest missing, expired, different expiry, or different strategy/cost assumptions'}

    def load_live_news(self, mode):
        cached={symbol:self.cache.get('news',symbol,now=self.now) for symbol in self.symbols}
        output={symbol:record['payload'] for symbol,record in cached.items() if record}
        missing=[symbol for symbol in self.symbols if symbol not in output]
        if mode not in ('LIVE_SCAN','AFTER_MARKET_RESEARCH'):
            for symbol in missing:
                output[symbol]={'news_state':'FETCH_FAILED'}
            return output
        def refresh(symbol):
            articles=collect(symbol,self.aliases,self.clock().to_pydatetime(),self.runtime.request_timeout_seconds)
            old=self.cache.get('news',symbol,now=self.now,allow_expired=True)
            return fresh_result(articles,old['payload'] if old else None,self.clock().to_pydatetime())
        budget=self.runtime.after_market_news_budget_seconds if mode=='AFTER_MARKET_RESEARCH' else self.runtime.live_news_budget_seconds
        values,errors=bounded_map(refresh,missing,self.runtime.workers,budget)
        for symbol,value in values.items():
            output[symbol]=value
            self.cache.put('news',symbol,value,now=self.clock(),ttl=self.runtime.news_ttl_seconds)
            if value.get('news_state')=='UNANALYSED_NEW_HEADLINES':
                self.cache.put('pending_news',symbol,value,now=self.clock(),ttl=86400)
        for symbol,error in errors.items():
            old=self.cache.get('news',symbol,now=self.now,allow_expired=True)
            output[symbol]={'news_state':'FETCH_FAILED','reason':error,'checked_at':self.clock().isoformat(),
                            'last_successful_analysis':old['payload'] if old else None,
                            'headlines':old['payload'].get('headlines',[]) if old else [],
                            'article_assessments':old['payload'].get('article_assessments',[]) if old else [],
                            'cached_analysis_checked_at':old['payload'].get('checked_at') if old else None}
        self.errors.update({'news:'+key:value for key,value in errors.items()})
        if mode=='AFTER_MARKET_RESEARCH':
            changed=[symbol for symbol,value in output.items() if value.get('news_state')=='UNANALYSED_NEW_HEADLINES']
            def analyse_changed(symbol):
                result=NewsAnalysisService.analyze(symbol,timeout=self.runtime.request_timeout_seconds,limit=16,
                    force_refresh=True,company_aliases=self.aliases,collected_articles=output[symbol].get('headlines',[]))
                if result.get('news_state') not in ('ANALYZED','NO_RELEVANT_NEWS'):
                    result['last_successful_analysis']=output[symbol].get('last_successful_analysis')
                    self.errors['news_analysis:'+symbol]={'type':'NEWS_ANALYSIS_UNAVAILABLE','detail':result.get('reasons',[])}
                self.cache.put('news',symbol,result,now=self.clock(),ttl=self.runtime.news_ttl_seconds)
                if result.get('news_state') in ('ANALYZED','NO_RELEVANT_NEWS'):
                    self.cache.invalidate('pending_news',symbol)
                return result
            analysed,analysis_errors=bounded_map(analyse_changed,changed,min(2,self.runtime.workers),self.runtime.after_market_analysis_budget_seconds)
            output.update(analysed)
            self.errors.update({'news_analysis:'+key:value for key,value in analysis_errors.items()})
        return output
