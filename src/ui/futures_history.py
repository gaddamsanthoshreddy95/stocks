"""Persist standalone scans in the worker, independent of browser navigation."""
from src.application.platform import TradingPlatform


def scan_and_save(settings,database,limit,mode,run_id):
    report=TradingPlatform(settings=settings).scan_futures_opportunities(limit,mode=mode)
    report['_ui_saved_scan_id']=database.save_futures_scan(report,settings.market_data_source,run_id)
    return report
