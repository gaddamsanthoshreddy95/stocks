from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch
import json

import numpy as np
import pandas as pd
import pytest

from src.application.settings import PlatformSettings
from src.futures.config import FuturesScanConfig
from src.futures.costs import FuturesCosts, trade_plan
from src.futures.scoring import score_both, prepare
from src.futures.scanner import FuturesOpportunityScanner, REQUIRED_EXECUTION
from src.presenter.futures_opportunities import FuturesOpportunitiesPresenter

NOW = pd.Timestamp('2026-10-08 13:00', tz='Asia/Kolkata')


def candles(values, index=None, volume=100):
    values = np.asarray(values, dtype=float)
    index = index if index is not None else pd.date_range(end=NOW.normalize(), periods=len(values), freq='B')
    return pd.DataFrame({'Open': values+.01, 'High': values+1, 'Low': values-1,
                         'Close': values, 'Volume': volume, 'OI': 1000}, index=index)


def histories():
    down = candles(130-np.arange(260)*.03+np.tile([0, .1, .2, -.1], 65))
    down.iloc[-1, down.columns.get_loc('Volume')] = 300
    up = candles(100+np.arange(260)*.05)
    up['Open'] = up.Close-.01
    up.iloc[-1, up.columns.get_loc('Volume')] = 300
    return down, up, candles(np.full(260, 120))


def test_bearish_discovery_is_independent_and_not_inverse_bullish_score():
    down, up, flat = histories()
    bear = score_both(down, benchmark=flat, sector=flat)
    bull = score_both(up, benchmark=flat, sector=flat)
    assert bear['SHORT']['technical_score'] > bear['LONG']['technical_score']
    assert bull['LONG']['technical_score'] > bull['SHORT']['technical_score']
    assert bear['SHORT']['technical_score'] != 100-bear['LONG']['technical_score']
    assert bear['SHORT']['evidence']['lower_highs_lower_lows']
    assert bear['SHORT']['evidence']['minus_di'] > bear['SHORT']['evidence']['plus_di']
    assert bear['SHORT']['evidence']['macd_histogram'] < 0


def test_sharp_fall_is_not_automatically_a_short():
    down, _, flat = histories()
    down.iloc[-1, down.columns.get_loc('Close')] *= .95
    down.iloc[-1, down.columns.get_loc('Low')] = down.Close.iloc[-1]-.2
    result = score_both(down, benchmark=flat, sector=flat)['SHORT']
    assert result['timing'] == 'TOO LATE'


def test_configurable_weights_and_thresholds():
    assert FuturesScanConfig().weights('bearish') == {
        'trend': .25, 'momentum': .20, 'volume': .15, 'structure': .15, 'sector': .1, 'opportunity': .15}
    with pytest.raises(ValueError):
        replace(FuturesScanConfig(), bearish_trend_weight=.5)
    with pytest.raises(ValueError):
        replace(FuturesScanConfig(), target_fraction=0)
    with pytest.raises(ValueError):
        replace(FuturesScanConfig(), minimum_history=20)


def test_short_execution_plan_uses_futures_price_and_real_lot_size():
    plan = trade_plan(200, 500, 'SHORT', config=FuturesScanConfig(), costs=FuturesCosts(),
                      risk_budget=1000, trade_date='2026-10-08')
    assert plan['entry'] == 200
    assert plan['target'] == pytest.approx(199.4)
    assert plan['stop_loss'] == pytest.approx(200.4)
    assert plan['gross_profit_at_target'] == pytest.approx(300)
    assert plan['gross_loss_at_stop'] == pytest.approx(200)
    assert plan['net_profit_at_target'] < plan['gross_profit_at_target']
    assert plan['net_loss_at_stop'] > plan['gross_loss_at_stop']
    assert plan['target_costs']['stt'] == pytest.approx(50)
    assert plan['margin_status'] == 'UNKNOWN'


