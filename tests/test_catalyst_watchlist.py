from copy import deepcopy
from datetime import datetime, timezone

import pytest

from src.news.catalyst_watchlist import assess_catalyst, ist_time
from src.presenter.futures_report import FuturesReportPresenter

NOW = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)


def candidate():
    return {"symbol": "TEST", "final_action": "REJECT",
            "setup_entry_timing": {"status": "WAIT FOR BREAKOUT", "trigger_price": 105},
            "event_risk": {"hard_block": True, "event_data_availability_state": "COMPLETE",
                "event_risk_level": "HIGH", "matched_events": [{
                    "title": "Scheduled results", "event_start": "2026-10-09T12:00:00+00:00",
                    "is_scheduled": True, "is_confirmed": True, "status": "SCHEDULED",
                    "freshness_score": 100, "source_urls_or_ids": ["https://example.com/filing"],
                    "category": "EARNINGS", "direction": "VOLATILITY_ONLY"}]}}


def test_upcoming_results_are_watchlist_not_trade_approval():
    result = assess_catalyst(candidate(), NOW)
    assert result["status"] == "UPCOMING_CATALYST_WATCHLIST"
    assert result["trade_approved"] is False
    assert result["event_hard_block"] is True
    assert result["upcoming_events"][0]["time_ist"] == "09 Oct 2026, 05:30 PM IST"


@pytest.mark.parametrize('changes', [
    {"is_confirmed": False}, {"is_scheduled": False}, {"source_urls_or_ids": []},
    {"event_start": "2026-10-07T12:00:00+00:00"},
    {"event_start": "2026-10-20T12:00:00+00:00"},
    {"event_start": "2026-10-09"}, {"status": "RESOLVED"}, {"freshness_score": 0},
])
def test_unverified_past_stale_or_distant_events_are_not_upcoming(changes):
    item = candidate()
    item['event_risk']['matched_events'][0].update(changes)
    assert assess_catalyst(item, NOW)['status'] == 'NO_VERIFIED_UPCOMING_CATALYST'


def test_extended_price_excluded_and_daily_change_is_not_news_reaction():
    item = candidate()
    item['setup_entry_timing']['status'] = 'TOO LATE'
    item['today_news_alignment'] = {'price_change_percent': 5}
    result = assess_catalyst(item, NOW)
    assert result['status'] == 'ALREADY_EXTENDED'
    assert result['reaction_status'] == 'UNKNOWN'
    assert result['session_price_change_percent'] == 5


def test_report_shows_news_in_ist_catalyst_and_conditional_levels():
    item = candidate()
    item['catalyst_assessment'] = assess_catalyst(item, NOW)
    item['levels'] = {'entry': 105, 'stop_loss': 100, 'target_1': 110}
    article = {'title': 'Company schedules results', 'published': '2026-10-08T10:00:00Z',
               'source': 'Exchange', 'url': 'https://example.com/filing', 'sentiment': 'NEUTRAL'}
    item['news'] = {'article_assessments': [article], 'headlines': [deepcopy(article)]}
    output = FuturesReportPresenter.render({'reviewed': [item], 'catalyst_watchlist': [item]})
    assert output.count('Corresponding news (') == 1
    assert '08 Oct 2026, 03:30 PM IST' in output
    assert 'Upcoming catalyst: Scheduled results' in output
    assert 'News reaction: UNKNOWN' in output
    assert 'Trade approval: NOT APPROVED' in output
    assert 'stop-loss ₹100.00' in output
    assert 'Upcoming-catalyst watchlist: TEST' in output
    assert ist_time('unknown') == 'Not verified'
