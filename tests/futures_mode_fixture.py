"""Deterministic, complete simulated feeds; no network or brokerage orders."""
from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np
import pandas as pd
from src.application.settings import PlatformSettings
from src.futures.config import FuturesScanConfig
from src.futures.runtime import ScanRuntime
from src.futures.scanner import FuturesOpportunityScanner
from src.futures.preparation import PreparedScanner
from src.futures.cache import PreparationCache
from src.quality.models import FundamentalSnapshot
from src.sector.sector_strength import SectorStrength

NOW=pd.Timestamp('2026-10-08 09:40',tz='Asia/Kolkata')


def frame(index,prices,volume=100):
    prices=np.asarray(prices,dtype=float)
    return pd.DataFrame({'Open':prices-.05,'High':prices+.15,'Low':prices-.15,'Close':prices,'Volume':volume,'OI':1000},index=index)


class SimulatedKite:
    def __init__(self, owner, latency=0):
        self.owner,self.latency=owner,latency
        self.quote=Mock(side_effect=self._quote)
        self.historical_data=Mock(side_effect=self._history)
        self.order_margins=Mock(side_effect=lambda orders:[{'tradingsymbol':order['tradingsymbol'],'total':10000} for order in orders])
        self.margins=Mock(return_value={'available':{'cash':100000}})
        self.profile=Mock(return_value={'user_id':'SYNTHETIC_TEST_ACCOUNT'})
        self.orders=Mock(return_value=[])
        self.trades=Mock(return_value=[])
        self.positions=Mock(return_value={'net':[], 'day':[]})
        for name in ('place_order','modify_order','cancel_order','convert_position','exit_order'):
            setattr(self,name,Mock(side_effect=AssertionError('Broker mutations forbidden')))

    def pause(self):
        if self.latency:
            from time import sleep
            sleep(self.latency)

    def _quote(self,keys):
        self.pause()
        return {key:self.owner.quote_for(key) for key in keys}

    def _history(self,token,start,end,interval,**kwargs):
        self.pause()
        data=self.owner.daily if interval=='day' else self.owner.intraday
        data=data.loc[(data.index>=pd.Timestamp(start))&(data.index<=pd.Timestamp(end))]
        return [{'date':stamp,**{key.lower():float(value) for key,value in row.items()}} for stamp,row in data.iterrows()]


class SimulatedProvider:
    def __init__(self,symbols,latency=0):
        self.symbols=symbols
        daily_index=pd.date_range(end=NOW.normalize()-pd.Timedelta(days=1),periods=270,freq='B')
        self.daily=frame(daily_index,180+np.arange(270)*.075,1000)
        self.daily['High']=self.daily.Close+1
        self.daily['Low']=self.daily.Close-1
        frames=[]
        for day in pd.date_range(end=NOW.normalize(),periods=9,freq='B'):
            index=pd.date_range(day+pd.Timedelta(hours=9,minutes=15),periods=75,freq='5min')
            price=200+np.arange(75)*.005+np.sin(np.arange(75))*.15
            frames.append(frame(index,price,200 if day.date()==NOW.date() else 100))
        self.intraday=pd.concat(frames)
        self.provider=SimpleNamespace(kite=SimulatedKite(self,latency),_instrument_token=lambda symbol:1)
        self.live_refresh_active=False
        self.get_futures_execution_data=Mock(side_effect=self._execution)
        self.master=[{'name':symbol,'tradingsymbol':symbol+'26OCTFUT','instrument_type':'FUT','segment':'NFO-FUT',
                     'expiry':'2026-10-27','lot_size':100,'instrument_token':1} for symbol in symbols]

    def get_nfo_instruments(self):return self.master
    def begin_live_refresh(self,symbols):self.live_refresh_active=True
    def end_live_refresh(self):self.live_refresh_active=False
    def quote_for(self,key):
        bars=self.intraday.loc[self.intraday.index+pd.Timedelta(minutes=5)<=NOW]
        today=bars.loc[bars.index.date==NOW.date()]
        price=float(today.Close.iloc[-1])
        return {'instrument_token':1,'timestamp':NOW.isoformat(),'last_price':price,'oi':1100,'volume':1000,
                'ohlc':{'open':float(today.Open.iloc[0]),'high':float(today.High.max()),'low':float(today.Low.min()),'close':float(self.daily.Close.iloc[-1])},
                'depth':{'buy':[{'price':price-.01,'quantity':10000}], 'sell':[{'price':price+.01,'quantity':10000}]}}
    def get_data(self,symbol):
        data=self.daily.copy()
        quote=self.quote_for('NSE:'+symbol)
        if self.live_refresh_active:
            for col,key in [('Open','open'),('High','high'),('Low','low')]:data.loc[NOW.normalize(),col]=quote['ohlc'][key]
            data.loc[NOW.normalize(),'Close']=quote['last_price']
            data.loc[NOW.normalize(),'Volume']=quote['volume']
            data.loc[NOW.normalize(),'IS_LIVE_CANDLE']=True
            data.loc[NOW.normalize(),'LIVE_SESSION_PROGRESS']=25/375
        return data
    get_annual_history=get_data
    def get_session_intraday(self,symbol):return self.intraday.loc[self.intraday.index+pd.Timedelta(minutes=5)<=NOW].copy()
    def _execution(self,symbol,contract):
        data=self.intraday.copy();data.attrs['contract']=contract['tradingsymbol']
        quote=self.quote_for('NFO:'+contract['tradingsymbol'])
        return {'contract':contract,'quote':quote,'spot_price':quote['last_price'],'daily':self.daily.copy(),
                'intraday':data,'instrument_token':1}


