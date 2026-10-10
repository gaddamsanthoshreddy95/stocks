"""Reuse existing Kite, published fundamentals, news and event integrations."""
from dataclasses import asdict
import json
import os
from threading import RLock, Lock
from pathlib import Path
import pandas as pd
from src.futures.sessions import ist, normalise_candles
from src.futures_workspace.discovery import fundamental_context, universe
from src.futures_workspace.bias import MarketBias
from src.futures_workspace.liquidity import completed_day, historical_volume
from src.futures.gateway import KiteGateway
from src.futures.runtime import ScanRuntime
from src.sector.sector_mapper import SectorMapper
from src.sector.sector_strength import SectorStrength

class RepositoryAdapter:
    def __init__(self,platform,config=None):
        from src.futures_workspace.config import WorkspaceConfig
        self.config=config or WorkspaceConfig.from_env()
        self.platform=platform
        self.provider=platform.provider
        self.mapper=SectorMapper()
        self.gateway=None
        self._fundamentals=None
        self._events=None
        self._event_context=None
        self.history_cache={}
        self._initialization_lock=RLock()
        self._history_locks={}
        self._provider_lock=RLock()
        self._adjusted_pacing=Lock()
        self._event_assessment_lock=Lock()

    def begin(self,now):
        self.history_cache={}
        self._event_context=None
        self.now=ist(now)
        begin=getattr(self.provider,'begin_live_refresh',None)
        self.started=bool(begin and not getattr(self.provider,'live_refresh_active',False))
        if self.started:
            begin([])

    def end(self):
        if getattr(self,'started',False):
            self.provider.end_live_refresh()

    def instruments(self):
        if self.platform.settings.market_data_source!='kite':
            raise RuntimeError('Full Futures instrument discovery requires Kite credentials')
        if self.gateway is None:
            self.gateway=KiteGateway(self.provider,ScanRuntime.from_env())
        master=self.gateway.request('quote',lambda:self.gateway.kite.instruments('NFO'))
        if not isinstance(master,list):
            raise ValueError('Invalid complete instrument master')
        equities=self.gateway.request('quote',lambda:self.gateway.kite.instruments('NSE'))
        if not isinstance(equities,list):
            raise ValueError('NSE underlying equity master unavailable')
        stocks={str(r.get('tradingsymbol','')).upper():r for r in equities if r.get('exchange')=='NSE' and r.get('segment')=='NSE' and r.get('instrument_type')=='EQ' and r.get('instrument_token')}
        return [{**r,'underlying_instrument_token':stocks[str(r.get('name','')).upper()]['instrument_token'],
                 'eligibility_source':'FRESH_KITE_NFO_AND_NSE_EQUITY_MASTERS'}
                for r in master if str(r.get('name','')).upper() in stocks]

    def quotes(self,contracts):
        if self.gateway is None:
            self.gateway=KiteGateway(self.provider,ScanRuntime.from_env())
        return self.gateway.quotes(['NFO:'+c['tradingsymbol'] for c in contracts.values()])

    def weekly_liquidity(self,contract,now):
        """Exact-contract completed volume, cached per completed session."""
        import hashlib
        day=completed_day(now)
        folder=Path('.cache/futures_workspace_liquidity')
        folder.mkdir(parents=True,exist_ok=True)
        key=hashlib.sha256(f"{contract['tradingsymbol']}:{contract['instrument_token']}:{day.date()}".encode()).hexdigest()
        path=folder/(key+'.json')
        if path.exists():
            try:
                cached=json.loads(path.read_text())
                if cached.get('as_of') and ist(cached['as_of']).normalize()==day:
                    return {**cached,'cache_reused':True,'status':'PASS' if cached['average_volume']>=self.config.minimum_futures_volume else 'FAIL'}
            except (ValueError,OSError):
                pass
        if self.gateway is None:
            self.gateway=KiteGateway(self.provider,ScanRuntime.from_env())
        rows=self.gateway.history(contract['instrument_token'],day-pd.Timedelta(days=20),day+pd.Timedelta(hours=15,minutes=30),'day')
        frame=pd.DataFrame(rows)
        if frame.empty:
            return historical_volume(frame,self.config,now)
        frame=frame.set_index('date').rename(columns={'volume':'Volume'})
        result=historical_volume(frame,self.config,now)
        result['source']='KITE_EXACT_CONTRACT_COMPLETED_DAILY'
        if result['status']!='UNKNOWN':
            temporary=path.with_suffix('.tmp')
            temporary.write_text(json.dumps(result))
            temporary.replace(path)
        return result

    def history(self,symbol):
        with self._initialization_lock:
            lock=self._history_locks.setdefault(symbol,RLock())
        with lock:
            return self._history(symbol)

    def _history(self,symbol):
        if symbol not in self.history_cache:
            if symbol=='NIFTY 50' and self.config.weekly_history_source=='yahoo_adjusted':
                # Match the equity research window; annual broker caches may only
                # cover one year and cannot establish a two-year holiday calendar.
                try:
                    frame=self.adjusted_history('^NSEI')
                except Exception:
                    getter=getattr(self.provider,'get_long_history',None)
                    if getter is None:
                        raise
                    with self._provider_lock:
                        frame=getter(symbol,period='2y')
            elif self.config.weekly_history_source=='yahoo_adjusted' and symbol not in set(SectorStrength.KITE_INDEX_SYMBOLS.values()):
                frame=self.adjusted_history(symbol)
            else:
                getter=getattr(self.provider,'get_annual_history',self.provider.get_data)
                with self._provider_lock:
                    frame=getter(symbol)
            if frame is not None:
                frame=frame.copy()
                # Optional verified, per-security adjustment evidence, never a blanket assertion.
                path=os.getenv('FUTURES_WORKSPACE_ADJUSTMENT_MANIFEST')
                if path:
                    manifest=json.loads(Path(path).read_text())
                    evidence=manifest.get(symbol,{})
                    try:
                        valid=(evidence.get('source') and evidence.get('basis') in ('ADJUSTED','CORPORATE_ACTION_VALIDATED')
                            and ist(evidence['validated_at'])<=self.now
                            and (self.now-ist(evidence['validated_at'])).total_seconds()<=7*86400
                            and ist(evidence['from'])<=ist(frame.index.min())
                            and ist(evidence['through']).date()>=ist(frame.index.max()).date())
                    except (KeyError,TypeError,ValueError):
                        valid=False
                    if valid:
                        frame.attrs['price_adjustment']=evidence['basis']
                        frame.attrs['adjustment_evidence']=evidence
            self.history_cache[symbol]=frame
        return self.history_cache[symbol]

    def adjusted_history(self,symbol):
        """Existing Yahoo auto-adjust provider; never splice raw Kite candles into it."""
        import time
        import hashlib
        from src.providers.yahoo_provider import YahooProvider
        folder=Path('.cache/futures_workspace_adjusted')
        folder.mkdir(parents=True,exist_ok=True)
        key=hashlib.sha256(symbol.encode()).hexdigest()
        path=folder/(key+'.parquet')
        if path.exists():
            try:
                cached=pd.read_parquet(path)
                normalized=normalise_candles(cached,'day',self.now)
                if not normalized.empty and normalized.index[-1].normalize()==completed_day(self.now):
                    return cached
            except (OSError,ValueError):
                pass
        with self._adjusted_pacing:
            delay=max(0,.35-(time.monotonic()-getattr(self,'last_adjusted_request',0)))
            if delay:
                time.sleep(delay)
            self.last_adjusted_request=time.monotonic()
        frame=YahooProvider().get_historical_data(symbol,period='2y',interval='1d')
        if frame is None or frame.empty:
            raise ValueError('Adjusted equity history unavailable')
        frame.attrs['price_adjustment']='ADJUSTED'
        frame.attrs['adjustment_evidence']={'source':'EXISTING_YAHOO_PROVIDER_AUTO_ADJUST_TRUE','retrieved_at':self.now.isoformat(),
            'publication_time_adjustments':'UNKNOWN_NOT_POINT_IN_TIME_BACKTEST_EVIDENCE'}
        frame.to_parquet(path)
        return frame

    def sector(self,symbol):
        return self.mapper.get_sector(symbol)

    def sector_history(self,symbol):
        key=SectorStrength.KITE_INDEX_SYMBOLS.get(self.sector(symbol))
        return self.history(key) if key else None

    def fundamentals(self,symbol,now):
        with self._initialization_lock:
            if self._fundamentals is None:
                from src.quality.public_fundamentals import PublicFundamentalProvider
                self._fundamentals=PublicFundamentalProvider()
        return fundamental_context(self._fundamentals.get_fundamentals(symbol),self.sector(symbol),now)

    def news(self,symbol,now):
        from src.news.analysis_service import NewsAnalysisService
        from src.event_risk.service import EventRiskService
        with self._initialization_lock:
            if self._events is None:
                self._events=EventRiskService(self.platform.settings)
            if self._event_context is None:
                self._event_context=self._events.build_daily_context(as_of=ist(now).to_pydatetime())
        raw=NewsAnalysisService.analyze(symbol,force_refresh=False,limit=16)
        if raw.get('fetch_failed') and raw.get('collection_state')!='FETCHED':
            from requests.exceptions import ConnectionError
            raise ConnectionError('Weekly news fetch failed: '+'; '.join(raw.get('reasons',[])))
        with self._event_assessment_lock:
            event=self._events.assess_candidate({'symbol':symbol,'sector':self.sector(symbol)},self._event_context,
                news_context=raw,as_of=ist(now).to_pydatetime()).to_dict()
        # Model headline sentiment is context, not a verified corporate disclosure.
        return {'status':'CONTEXT_ONLY','bullish_score':None,'bearish_score':None,'source':'EXISTING_NEWS_AND_EVENT_SERVICES',
                'retrieved_at':ist(now).isoformat(),'news':raw,'events':event,
                'verified_directional_disclosure':'UNKNOWN'}

    def market_bias(self,now):
        inputs={}; cache={}
        def read(symbol):
            if symbol not in cache:
                raw=self.provider.get_session_intraday(symbol)
                bars=normalise_candles(raw,'5minute',now)
                bars=bars.loc[bars.index.date==ist(now).date()]
                if len(bars)<2 or not 0<=(ist(now)-bars.index[-1]).total_seconds()<=600:
                    raise ValueError('Current market context unavailable')
                move=float((bars.Close.iloc[-1]/bars.Open.iloc[0]-1)*100)
                cache[symbol]=(bars,move)
            return cache[symbol]
        for key,symbol in (('nifty_trend','NIFTY 50'),('bank_trend','NIFTY BANK'),('midcap_trend','NIFTY MIDCAP 100')):
            try:
                bars,move=read(symbol)
                evidence={'status':'PASS','value':max(-1,min(1,move)),'source':'KITE_COMPLETED_INTRADAY','as_of':bars.index[-1].isoformat()}
                inputs[key]=evidence
                if key=='nifty_trend' and bars.Volume.sum()>0:
                    vwap=float((bars[['High','Low','Close']].mean(axis=1)*bars.Volume).sum()/bars.Volume.sum())
                    inputs['nifty_vwap']={**evidence,'value':max(-1,min(1,(float(bars.Close.iloc[-1])/vwap-1)*100))}
            except Exception:
                continue
        sectors=[]
        for symbol in sorted(set(SectorStrength.KITE_INDEX_SYMBOLS.values())):
            try:
                bars,move=read(symbol)
                sectors.append({'symbol':symbol,'return_percent':move,'as_of':bars.index[-1].isoformat()})
            except Exception:
                continue
        if len(sectors)>=3:
            inputs['sector_participation']={'status':'PASS','value':sum(1 if s['return_percent']>0 else -1 if s['return_percent']<0 else 0 for s in sectors)/len(sectors),
                'source':'KITE_SECTOR_INDICES','as_of':min(s['as_of'] for s in sectors),'sectors':sectors}
        # Market breadth is not inferred from selected watchlists or sector index counts.
        return MarketBias.evaluate(inputs,directional_threshold=self.config.bias_directional_threshold,
                                   strong_threshold=self.config.bias_strong_threshold)

