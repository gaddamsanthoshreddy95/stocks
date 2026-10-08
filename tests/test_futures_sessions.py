"""Synthetic exchange-clock regressions; no market requests or broker actions."""
from dataclasses import replace

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from src.futures.backtest import FuturesIntradayBacktester
from src.futures.config import FuturesScanConfig
from src.futures.data_recovery import completed_frame
from src.futures.scoring import prepare
from src.futures.sessions import (
    CALCULATION_VERSION, completed_session_check, entry_window,
    expected_completed_bar, normalise_candles,
)


def candles(index):
    return pd.DataFrame({'Open': 100., 'High': 100.05, 'Low': 99.95,
                         'Close': 100., 'Volume': 100}, index=index)


def stamp(value):
    return pd.Timestamp(value, tz='Asia/Kolkata')


@pytest.fixture(autouse=True)
def calendar(monkeypatch):
    monkeypatch.delenv('MARKET_HOLIDAYS_IST', raising=False)


def test_invalid_session_rows_are_removed_without_mutating_source():
    index = pd.DatetimeIndex([stamp(value) for value in (
        '2026-10-08 09:10', '2026-10-08 09:15', '2026-10-08 09:17',
        '2026-10-08 15:25', '2026-10-08 15:30', '2026-10-08 15:35',
        '2026-10-10 09:15', '2026-10-11 09:15')])
    raw = candles(index)
    original = raw.copy(deep=True)
    result = normalise_candles(raw, '5minute')
    assert list(result.index) == [stamp('2026-10-08 09:15'), stamp('2026-10-08 15:25')]
    assert result.attrs['session_normalization']['excluded_rows'] == 6
    assert result.attrs['session_normalization']['version'] == CALCULATION_VERSION
    assert_frame_equal(raw, original)


def test_holiday_is_excluded_for_daily_and_five_minute_data(monkeypatch):
    monkeypatch.setenv('MARKET_HOLIDAYS_IST', '2026-10-07')
    daily = candles(pd.date_range('2026-10-06', periods=3, freq='D', tz='Asia/Kolkata'))
    intra = daily.copy()
    intra.index += pd.Timedelta(hours=9, minutes=15)
    assert [x.day for x in normalise_candles(daily, 'day').index] == [6, 8]
    assert [x.day for x in normalise_candles(intra, '5minute').index] == [6, 8]
    assert expected_completed_bar(stamp('2026-10-07 13:00')) is None
    assert entry_window(stamp('2026-10-07 13:00'), FuturesScanConfig())['status'] == 'FAIL'


def test_timezone_conversion_precedes_session_grid_validation():
    raw = candles(pd.date_range('2026-10-08 03:45', periods=2, freq='5min', tz='UTC'))
    result = normalise_candles(raw, '5minute')
    assert result.index[0] == stamp('2026-10-08 09:15')
    assert len(result) == 2


def test_duplicate_timestamps_are_not_silently_replaced():
    raw = candles(pd.DatetimeIndex([stamp('2026-10-08 09:15')] * 2))
    with pytest.raises(ValueError, match='DUPLICATED'):
        normalise_candles(raw, '5minute')
    with pytest.raises(ValueError, match='DUPLICATED'):
        completed_frame(raw, stamp('2026-10-08 16:00'), '5minute')


def test_forming_and_future_bars_are_excluded_including_boundary():
    raw = candles(pd.date_range('2026-10-08 09:15', periods=4, freq='5min', tz='Asia/Kolkata'))
    for now in ('2026-10-08 09:25', '2026-10-08 09:29:59'):
        result = normalise_candles(raw, '5minute', stamp(now))
        assert list(result.index) == list(raw.index[:2])
        assert result.attrs['session_normalization']['incomplete_rows_excluded'] == 2
    assert len(normalise_candles(raw, '5minute', stamp('2026-10-08 09:30'))) == 3


def test_daily_live_discovery_requires_explicit_marker_and_permission():
    daily = candles(pd.date_range('2026-10-07', periods=2, freq='D', tz='Asia/Kolkata'))
    now = stamp('2026-10-08 12:00')
    assert len(normalise_candles(daily, 'day', now, allow_live_daily=True)) == 1
    daily['IS_LIVE_CANDLE'] = [False, True]
    assert len(normalise_candles(daily, 'day', now)) == 1
    live = normalise_candles(daily, 'day', now, allow_live_daily=True)
    assert len(live) == 2 and bool(live.iloc[-1].IS_LIVE_CANDLE)
    assert len(normalise_candles(daily, 'day', stamp('2026-10-08 15:30'))) == 2
    recovered = completed_frame(daily, now, 'day')
    assert len(recovered) == 1
    assert 'IS_LIVE_CANDLE' not in recovered


