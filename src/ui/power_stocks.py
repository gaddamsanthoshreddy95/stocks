"""Separate power watchlist using the same manual thematic scanner as AI-Stock."""
from src.ui.ai_stock import AiStockHistory,scan_ai_stock,render as render_theme

POWER_STOCKS={
    'NTPC':'Power generation',
    'POWERGRID':'Power transmission',
    'TATAPOWER':'Power generation and distribution',
    'JSWENERGY':'Power generation',
    'BHEL':'Power equipment',
    'CGPOWER':'Electrical equipment and power infrastructure',
    'PFC':'Power-sector financing',
    'RECLTD':'Power-sector financing',
}


class PowerStockHistory(AiStockHistory):
    table='power_stock_scan_runs'


def scan_power_stocks(settings,database,limit,mode,run_id):
    return scan_ai_stock(settings,database,limit,mode,run_id,stocks=POWER_STOCKS,
        title='Power Stocks',cache_name='power_stocks_prepared',history_class=PowerStockHistory,
        saved_id='_power_saved_scan_id')


def render(platform,database,start_job,is_running):
    return render_theme(platform,database,start_job,is_running,stocks=POWER_STOCKS,
        title='Power Stocks',feature='power_stocks',history_class=PowerStockHistory,
        scan_function=scan_power_stocks,connection_label='Power sector connection')