def fixture(symbols=None,latency=0):
    symbols=symbols or ['SBIN','PNB','AXISBANK','FEDERALBNK']
    provider=SimulatedProvider(symbols,latency)
    facts=FundamentalSnapshot('TEST',pe_ratio=25,sector_pe=25,total_debt=0,debt_to_equity=0,roe=20,roce=25,
        fii_holding_percent=10,dii_holding_percent=10,fii_holding_change_pct_points=1,dii_holding_change_pct_points=1,
        promoter_holding_percent=50,promoter_holding_change_pct_points=0,promoter_pledge=0,
        delivery_percent=50,monthly_delivery_percent=40,quarterly_revenue_growth_pct=(4,5,6),
        quarterly_profit_growth_pct=(4,5,6),commentary_strength='VERY_STRONG',block_deal_price_impact=False)
    fundamental=SimpleNamespace(get_fundamentals=Mock(side_effect=lambda symbol:replace(facts,symbol=symbol)))
    news=Mock(side_effect=lambda symbol:{'news_state':'NO_RELEVANT_NEWS','checked_at':NOW.isoformat(),'headlines':[],'article_assessments':[]})
    event=lambda *args:{'event_data_availability_state':'COMPLETE','hard_block':False,'event_risk_level':'LOW','freshness_state':'FRESH'}
    platform=SimpleNamespace(provider=provider,settings=PlatformSettings(),_universe_symbols=lambda:symbols)
    scanner=FuturesOpportunityScanner(platform,config=replace(FuturesScanConfig(),review_per_direction=5),
        fundamental_provider=fundamental,news_provider=news,event_provider=event)
    from tempfile import TemporaryDirectory
    scanner._ledger_test_directory = TemporaryDirectory(prefix='futures-ledger-test-')
    scanner.runtime = ScanRuntime(ledger_path=scanner._ledger_test_directory.name+'/ledger.sqlite3')
    return scanner,provider


def seed(scanner,directory,*,stale_sessions=False):
    runtime=ScanRuntime(cache_directory=str(directory),require_margin=False,movement_basis='FUTURES', ledger_path=str(directory)+'/ledger.sqlite3')
    manager=PreparedScanner(scanner,runtime)
    manager.now=NOW;manager.clock=lambda:NOW
    cache=manager.cache
    symbols=scanner.platform._universe_symbols()
    for symbol in set(symbols)|set(SectorStrength.KITE_INDEX_SYMBOLS.values())|{'NIFTY 50'}:
        cache.put_frame('NSE:'+symbol+':day',scanner.provider.daily,now=NOW,ttl=604800)
    for symbol in symbols:
        facts=scanner.company_research.engine.fundamental_provider.get_fundamentals(symbol)
        cache.put('company',symbol,asdict(facts),now=NOW-pd.Timedelta(hours=12),ttl=604800)
        cache.put('market_fields',symbol,{'values':{field:getattr(facts,field) for field in ('pe_ratio','sector_pe','delivery_percent','monthly_delivery_percent')}},now=NOW,ttl=86400)
        cache.put('news',symbol,scanner.news_provider(symbol),now=NOW,ttl=900)
        bars=scanner.provider.get_session_intraday(symbol)
        if stale_sessions:bars=bars.loc[bars.index.date<NOW.date()]
        for identity in ('NSE:'+symbol+':5minute','NFO:'+symbol+'26OCTFUT:5minute'):
            cache.put_frame(identity,bars,now=NOW-pd.Timedelta(hours=12) if stale_sessions else NOW,ttl=604800)
        cache.put_frame('NFO:'+symbol+'26OCTFUT:day',scanner.provider.daily,now=NOW,ttl=604800)
        cache.put('backtest',symbol+'26OCTFUT',{'movement_basis':'FUTURES','policy_fingerprint':manager.backtest_policy(),
            'groups':{},'validation_status':'INSUFFICIENT_OUT_OF_SAMPLE_SIGNALS'},now=NOW,ttl=604800)
    scanner.company_research.engine.fundamental_provider.get_fundamentals.reset_mock()
    scanner.news_provider.reset_mock()
    return runtime,cache
