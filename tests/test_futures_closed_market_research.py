from types import SimpleNamespace
from unittest.mock import patch
import pandas as pd
import pytest

from src.futures.preparation import PreparedDataProvider, live_market_open


@pytest.mark.parametrize('stamp',[
    '2026-10-15 08:00','2026-10-15 15:30','2026-10-15 18:00','2026-10-17 10:00'])
def test_closed_market_keeps_daily_research_without_overlaying_old_quote(stamp):
    now=pd.Timestamp(stamp,tz='Asia/Kolkata')
    previous=pd.Timestamp('2026-10-14',tz='Asia/Kolkata')
    cached=pd.DataFrame({'Open':[100.],'High':[102.],'Low':[99.],'Close':[101.],'Volume':[1000.]},index=[previous])
    manager=SimpleNamespace(scanner=SimpleNamespace(provider=object()),mode='LIVE_SCAN',now=now,
        clock=lambda:now,errors={},cache=SimpleNamespace(frame=lambda *a,**kw:cached))
    provider=PreparedDataProvider(manager)
    provider.quotes={'NSE:STOCK':{'timestamp':previous.isoformat(),'last_price':9999}}
    result=provider.get_data('STOCK')
    assert result.Close.iloc[-1]==101 and result.index[-1]==previous
    assert 'IS_LIVE_CANDLE' not in result and not manager.errors
    assert len(cached)==1


def test_preopen_does_not_use_incomplete_today_daily_candle():
    now=pd.Timestamp('2026-10-15 08:00',tz='Asia/Kolkata')
    cached=pd.DataFrame({'Close':[101.,999.]},index=[now.normalize()-pd.Timedelta(days=1),now.normalize()])
    manager=SimpleNamespace(scanner=SimpleNamespace(provider=object()),mode='LIVE_SCAN',now=now,
        clock=lambda:now,errors={},cache=SimpleNamespace(frame=lambda *a,**kw:cached))
    result=PreparedDataProvider(manager).get_data('STOCK')
    assert len(result)==1 and result.Close.iloc[-1]==101


def test_holiday_does_not_require_live_quote(monkeypatch):
    monkeypatch.setenv('MARKET_HOLIDAYS_IST','2026-10-15')
    assert not live_market_open(pd.Timestamp('2026-10-15 10:00',tz='Asia/Kolkata'))


def test_closed_live_scan_retains_scores_and_never_approves(tmp_path):
    from futures_mode_fixture import fixture,seed,NOW
    scanner,provider=fixture(['SBIN'])
    runtime,_=seed(scanner,tmp_path)
    now=NOW.normalize()+pd.Timedelta(hours=18)
    with patch('src.futures.preparation.collect',return_value=[]):
        report=scanner.scan(mode='LIVE_SCAN',runtime=runtime,now=now)
    assert report['live_market_closed'] and report['research_only']
    assert any(i.get('technical_score') is not None for i in report['reviewed'])
    assert report['approved_count']==0 and not report['report_c']
    provider.provider.kite.place_order.assert_not_called()
