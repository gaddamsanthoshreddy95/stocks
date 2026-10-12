"""Persist standalone scans in the worker, independent of browser navigation."""
from src.application.platform import TradingPlatform
from src.ui.stock_tables import context_columns


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
    report=TradingPlatform(settings=settings).scan_futures_opportunities(limit,mode=mode)
    report['_ui_saved_scan_id']=database.save_futures_scan(report,settings.market_data_source,run_id)
    return report