@pytest.mark.parametrize('now,expected', [
    ('2026-10-08 09:19:59', None),
    ('2026-10-08 09:20', '2026-10-08 09:15'),
    ('2026-10-08 09:24:59', '2026-10-08 09:15'),
    ('2026-10-08 09:25', '2026-10-08 09:20'),
    ('2026-10-08 15:29:59', '2026-10-08 15:20'),
    ('2026-10-08 15:30', '2026-10-08 15:25'),
    ('2026-10-08 20:00', '2026-10-08 15:25'),
    ('2026-10-10 13:00', None),
])
def test_expected_latest_completed_bar_has_exact_boundaries(now, expected):
    assert expected_completed_bar(stamp(now)) == (stamp(expected) if expected else None)


def test_latest_required_bar_becomes_missing_at_new_boundary():
    data = candles(pd.date_range('2026-10-08 09:15', periods=2, freq='5min', tz='Asia/Kolkata'))
    assert completed_session_check(data, stamp('2026-10-08 09:29:59'))['status'] == 'PASS'
    result = completed_session_check(data, stamp('2026-10-08 09:30'))
    assert result['status'] == 'UNKNOWN'
    assert result['missing_candles'] == [stamp('2026-10-08 09:25').isoformat()]


def test_internal_gap_cannot_be_hidden_by_fresh_last_bar():
    data = candles(pd.date_range('2026-10-08 09:15', periods=4, freq='5min', tz='Asia/Kolkata'))
    result = completed_session_check(data.drop(data.index[1]), stamp('2026-10-08 09:35'))
    assert result['status'] == 'UNKNOWN'
    assert result['candle_timestamp'] == data.index[-1].isoformat()
    assert result['missing_candles'] == [data.index[1].isoformat()]


def test_future_bars_cannot_fill_current_session_gaps():
    data = candles(pd.DatetimeIndex([stamp('2026-10-08 09:15'), stamp('2026-10-08 09:30'),
                                     stamp('2026-10-09 09:20')]))
    result = completed_session_check(data, stamp('2026-10-08 09:25'))
    assert result['status'] == 'UNKNOWN'
    assert result['missing_candles'] == [stamp('2026-10-08 09:20').isoformat()]


def test_scoring_indicators_match_clean_session_when_feed_has_after_hours_extreme():
    data = candles(pd.date_range('2026-10-07 09:15', periods=75, freq='5min', tz='Asia/Kolkata'))
    data = pd.concat([data, candles(pd.date_range('2026-10-08 09:15', periods=5, freq='5min', tz='Asia/Kolkata'))])
    invalid = candles(pd.DatetimeIndex([stamp('2026-10-07 15:30'), stamp('2026-10-07 15:35')]))
    invalid[['Open', 'High', 'Low', 'Close']] *= 10
    baseline = prepare(data)
    result = prepare(pd.concat([data, invalid]).sort_index())
    columns = ['EMA9', 'EMA21', 'ATR', 'ADX', 'RSI', 'MACD_HISTOGRAM', 'RVOL']
    assert_frame_equal(result[columns], baseline[columns])


@pytest.mark.parametrize('clock,can_enter,warning', [
    ('09:19:59', False, False), ('09:20', True, False),
    ('15:09:59', True, False), ('15:10', True, True),
    ('15:14:59', True, True), ('15:15', False, True), ('15:20', False, True),
])
def test_explicit_entry_cutoff_and_manual_exit_warning(clock, can_enter, warning):
    result = entry_window(stamp('2026-10-08 ' + clock), FuturesScanConfig())
    assert (result['status'] == 'PASS') == can_enter
    assert result['manual_exit_warning_active'] == warning
    assert result['automatic_square_off'] is False
    assert result['entry_cutoff'] == stamp('2026-10-08 15:15').isoformat()
    assert result['manual_exit_deadline'] == stamp('2026-10-08 15:20').isoformat()
    assert 'Manually close' in result['manual_exit_warning']