def test_costs_are_directional_and_date_aware():
    costs = FuturesCosts(slippage_bps=0)
    old = costs.round_trip(200, 199.4, 500, 'SHORT', '2026-03-31')
    new = costs.round_trip(200, 199.4, 500, 'SHORT', '2026-04-01')
    assert old['cost_breakdown']['stt'] == pytest.approx(20)
    assert new['cost_breakdown']['stt'] == pytest.approx(50)
    assert new['cost_breakdown']['stamp_duty'] == pytest.approx(199.4*500*.00002)


def test_sizing_respects_costs_risk_budget_and_optional_supplied_margin():
    small = trade_plan(200, 500, 'SHORT', config=FuturesScanConfig(), costs=FuturesCosts(),
                       risk_budget=10, trade_date='2026-10-08')
    assert small['number_of_lots'] == 0
    assert small['quantity'] == 0
    funded = trade_plan(200, 500, 'SHORT', config=replace(FuturesScanConfig(), maximum_lots=10),
                        costs=FuturesCosts(), risk_budget=10000, trade_date='2026-10-08',
                        margin_per_lot=10000, available_capital=20000)
    assert funded['number_of_lots'] == 2
    assert funded['margin_requirement'] == 20000


def scanner_fixture():
    down, up, flat = histories()
    provider = Mock()
    data = {'DOWN': down, 'UP': up, 'BROKEN': None, 'NIFTY 50': flat}
    provider.get_data.side_effect = lambda symbol: data.get(symbol, flat)
    provider.live_refresh_active = False
    provider.get_nfo_instruments.return_value = [
        {'name': symbol, 'tradingsymbol': symbol+'26OCTFUT', 'instrument_type': 'FUT',
         'segment': 'NFO-FUT', 'expiry': '2026-10-27', 'lot_size': 500}
        for symbol in ('DOWN', 'UP')]
    provider.get_futures_execution_data.return_value = {}
    platform = SimpleNamespace(provider=provider, settings=PlatformSettings(),
                               _universe_symbols=lambda: ['UP', 'DOWN', 'BROKEN', 'NIFTY'])
    news = lambda symbol: {'news_state': 'NO_RELEVANT_NEWS', 'checked_at': NOW.isoformat()}
    event = lambda *args: {'event_data_availability_state': 'COMPLETE', 'hard_block': False,
                           'event_risk_level': 'LOW', 'freshness_state': 'FRESH'}
    fundamental = SimpleNamespace(get_fundamentals=Mock(return_value=None))
    return FuturesOpportunityScanner(platform, news_provider=news, event_provider=event,
                                     fundamental_provider=fundamental), provider


def test_full_universe_scan_has_both_directions_and_missing_data_never_approves():
    scanner, provider = scanner_fixture()
    report = scanner.scan(5, now=NOW, include_backtest=False)
    assert report['universe_size'] == 3
    assert report['long_discovery_count'] == report['short_discovery_count'] == 3
    assert len(report['reviewed']) == 6
    assert any(item['symbol'] == 'DOWN' and item['side'] == 'SHORT' and item['execution_reviewed'] for item in report['reviewed'])
    assert any(item['symbol'] == 'UP' and item['side'] == 'LONG' and item['execution_reviewed'] for item in report['reviewed'])
    assert report['approved_count'] == 0
    assert report['report_c'] == []
    assert all(item['final_decision'] != 'APPROVED' for item in report['reviewed'])
    provider.begin_live_refresh.assert_called_once_with(['BROKEN', 'DOWN', 'UP'])
    provider.end_live_refresh.assert_called_once()
    assert not provider.kite.place_order.called
    assert not provider.kite.order_margins.called
    json.dumps(report, default=str, allow_nan=False)
    text = FuturesOpportunitiesPresenter.render(report)
    assert 'Report A' in text and 'Report B' in text and 'Report C' in text
    assert 'BEARISH_SCORE' in text and 'UNKNOWN' in text


def test_facade_uses_new_scanner_without_bullish_shortlist(monkeypatch):
    from src.application.platform import TradingPlatform
    platform = TradingPlatform.__new__(TradingPlatform)
    platform.settings = PlatformSettings(market_data_source='cache')
    with patch('src.futures.scanner.FuturesOpportunityScanner') as factory:
        factory.return_value.scan.return_value = {'report_a': [], 'report_b': [], 'report_c': []}
        assert platform.scan_futures_opportunities(3, False)['report_b'] == []
    factory.return_value.scan.assert_called_once_with(3, include_backtest=False)


