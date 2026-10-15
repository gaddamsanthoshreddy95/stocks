"""User-curated real estate watchlist using the shared manual analysis workflow."""
from src.ui.ai_stock import AiStockHistory,scan_ai_stock,render as render_theme

REALITY_STOCKS={
    'DLF':'DLF Ltd',
    'GODREJPROP':'Godrej Properties',
    'LODHA':'Lodha Developers',
    'OBEROIRLTY':'Oberoi Realty',
    'PRESTIGE':'Prestige Estates Projects',
    'PHOENIXLTD':'Phoenix Mills',
}


class RealityStockHistory(AiStockHistory):
    table='reality_stock_scan_runs'


def scan_reality_stocks(settings,database,limit,mode,run_id):
    return scan_ai_stock(settings,database,limit,mode,run_id,stocks=REALITY_STOCKS,
        title='Reality',cache_name='reality_stocks_prepared',history_class=RealityStockHistory,
        saved_id='_reality_saved_scan_id')


def render(platform,database,start_job,is_running):
    return render_theme(platform,database,start_job,is_running,stocks=REALITY_STOCKS,
        title='Reality',feature='reality_stocks',history_class=RealityStockHistory,
        scan_function=scan_reality_stocks,connection_label='Company')