class SelectedScanProvider:
    """Per-job shared reads; fresh execution quotes remain subject to scanner gates."""
    def __init__(self,provider,symbols=None):
        self._wrapped=provider
        self.symbols=sorted(set(symbols or []))
        self.execution_prefetched=False
        self.cache={}
        self._gateway=None

    def __getattr__(self,name):
        return getattr(self._wrapped,name)

    def cached(self,name,*args):
        key=(name,*args)
        if key not in self.cache:
            self.cache[key]=getattr(self._wrapped,name)(*args)
        value=self.cache[key]
        return value.copy() if hasattr(value,'copy') else value

    def get_data(self,symbol):
        return self.cached('get_data',symbol)

    def get_annual_history(self,symbol):
        return self.cached('get_annual_history',symbol)

    def get_session_intraday(self,symbol):
        # TTL prevents a shared candle snapshot from aging silently during a long job.
        import time
        key=('session',symbol)
        value=self.cache.get(key)
        if value is None or time.monotonic()-value[0]>30:
            value=(time.monotonic(),self._wrapped.get_session_intraday(symbol))
            self.cache[key]=value
        return value[1].copy()

    def get_nfo_instruments(self):
        return self.cached('get_nfo_instruments')

    def _execution_history(self,symbol,contract):
        key=('execution',symbol,contract['tradingsymbol'])
        if key in self.cache:
            return
        if self._gateway is None:
            self._gateway=KiteGateway(self._wrapped,ScanRuntime.from_env())
        master=next((r for r in self.get_nfo_instruments() if r.get('tradingsymbol')==contract['tradingsymbol'] and r.get('segment')=='NFO-FUT'),None)
        if master is None:
            return
        now=pd.Timestamp.now(tz='Asia/Kolkata')
        output={'contract':contract,'instrument_token':master['instrument_token'],'fetch_errors':{}}
        for label,interval,days in (('daily','day',120),('intraday','5minute',45)):
            try:
                rows=self._gateway.history(master['instrument_token'],now-pd.Timedelta(days=days),now,interval,oi=True)
                output[label]=pd.DataFrame(rows).set_index('date').rename(columns={'open':'Open','high':'High','low':'Low','close':'Close','volume':'Volume','oi':'OI'})
            except Exception as exc:
                output[label]=pd.DataFrame()
                output['fetch_errors'][label]=type(exc).__name__
        self.cache[key]=output

    def _execution_quotes(self):
        import time
        entries=[(key,value) for key,value in self.cache.items() if key[0]=='execution']
        keys=[k for key,value in entries for k in ('NFO:'+key[2],'NSE:'+key[1])]
        try:
            quotes=self._gateway.quotes(sorted(set(keys))) if keys else {}
        except Exception:
            quotes={}
        for key,value in entries:
            value.update({'quote':quotes.get('NFO:'+key[2],{}),'spot_price':quotes.get('NSE:'+key[1],{}).get('last_price'),
                          'fetched_at':pd.Timestamp.now(tz='Asia/Kolkata').isoformat()})
        self.quote_refresh_clock=time.monotonic()

    def get_futures_execution_data(self,symbol,contract):
        import time
        if not self.execution_prefetched:
            # Fetch all selected contract histories before one shared quote batch.
            selected_master=[r for r in self.get_nfo_instruments() if str(r.get('name','')).upper() in self.symbols]
            contracts=universe(selected_master,pd.Timestamp.now(tz='Asia/Kolkata'))
            for name in self.symbols:
                if name in contracts:
                    self._execution_history(name,contracts[name])
            self._execution_history(symbol,contract)
            self._execution_quotes()
            self.execution_prefetched=True
        key=('execution',symbol,contract['tradingsymbol'])
        if key not in self.cache:
            self._execution_history(symbol,contract)
            self._execution_quotes()
        elif time.monotonic()-self.quote_refresh_clock>30:
            self._execution_quotes()
        return dict(self.cache.get(key,{}))
