"""Provenanced identity resolution and explicit exact/proxy benchmark catalogue."""
import json,re,time,hashlib
from pathlib import Path
from threading import RLock
from urllib.parse import quote,urlparse
from io import StringIO
import pandas as pd
import requests
from bs4 import BeautifulSoup
from src.futures.sessions import ist,normalise_candles,trading_day
from src.futures_workspace.validation import require_valid_ohlcv

# Names are from the official Nifty catalogue. Yahoo aliases are established
# existing project mappings; new index aliases are discovered through Kite names.
CATALOG={
 'BANKING':('NIFTY BANK','^NSEBANK'), 'PSU_BANK':('NIFTY PSU BANK','^CNXPSUBANK'),
 'IT':('NIFTY IT','^CNXIT'),'PHARMA':('NIFTY PHARMA','^CNXPHARMA'),
 'AUTO':('NIFTY AUTO','^CNXAUTO'),'FMCG':('NIFTY FMCG','^CNXFMCG'),
 'METAL':('NIFTY METAL','^CNXMETAL'),'ENERGY':('NIFTY ENERGY','^CNXENERGY'),
 'POWER':('NIFTY POWER',None),'REAL_ESTATE':('NIFTY REALTY','^CNXREALTY'),
 'CONSUMER_DURABLES':('NIFTY CONSR DURBL',None),
 'FINANCIAL_SERVICES':('NIFTY FIN SERVICE',None),'INSURANCE':('NIFTY INSURANCE',None),
 'NBFC':('NIFTY NBFC',None),'HOUSING_FINANCE':('NIFTY HOUSING FIN',None),
 'HEALTHCARE':('NIFTY HEALTHCARE',None),'HOSPITALS':('NIFTY HOSPITALS',None),
 'CAPITAL_GOODS':('NIFTY CAPITAL GOODS',None),'CEMENT':('NIFTY CEMENT',None),
 'CHEMICALS':('NIFTY CHEMICALS',None),'RETAIL':('NIFTY RETAIL',None),
 'TELECOM':('NIFTY TELECOM',None),'DEFENCE':('NIFTY INDIA DEFENCE',None),
 'INFRA':('NIFTY INFRA','^CNXINFRA'),'MEDIA':('NIFTY MEDIA','^CNXMEDIA')}
PROXIES={'ASSET_MANAGEMENT':'FINANCIAL_SERVICES','EXCHANGE':'FINANCIAL_SERVICES',
 'FINTECH':'FINANCIAL_SERVICES','MINING':'METAL','RENEWABLE_ENERGY':'POWER',
 'BUILDING_MATERIALS':'INFRA','LOGISTICS':'INFRA','AVIATION':'INFRA',
 'APPAREL':'FMCG','BEVERAGES':'FMCG','CONSUMER':'FMCG','JEWELLERY':'CONSUMER_DURABLES',
 'HOSPITALITY':'RETAIL','RESTAURANTS':'RETAIL','FOOD_DELIVERY':'RETAIL','INTERNET':'IT','ELECTRONICS':'CAPITAL_GOODS'}
ALIASES={'REALTY':'REAL_ESTATE','POWER GENERATION/DISTRIBUTION':'POWER','POWER GENERATION':'POWER',
         'HEALTH CARE':'HEALTHCARE','HOSPITAL':'HOSPITALS','BANKS':'BANKING','BANK':'BANKING'}


def canonical(label):
    text=re.sub(r'\s+',' ',str(label or '').upper().replace('_',' ')).strip()
    direct=text.replace(' ','_')
    if direct in CATALOG or direct in PROXIES or direct=='DIVERSIFIED':return direct
    if text in ALIASES:return ALIASES[text]
    for term,value in [('HOSPITAL','HOSPITALS'),('POWER','POWER'),('TELECOM','TELECOM'),('INSURANCE','INSURANCE'),
       ('ASSET MANAGEMENT','ASSET_MANAGEMENT'),('HOUSING FINANCE','HOUSING_FINANCE'),('NBFC','NBFC'),
       ('PHARMA','PHARMA'),('BANK','BANKING'),('REAL ESTATE','REAL_ESTATE'),('CEMENT','CEMENT'),
       ('CHEMICAL','CHEMICALS'),('INFORMATION TECHNOLOGY','IT'),('AUTOMOBILE','AUTO'),
       ('FINANCIAL','FINANCIAL_SERVICES'),('METAL','METAL'),('HEALTH','HEALTHCARE')]:
        if term in text:return value
    return 'UNKNOWN'


def benchmark_quality(frame,now,maximum_age=2,calendar=None):
    data=normalise_candles(frame,'day',now)
    require_valid_ohlcv(data)
    if len(data)<21:raise ValueError('SECTOR_HISTORY_INSUFFICIENT_21_ALIGNED_SESSIONS')
    from src.futures_workspace.liquidity import completed_day
    end=completed_day(now)
    age=sum(trading_day(day) for day in pd.date_range(data.index[-1]+pd.Timedelta(days=1),end,freq='D'))
    if age>maximum_age:raise ValueError('SECTOR_HISTORY_STALE')
    if calendar is not None:
        sessions=normalise_candles(calendar,'day',now).index
        grid=sessions[sessions<=data.index[-1]][-21:]
    else:
        grid=pd.DatetimeIndex([day for day in pd.date_range(data.index[-21],data.index[-1],freq='D') if trading_day(day)])
    if len(grid)<21 or not grid.difference(data.index).empty:
        raise ValueError('SECTOR_HISTORY_MISSING_REQUIRED_SESSIONS')
    return data


