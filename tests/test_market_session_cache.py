from dataclasses import replace
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo
import pytest
from src.application.settings import PlatformSettings
from src.application.market_session_cache import cached_command, live_session

IST=ZoneInfo('Asia/Kolkata')
def moment(value):
    return datetime.fromisoformat(value).replace(tzinfo=IST)

class Platform:
    def __init__(self):
        self.settings=PlatformSettings(market_data_source='kite')
        self.calls=[]
    @staticmethod
    def _serialize(value): return value
    @cached_command('futures_suggest')
    def run(self,limit=5,minimum_score=40):
        self.calls.append((limit,minimum_score))
        return {'suggestions':[{'symbol':'TEST'}], 'reviewed':[]}

@pytest.mark.parametrize('when,expected',[
    ('2026-10-08T09:14:59',False),('2026-10-08T09:15:00',True),
    ('2026-10-08T15:29:59',True),('2026-10-08T15:30:00',False),
    ('2026-10-08T18:00:00',False),('2026-10-10T11:00:00',False),
])
def test_ist_boundaries(when,expected,monkeypatch):
    monkeypatch.delenv('MARKET_LIVE_END_IST',raising=False)
    monkeypatch.delenv('MARKET_HOLIDAYS_IST',raising=False)
    assert live_session(moment(when))==expected
    assert live_session(moment(when).astimezone(ZoneInfo('UTC')))==expected

@pytest.mark.parametrize('when', [
    '2026-10-08T08:00', '2026-10-08T09:14:59',
    '2026-10-08T15:30', '2026-10-08T18:00', '2026-10-10T11:00',
])
@pytest.mark.parametrize('files', ['missing', 'existing', 'corrupt'])
def test_commands_refresh_at_any_time(tmp_path, monkeypatch, when, files):
    monkeypatch.setenv('MARKET_REPORT_CACHE_DIR', str(tmp_path))
    monkeypatch.setenv('MARKET_HOLIDAYS_IST', '2026-10-08')
    p = Platform()
    if files != 'missing':
        with patch('src.application.market_session_cache.session_now', return_value=moment('2026-10-08T14:00')):
            p.run()
        if files == 'corrupt':
            next(tmp_path.glob('*.json')).write_text('broken')
    before = len(p.calls)
    with patch('src.application.market_session_cache.session_now', return_value=moment(when)):
        result = p.run()
    assert len(p.calls) == before + 1
    assert result['suggestions'][0]['symbol'] == 'TEST'
    assert result['data_cache']['source'] == 'LIVE'
    assert result['data_cache']['historical_snapshot'] is False


def test_parameter_and_settings_changes_refresh_outside_hours(tmp_path, monkeypatch):
    monkeypatch.setenv('MARKET_REPORT_CACHE_DIR', str(tmp_path))
    p = Platform()
    with patch('src.application.market_session_cache.session_now', return_value=moment('2026-10-08T18:00')):
        p.run()
        p.run(minimum_score=70)
        p.settings = replace(p.settings, capital=p.settings.capital + 1)
        p.run()
    assert p.calls == [(5, 40), (5, 70), (5, 40)]
    assert len(list(tmp_path.glob('*.json'))) == 3

def test_live_pipeline_keeps_mode_across_worker_threads(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setenv('MARKET_REPORT_CACHE_DIR',str(tmp_path))
    class Nested(Platform):
        @cached_command('outer')
        def outer(self):
            with patch('src.application.market_session_cache.session_now',return_value=moment('2026-10-08T18:00')):
                with ThreadPoolExecutor(max_workers=1) as pool:
                    return pool.submit(self.run).result()
    p=Nested()
    with patch('src.application.market_session_cache.session_now',return_value=moment('2026-10-08T15:29')):
        assert p.outer()['data_cache']['source']=='LIVE'
    assert len(p.calls)==1

def test_holidays_and_explicit_offline_mode(monkeypatch):
    monkeypatch.setenv('MARKET_HOLIDAYS_IST','2026-10-08')
    assert not live_session(moment('2026-10-08T11:00'))
    p=Platform();p.settings=replace(p.settings,market_data_source='cache')
    assert 'data_cache' not in p.run()
