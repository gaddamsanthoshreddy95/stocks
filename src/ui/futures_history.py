"""Persist standalone scans in the worker, independent of browser navigation."""
from src.application.platform import TradingPlatform
from src.ui.stock_tables import context_columns
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict,is_dataclass
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
from src.quality.public_fundamentals import PublicFundamentalProvider
from contextlib import closing


def saved_weekly_fundamentals(database,symbol):
    if database is None:return None
    try:
        with closing(sqlite3.connect(Path(database.path).resolve().as_uri()+'?mode=ro',uri=True)) as connection:
            rows=connection.execute("SELECT retrieved_at,payload FROM ft_evidence WHERE symbol=? AND kind='FUNDAMENTAL' ORDER BY id DESC LIMIT 20",(symbol,)).fetchall()
        now=datetime.now(timezone.utc)
        for captured,payload in rows:
            date=datetime.fromisoformat(captured)
            if date.tzinfo is None or not 0<=(now-date).total_seconds()<=7*86400:continue
            evidence=json.loads(payload);raw=evidence.get('snapshot') or {}
            if raw.get('symbol')!=symbol or raw.get('source','UNKNOWN')=='UNKNOWN':continue
            if any(raw.get(key) is not None for key in ('pe_ratio','sector_pe','roe','roce')):
                return {'snapshot':raw,'retrieved_at':captured,'read_at':now.isoformat(),
                    'data_origin':'SAVED_WEEKLY_FUNDAMENTALS','basis':'EARLIER_SAVED_COMPANY_DATA_DISPLAY_ONLY',
                    'status':'AVAILABLE' if all(raw.get(key) is not None for key in ('pe_ratio','sector_pe','roe','roce')) else 'PARTIAL'}
    except (sqlite3.Error,ValueError,TypeError,KeyError):pass
    return None


def fetch_missing_fundamentals(report,provider=None,database=None):
    """Real company facts, fetched once per symbol; scoring evidence is untouched."""
    report=deepcopy(report)
    symbols=sorted({item['symbol'] for item in report.get('reviewed',[])
                    if any(context_columns(item).get(field) is None for field in ('P/E','Sector P/E','ROE %','ROCE %'))})
    if not symbols:return report
    provider=provider or PublicFundamentalProvider()
    def fetch(symbol):
        saved=saved_weekly_fundamentals(database,symbol)
        if saved and saved['status']=='AVAILABLE':return symbol,saved
        try:
            snapshot=provider.get_fundamentals(symbol)
            raw=asdict(snapshot) if is_dataclass(snapshot) else dict(snapshot or {})
            if raw.get('symbol')!=symbol:raise ValueError('FUNDAMENTAL_SYMBOL_MISMATCH')
            if saved and not any(raw.get(field) is not None for field in ('pe_ratio','sector_pe','roe','roce')):return symbol,saved
            provenance={}
            if saved:
                for field,value in saved['snapshot'].items():
                    if field not in ('symbol','source','as_of','evidence') and raw.get(field) is None and value is not None:
                        raw[field]=value
                        provenance[field]={'source':saved['snapshot'].get('source'),
                            'as_of':saved['snapshot'].get('as_of'),'retrieved_at':saved['retrieved_at'],
                            'basis':'EARLIER_SAVED_WEEKLY_VALUE'}
            return symbol,{'snapshot':raw,'retrieved_at':datetime.now(timezone.utc).isoformat(),
                'field_provenance':provenance,
                'basis':'SUPPLEMENTAL_COMPANY_DATA_DISPLAY_ONLY',
                'status':'AVAILABLE' if all(raw.get(field) is not None for field in ('pe_ratio','sector_pe','roe','roce')) else 'PARTIAL' if any(raw.get(field) is not None for field in ('pe_ratio','sector_pe','roe','roce')) else 'UNAVAILABLE'}
        except Exception as exc:
            if saved:return symbol,{**saved,'refresh_error_type':type(exc).__name__}
            return symbol,{'snapshot':{},'status':'UNAVAILABLE','error_type':type(exc).__name__,
                          'retrieved_at':datetime.now(timezone.utc).isoformat()}
    with ThreadPoolExecutor(max_workers=4) as pool:results=dict(pool.map(fetch,symbols))
    for bucket in ('reviewed','report_a','report_b','report_c'):
        for item in report.get(bucket,[]):
            if item.get('symbol') in results:item['display_fundamentals']=deepcopy(results[item['symbol']])
    return report


def refresh_and_save(report,settings,database,run_id):
    report=fetch_missing_fundamentals(report,database=database)
    report['_fundamentals_refresh_only']=True
    report['_ui_saved_scan_id']=database.save_futures_scan(report,settings.market_data_source,run_id)
    return report


def missing_data_rows(report):
    rows=[]
    for item in report.get('reviewed',[]):
        values=context_columns(item)
        research=(item.get('company_research') or {}).get('checks') or {}
        execution=item.get('execution_checks') or {}
        fields=(('P/E',research.get('valuation_quality')),('Sector P/E',research.get('valuation_quality')),
                ('Stock VWAP',research.get('vwap_quality')),('Futures VWAP',execution.get('futures_vwap_quality')),
                ('RSI',execution.get('futures_rsi_quality')),('Relative volume',execution.get('futures_rvol_quality')))
        for field,check in fields:
            if values.get(field) is not None:continue
            check=check or {}
            reasons=check.get('reason_codes') or item.get('missing_execution_checks') or item.get('reason_codes') or ['No verified value retained in this snapshot']
            rows.append({'Symbol':item.get('symbol'),'Direction':item.get('side'),'Missing field':field,
                'Status':check.get('status','NOT_EXECUTION_REVIEWED' if field in ('Futures VWAP','RSI','Relative volume') and not item.get('execution_reviewed') else 'UNAVAILABLE'),
                'Reason':'; '.join(str(r) for r in reasons),
                'Scan time':report.get('generated_at')})
    return rows


def scan_and_save(settings,database,limit,mode,run_id):
    platform=TradingPlatform(settings=settings)
    from src.data_provider.kite_data_provider import KiteDataProvider
    if isinstance(getattr(platform,'provider',None),KiteDataProvider):
        # access.py can update .env while Streamlit retains its old environment.
        platform.provider.provider.reload_saved_credentials()
    report=platform.scan_futures_opportunities(limit,mode=mode)
    report=fetch_missing_fundamentals(report,database=database)
    report['_ui_saved_scan_id']=database.save_futures_scan(report,settings.market_data_source,run_id)
    return report