class SectorResolver:
    def __init__(self,local,fetcher=None,root='.cache/futures_sector_identity'):
        self.local=local;self.fetcher=fetcher;self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.lock=RLock();self.isins=None;self.blocked=set();self.last_request=0
    def fetch(self,url):
        if self.fetcher:return self.fetcher(url)
        host=urlparse(url).netloc
        with self.lock:
            if host in self.blocked:raise RuntimeError('SOURCE_ACCESS_UNAVAILABLE')
            delay=max(0,.35-(time.monotonic()-self.last_request))
            if delay:time.sleep(delay)
            self.last_request=time.monotonic()
        response=requests.get(url,timeout=(3,4),headers={'User-Agent':'Mozilla/5.0','Accept':'application/json,text/html,text/csv'})
        if response.status_code in (401,403,429):
            with self.lock:self.blocked.add(host)
        response.raise_for_status()
        return response.text
    def resolve(self,symbol,now):
        path=self.root/(hashlib.sha256(symbol.encode()).hexdigest()+'.json')
        cached=None
        if path.exists():
            try:
                cached=json.loads(path.read_text())
                age=(ist(now)-ist(cached['verified_at'])).total_seconds()/86400
                if 0<=age<=30 and cached['status']=='VERIFIED':return cached
            except (ValueError,KeyError):cached=None
        errors=[];observations=[];isin=None
        try:
            with self.lock:
                if self.isins is None:
                    table=pd.read_csv(StringIO(self.fetch('https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv')))
                    table.columns=table.columns.str.strip()
                    self.isins=dict(zip(table['SYMBOL'],table['ISIN NUMBER']))
                isin=self.isins.get(symbol)
            raw=json.loads(self.fetch('https://www.nseindia.com/api/quote-equity?symbol='+quote(symbol)))
            info=raw.get('info',{});industry=raw.get('industryInfo',{})
            if info.get('symbol')!=symbol or not isin or info.get('isin')!=isin:
                raise ValueError('NSE_SYMBOL_ISIN_MISMATCH_OR_UNVERIFIED')
            sector=next((canonical(industry.get(k)) for k in ('basicIndustry','industry','sector') if canonical(industry.get(k))!='UNKNOWN'),'UNKNOWN')
            if sector!='UNKNOWN':observations.append({'sector':sector,'source':'NSE','raw':industry,'url':'https://www.nseindia.com/api/quote-equity?symbol='+symbol})
        except Exception as exc:errors.append({'source':'NSE','error_type':type(exc).__name__})
        if not observations and isin:
            try:
                from src.quality.public_fundamentals import PublicFundamentalProvider
                suggestions=json.loads(self.fetch('https://www.moneycontrol.com/mccode/common/autosuggestion_solr.php?query='+quote(symbol)+'&type=1&format=json'))
                url=PublicFundamentalProvider.moneycontrol_url(symbol,suggestions)
                if not url:raise ValueError('MONEYCONTROL_IDENTITY_UNRESOLVED')
                html=self.fetch(url)
                def field(name):
                    match=re.search(r'"'+name+r'"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"',html)
                    return json.loads('"'+match.group(1)+'"') if match else None
                if field('nseId')!=symbol or field('isinid')!=isin:raise ValueError('MONEYCONTROL_SYMBOL_ISIN_MISMATCH')
                label=field('sector') or field('industry')
                if not label:
                    match=re.search(r'SECTOR\s*:\s*([^|\n]{3,70})[|\n]',BeautifulSoup(html,'html.parser').get_text(' ',strip=True),re.I)
                    label=match.group(1).strip() if match else None
                sector=canonical(label)
                if sector!='UNKNOWN':observations.append({'sector':sector,'source':'MONEYCONTROL','raw':label,'url':url})
            except Exception as exc:errors.append({'source':'MONEYCONTROL','error_type':type(exc).__name__})
        if not observations and cached and cached.get('status')=='VERIFIED':
            return {**cached,'status':'STALE_CONTEXT_ONLY','errors':errors}
        local=self.local.get(symbol,'UNKNOWN')
        result={'symbol':symbol,'isin':isin,'sector':observations[0]['sector'] if observations else local,
                'status':'VERIFIED' if observations else 'LOCAL_UNVERIFIED' if local!='UNKNOWN' else 'UNKNOWN',
                'source':observations[0]['source'] if observations else 'LOCAL_CSV_UNVERIFIED',
                'verified_at':ist(now).isoformat() if observations else None,'retrieved_at':ist(now).isoformat(),
                'observations':observations,'errors':errors}
        if observations and local!='UNKNOWN' and local!=result['sector']:
            result['superseded_unverified_local_sector']=local
        if observations and cached and cached.get('status')=='VERIFIED' and cached.get('sector')!=result['sector']:
            result['status']='REVIEW_REQUIRED';result['conflicting_verified_sector']=cached.get('sector')
        if result['status']=='VERIFIED':
            temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(result));temporary.replace(path)
        return result