def test_unknown_contract_does_not_fetch_wrong_expiry():
    scanner, provider = scanner_fixture()
    provider.get_nfo_instruments.return_value = []
    report = scanner.scan(5, now=NOW, include_backtest=False)
    assert report['approved_count'] == 0
    provider.get_futures_execution_data.assert_not_called()


def test_approval_expires_if_quote_ages_before_report_finishes():
    scanner, _ = scanner_fixture()
    def provisional(item, *args):
        item.update({'final_decision': 'APPROVED', 'execution_reviewed': True,
                     'futures_quote': {'timestamp': NOW.isoformat()}, 'missing_execution_checks': []})
        scanner._clock = lambda: NOW+pd.Timedelta(minutes=3)
    scanner._execution = provisional
    report = scanner.scan(5, now=NOW, include_backtest=False)
    assert report['report_c'] == []
    assert report['approved_count'] == 0
    reviewed = [item for item in report['reviewed'] if item['execution_reviewed']]
    assert reviewed
    assert all('QUOTE_EXPIRED_BEFORE_REPORT_COMPLETED' in item['reason_codes'] for item in reviewed)


@pytest.mark.parametrize('gate,status', [(None, None)] + [(gate, state) for gate in REQUIRED_EXECUTION for state in ('UNKNOWN', 'FAIL')])
def test_long_unwinding_and_each_mandatory_execution_gate(gate, status):
    from src.quality.futures_execution import CHECKS, check, unknown
    scanner, _ = scanner_fixture()
    # Zero-cost synthetic model isolates mandatory gate decisions; production
    # remains at net R:R >=1 and the real tariff is covered by cost regressions.
    from futures_mode_fixture import SyntheticNoCostModel
    scanner.costs = SyntheticNoCostModel()
    item = {'symbol': 'DOWN', 'side': 'SHORT', 'technical_score': 90, 'setup_type': 'BEARISH_TREND_CONTINUATION',
            'reason_codes': [], 'company_research': {'unavailable_checks': [], 'failed_checks': [], 'policy_review_required': False}}
    passed = {key: check(True, {}, 'TEST') for key in CHECKS}
    passed['futures_oi_quality'] = check(False, {'price_change_percent': -1, 'oi_change_percent': -2}, 'LONG_UNWINDING')
    if gate is not None:
        passed[gate] = unknown('MISSING_EVIDENCE') if status == 'UNKNOWN' else check(False, {}, 'FAILED_EVIDENCE')
    future = {'technical_score': 90, 'confirmed': True, 'timing': 'READY', 'reason_codes': [],
              'setup_type': 'BEARISH_TREND_CONTINUATION',
              'evidence': {'support': 190, 'resistance': 210, 'short_trigger_price': 200, 'setup_invalidation_price': 200.1}}
    frame = candles(np.full(260, 200))
    from futures_mode_fixture import SimulatedProvider
    intraday = SimulatedProvider(['DOWN']).intraday
    data = {'daily': frame, 'intraday': intraday,
            'quote': {'timestamp': NOW.isoformat(), 'last_price':200, 'depth': {'buy': [{'price': 200, 'quantity': 10000}],
                               'sell': [{'price': 200.02, 'quantity': 10000}]}}}
    with patch('src.futures.scanner.assess_execution', return_value=passed), \
            patch('src.futures.scanner.directional_setup', return_value=future):
        scanner._execution(item, data, {'lot_size': 500}, (None, None),
                           {'news_state': 'NO_RELEVANT_NEWS', 'checked_at': NOW.isoformat()}, {}, NOW, None)
    if gate is not None:
        assert item['final_decision'] != 'APPROVED'
        assert gate in item['missing_execution_checks'] + item['failed_execution_checks']
        return
    assert item['execution_checks']['futures_oi_quality']['status'] == 'PASS'
    assert item['final_decision'] == 'APPROVED'
    assert item['plan']['entry'] == 200
    assert item['plan']['target'] == pytest.approx(199.4)