def test_entry_cutoff_is_configurable_without_changing_forced_exit():
    config = replace(FuturesScanConfig(), entry_cutoff_hour=14, entry_cutoff_minute=30)
    before = entry_window(stamp('2026-10-08 14:29:59'), config)
    after = entry_window(stamp('2026-10-08 14:30'), config)
    assert before['status'] == 'PASS' and after['status'] == 'FAIL'
    assert after['manual_exit_deadline'] == stamp('2026-10-08 15:20').isoformat()


def test_backtest_next_bar_entry_respects_entry_cutoff_and_still_exits_at_1520():
    data = pd.concat([candles(pd.date_range(day+pd.Timedelta(hours=9, minutes=15), periods=75, freq='5min'))
                      for day in pd.date_range('2026-10-05', periods=4, freq='B', tz='Asia/Kolkata')])
    def late_signals(prefix):
        if prefix.index[-1].strftime('%H:%M') in ('15:05', '15:10'):
            return [{'side': 'LONG', 'setup_type': 'TEST'}]
        return []
    result = FuturesIntradayBacktester().run(data, 500, signal_factory=late_signals)
    assert result['trades']
    assert all(pd.Timestamp(t['entry_timestamp']).strftime('%H:%M') == '15:10' for t in result['trades'])
    assert all(pd.Timestamp(t['exit_timestamp']).strftime('%H:%M') == '15:20' for t in result['trades'])
    def too_late_signals(prefix):
        return [{'side': 'LONG', 'setup_type': 'TEST'}] if prefix.index[-1].strftime('%H:%M') == '15:10' else []
    assert FuturesIntradayBacktester().run(data, 500, signal_factory=too_late_signals)['trades'] == []


@pytest.mark.parametrize('side,sign', [('LONG', 1), ('SHORT', -1)])
def test_underlying_context_never_changes_futures_barriers_or_outcomes(side, sign):
    data = candles(pd.date_range('2026-10-08 09:15', periods=75, freq='5min', tz='Asia/Kolkata'))
    underlying = data.copy()
    underlying[['Open', 'High', 'Low', 'Close']] *= 5
    simulator = FuturesIntradayBacktester()
    baseline = simulator.simulate(data, 1, side, 'TEST', 500)
    with_context = simulator.simulate(data, 1, side, 'TEST', 500, underlying=underlying)
    assert with_context == baseline
    assert with_context['movement_basis'] == 'FUTURES'
    assert with_context['target'] == pytest.approx(100*(1+sign*.003))
    assert with_context['stop_loss'] == pytest.approx(100*(1-sign*.002))


def test_direct_simulation_cannot_silently_shift_invalid_positional_entry():
    data = candles(pd.date_range('2026-10-08 09:15', periods=77, freq='5min', tz='Asia/Kolkata'))
    with pytest.raises(ValueError, match='NORMALIZED_SESSION'):
        FuturesIntradayBacktester().simulate(data, 1, 'LONG', 'TEST', 500)


@pytest.mark.parametrize('interval,clock', [('day', '00:00'), ('5minute', '09:15')])
def test_nanosecond_offset_candles_are_not_on_the_exchange_grid(interval, clock):
    valid = stamp('2026-10-08 ' + clock)
    raw = candles(pd.DatetimeIndex([valid, valid+pd.Timedelta(nanoseconds=1)]))
    result = normalise_candles(raw, interval)
    assert list(result.index) == [valid]
    assert result.attrs['session_normalization']['excluded_rows'] == 1


@pytest.mark.parametrize('changes', [
    {'entry_cutoff_hour': 14, 'entry_cutoff_minute': 60},
    {'entry_cutoff_hour': 14, 'entry_cutoff_minute': 75},
    {'intraday_exit_hour': 14, 'intraday_exit_minute': 60},
    {'entry_cutoff_hour': 14.5},
    {'entry_cutoff_minute': 14.5},
])
def test_invalid_clock_components_do_not_roll_into_another_hour(changes):
    with pytest.raises(ValueError):
        replace(FuturesScanConfig(), **changes)


@pytest.mark.parametrize('entry_time', ['09:15', '15:15', '15:20', '15:25'])
def test_direct_simulation_also_enforces_entry_window(entry_time):
    data = candles(pd.date_range('2026-10-08 09:15', periods=75, freq='5min', tz='Asia/Kolkata'))
    start = data.index.get_loc(stamp('2026-10-08 ' + entry_time))
    assert FuturesIntradayBacktester().simulate(data, start, 'LONG', 'TEST', 500) is None
