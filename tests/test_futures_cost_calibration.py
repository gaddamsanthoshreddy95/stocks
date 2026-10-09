"""Price-basis, sourced fee schedule and local-only calibration regressions."""
from dataclasses import replace
import importlib.util
from pathlib import Path

import pytest

from src.futures.config import FuturesScanConfig
from src.futures.costs import FuturesCosts, fee_schedule, trade_plan


@pytest.mark.parametrize('side,sign', [('LONG', 1), ('SHORT', -1)])
def test_equity_reference_cannot_change_futures_levels(side, sign):
    plans = [trade_plan(205, 100, side, config=FuturesScanConfig(), costs=FuturesCosts(),
                       risk_budget=1000, trade_date='2026-10-08', movement_reference_price=reference)
             for reference in (None, 200, 250)]
    for plan in plans:
        assert plan['target'] == pytest.approx(205*(1+sign*.003))
        assert plan['stop_loss'] == pytest.approx(205*(1-sign*.002))
        assert plan['movement_basis'] == 'FUTURES'
        assert plan['entry_price_kind'] == 'MODELED_FUTURES_EXECUTION_REFERENCE_NOT_BROKER_FILL'
        assert plan['underlying_target'] is None and plan['underlying_stop_loss'] is None
        assert plan['net_risk_reward'] == plans[0]['net_risk_reward']
    assert plans[1]['underlying_context']['reference_price'] == 200
    assert FuturesScanConfig().minimum_net_rr == 1.0


@pytest.mark.parametrize('day,stt,exchange,ipft', [
    ('2024-10-01', .0002, .0000173, .000001),
    ('2026-02-28', .0002, .0000173, .000001),
    ('2026-03-01', .0002, .000018299, .000000001),
    ('2026-03-31', .0002, .000018299, .000000001),
    ('2026-04-01', .0005, .000018299, .000000001),
])
def test_official_effective_dates_and_no_ipft_double_count(day, stt, exchange, ipft):
    result = FuturesCosts(slippage_bps=0).round_trip(100, 101, 100, 'LONG', day)
    fees = result['cost_breakdown']; turnover = 20100
    assert fees['stt'] == pytest.approx(10100*stt)
    assert fees['exchange_fees'] == pytest.approx(turnover*exchange)
    assert fees['ipft'] == pytest.approx(turnover*ipft)
    assert fees['exchange_fees']+fees['ipft'] == pytest.approx(turnover*.0000183)
    assert result['fee_schedule']['tariff_status'] == 'OFFICIAL_STANDARD_TARIFF'


@pytest.mark.parametrize('side,buy,sell', [('LONG', 100, 101), ('SHORT', 101, 100)])
def test_turnover_sides_brokerage_cap_and_gst(side, buy, sell):
    cost = FuturesCosts(slippage_bps=0)
    result = cost.round_trip(100, 101, 1000, side, '2026-10-08')
    fees = result['cost_breakdown']
    assert fees['brokerage'] == 40
    assert fees['stamp_duty'] == pytest.approx(buy*1000*.00002)
    assert fees['stt'] == pytest.approx(sell*1000*.0005)
    assert fees['gst'] == pytest.approx(sum(fees[key] for key in
         ('brokerage', 'exchange_fees', 'sebi_fees', 'ipft'))*.18)
    assert result['total_costs'] == pytest.approx(sum(fees.values()))
    small = cost.round_trip(100, 101, 100, side, '2026-10-08')
    assert small['cost_breakdown']['brokerage'] == pytest.approx(20100*.0003)


def test_depth_entry_spread_is_not_charged_twice():
    cost = FuturesCosts()
    base = cost.round_trip(100, 100.3, 500, 'LONG', '2026-10-08')
    result = cost.round_trip(100, 100.3, 500, 'LONG', '2026-10-08', exit_spread_bps=4)
    assert result['cost_breakdown']['spread'] == pytest.approx(100.3*500*4/20000)
    assert result['cost_breakdown']['slippage'] == base['cost_breakdown']['slippage']
    assert result['total_costs']-base['total_costs'] == pytest.approx(result['cost_breakdown']['spread'])
    assert result['execution_cost_calibration'] == 'PROVISIONAL'
    with pytest.raises(ValueError, match='not both'):
        cost.round_trip(100, 101, 500, 'LONG', '2026-10-08', 2, exit_spread_bps=4)


@pytest.mark.parametrize('bad', [-1, float('nan'), float('inf')])
def test_invalid_execution_cost_inputs_fail_closed(bad):
    with pytest.raises(ValueError):
        FuturesCosts().round_trip(100, 101, 100, 'LONG', '2026-10-08', exit_spread_bps=bad)


def test_unsupported_history_date_and_custom_tariff_are_explicit():
    with pytest.raises(ValueError, match='2024-10-01'):
        FuturesCosts().round_trip(100, 101, 100, 'LONG', '2024-09-30')
    cost = replace(FuturesCosts(), brokerage_cap=40)
    assert cost.round_trip(100, 101, 1000, 'LONG', '2026-10-08')['fee_schedule']['tariff_status'] == 'CUSTOM_ASSUMPTIONS'
    assert all(source['retrieved_on_ist'] == '2026-10-09' for source in fee_schedule()['sources'])


def audit_module():
    path = Path(__file__).resolve().parents[1]/'scripts'/'audit_futures_costs.py'
    spec = importlib.util.spec_from_file_location('audit_futures_costs', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_no_evidence_is_unknown_not_a_fake_calibration():
    result = audit_module().audit()
    assert result['read_only'] is True
    assert result['calibration_status'] == 'UNKNOWN'
    assert result['slippage_calibration']['median_observed_adverse_bps'] is None
    assert result['slippage_calibration']['active_model_bps_per_leg'] == 2


def test_observed_fills_require_contemporaneous_executable_reference_and_never_mutate_model():
    row = {'transaction_type': 'BUY', 'fill_price': 100.02, 'executable_reference_price': 100,
           'fill_timestamp': '2026-10-08T10:00:01+05:30', 'reference_timestamp': '2026-10-08T10:00:00+05:30'}
    result = audit_module().audit({'fill_observations': [row, {**row, 'reference_timestamp': '2026-10-08T09:59:00+05:30'}]})
    assert len(result['fill_observations']) == 1
    assert len(result['failures']) == 1
    assert result['slippage_calibration']['median_observed_adverse_bps'] == pytest.approx(2)
    assert result['slippage_calibration']['active_model_status'] == 'PROVISIONAL'
    assert FuturesCosts().slippage_bps == 2


def test_actual_charge_differences_do_not_change_tariff():
    result = audit_module().audit({'round_trips': [{'entry': 100, 'exit': 101, 'quantity': 100,
        'side': 'LONG', 'trade_date': '2026-10-08', 'observed_charges': {'brokerage': 6.03}}]})
    comparison = result['contract_note_comparisons'][0]
    assert comparison['status'] == 'COMPARISON_ONLY'
    assert comparison['differences']['brokerage']['observed_minus_modeled'] == pytest.approx(0)
    assert result['configured_assumptions']['brokerage_cap'] == 20


@pytest.mark.parametrize('reference',[None, 0, -1, float('nan'), 'unavailable'])
def test_missing_equity_reference_never_changes_futures_execution_basis(reference):
    plan=trade_plan(205,100,'SHORT',config=FuturesScanConfig(),costs=FuturesCosts(),
                    risk_budget=1000,trade_date='2026-10-08',movement_reference_price=reference)
    assert plan['target']==pytest.approx(204.385)
    assert plan['stop_loss']==pytest.approx(205.41)
    assert plan['underlying_entry'] is None
