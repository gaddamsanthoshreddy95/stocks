"""Separate, manually scanned defence watchlist supplied by the user."""
from src.ui.ai_stock import AiStockHistory,scan_ai_stock,render as render_theme

DEFENCE_STOCKS={
    'HAL':'Fighter jets, helicopters',
    'BEL':'Defence electronics, radar',
    'BDL':'Missiles, weapon systems',
    'MAZDOCK':'Warships, submarines',
    'COCHINSHIP':'Naval shipbuilding',
    'BHARATFORG':'Artillery, defence manufacturing',
    'LT':'Naval systems, defence engineering',
    'BHEL':'Defence and naval equipment',
    'ASHOKLEY':'Military logistics vehicles',
    'M&M':'Defence vehicles through group businesses',
}
DEFENCE_COMPANIES={
    'HAL':'Hindustan Aeronautics',
    'BEL':'Bharat Electronics',
    'BDL':'Bharat Dynamics',
    'MAZDOCK':'Mazagon Dock Shipbuilders',
    'COCHINSHIP':'Cochin Shipyard',
    'BHARATFORG':'Bharat Forge',
    'LT':'Larsen & Toubro',
    'BHEL':'Bharat Heavy Electricals',
    'ASHOKLEY':'Ashok Leyland',
    'M&M':'Mahindra & Mahindra',
}


class DefenceIndiaHistory(AiStockHistory):
    table='defence_india_scan_runs'


def scan_defence_india(settings,database,limit,mode,run_id):
    return scan_ai_stock(settings,database,limit,mode,run_id,stocks=DEFENCE_STOCKS,
        title='Defence India',cache_name='defence_india_prepared',history_class=DefenceIndiaHistory,
        saved_id='_defence_saved_scan_id',companies=DEFENCE_COMPANIES)


def render(platform,database,start_job,is_running):
    return render_theme(platform,database,start_job,is_running,stocks=DEFENCE_STOCKS,
        title='Defence India',feature='defence_india',history_class=DefenceIndiaHistory,
        scan_function=scan_defence_india,connection_label='Defence business',companies=DEFENCE_COMPANIES)
