from dataclasses import replace
import numpy as np
import pandas as pd
import pytest

from src.futures.backtest import FuturesIntradayBacktester, metrics
from src.futures.config import FuturesScanConfig
from src.futures.costs import FuturesCosts


def frame():
    index = pd.date_range('2026-10-08 09:15', periods=75, freq='5min', tz='Asia/Kolkata')
    return pd.DataFrame({'Open': 100., 'High': 100.05, 'Low': 99.95, 'Close': 100., 'Volume': 100}, index=index)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_target_and_stop_same_candle_are_stop_first(side):
    candles = frame()
    candles.iloc[1, candles.columns.get_loc('High')] = 101
    candles.iloc[1, candles.columns.get_loc('Low')] = 99
    result = FuturesIntradayBacktester().simulate(candles, 1, side, 'TEST', 500)
    assert result['outcome'] == 'STOP_FIRST'
    assert result['net_pnl'] < 0


@pytest.mark.parametrize('side,target', [('LONG', 100.3), ('SHORT', 99.7)])
def test_target_first_uses_actual_contract_entry(side, target):
    candles = frame()
    column = 'High' if side == 'LONG' else 'Low'
    candles.iloc[2, candles.columns.get_loc(column)] = target
    result = FuturesIntradayBacktester().simulate(candles, 1, side, 'TEST', 500)
    assert result['outcome'] == 'TARGET_FIRST'
    assert result['entry'] == 100
    assert result['gross_pnl'] == pytest.approx(150)
    assert result['net_pnl'] < result['gross_pnl']


def test_neither_exits_intraday_without_carrying_overnight():
    candles = frame()
    result = FuturesIntradayBacktester().simulate(candles, 1, 'SHORT', 'TEST', 500)
    assert result['outcome'] == 'INTRADAY_EXIT'
    assert pd.Timestamp(result['exit_timestamp']).time().isoformat() == '15:20:00'
    assert result['gross_pnl'] == 0
    assert result['net_pnl'] < 0


def test_incomplete_sessions_and_missing_bars_do_not_invent_outcomes():
    candles = frame()
    assert FuturesIntradayBacktester().simulate(candles.iloc[:10], 1, 'SHORT', 'TEST', 500) is None
    assert FuturesIntradayBacktester().simulate(candles.drop(candles.index[3]), 1, 'SHORT', 'TEST', 500) is None


def test_gap_through_stop_has_worse_fill_than_stop_price():
    candles = frame()
    candles.iloc[2, candles.columns.get_loc('Open')] = 101
    candles.iloc[2, candles.columns.get_loc('High')] = 101.1
    result = FuturesIntradayBacktester().simulate(candles, 1, 'SHORT', 'TEST', 500)
    assert result['exit'] == 101
    assert result['gross_pnl'] == -500


def multi_session():
    frames = []
    for day in pd.date_range('2026-10-01', periods=8, freq='B'):
        data = frame()
        data.index = pd.date_range(day.strftime('%Y-%m-%d')+' 09:15', periods=75, freq='5min', tz='Asia/Kolkata')
        frames.append(data)
    return pd.concat(frames)


def test_prefix_only_signals_and_next_bar_execution_independent_directions():
    candles = multi_session()
    seen = []
    def signals(prefix):
        seen.append(prefix.index[-1])
        # A callback sees no future candle; opening price for entry comes from the next bar.
        return [{'side': side, 'setup_type': 'TEST', 'confirmation_timestamp': prefix.index[-1]}
                for side in ('LONG', 'SHORT')]
    result = FuturesIntradayBacktester().run(candles, 500, signal_factory=signals)
    assert seen
    assert {trade['side'] for trade in result['trades']} == {'LONG', 'SHORT'}
    for trade in result['trades']:
        assert pd.Timestamp(trade['entry_timestamp']) == pd.Timestamp(trade['signal_timestamp'])+pd.Timedelta(minutes=5)
    assert any(trade['partition'] == 'OUT_OF_SAMPLE' for trade in result['trades'])
    assert result['groups']['side:SHORT']['target_first_percent'] is None
    assert result['validation_status'] == 'OUT_OF_SAMPLE_EVALUATED'


def test_future_changes_do_not_change_earlier_signals_or_outcomes():
    candles = multi_session()
    def signals(prefix):
        return [{'side': 'SHORT', 'setup_type': 'TEST'}] if prefix.index[-1].hour == 10 else []
    baseline = FuturesIntradayBacktester().run(candles, 500, signal_factory=signals)
    changed = candles.copy()
    final_day = changed.index[-1].date()
    changed.loc[changed.index.date == final_day, ['Open', 'High', 'Low', 'Close']] *= 2
    altered = FuturesIntradayBacktester().run(changed, 500, signal_factory=signals)
    earlier = lambda result: [trade for trade in result['trades'] if pd.Timestamp(trade['entry_timestamp']).date() < final_day]
    assert earlier(baseline) == earlier(altered)


def test_drawdown_includes_loss_from_initial_zero_equity():
    candles = frame()
    trade = FuturesIntradayBacktester().simulate(candles, 1, 'SHORT', 'TEST', 500)
    result = metrics([trade], 30)
    assert result['maximum_drawdown'] == pytest.approx(abs(trade['net_pnl']))
    assert result['target_first_percent'] is None
    assert result['neither_percent'] == 100


def test_empty_signals_have_no_probability_or_profit_factor():
    result = metrics([], 30)
    assert result['target_first_percent'] is None
    assert result['profit_factor'] is None


def test_mixed_expiry_history_is_rejected():
    candles = multi_session()
    candles['tradingsymbol'] = ['TEST26OCTFUT'] * (len(candles)-1) + ['TEST26NOVFUT']
    with pytest.raises(ValueError, match='mixed expiry'):
        FuturesIntradayBacktester().run(candles, 500)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_default_strategy_discovers_each_direction_independently(side):
    candles = multi_session()
    sign = 1 if side == 'LONG' else -1
    values = 120+sign*np.arange(len(candles))*.005
    candles['Close'] = values
    candles['Open'] = values-sign*.1
    candles['High'] = values+(.01 if side == 'LONG' else .2)
    candles['Low'] = values-(.2 if side == 'LONG' else .01)
    daily_index = pd.date_range('2026-08-01', '2026-10-20', freq='B', tz='Asia/Kolkata')
    daily = pd.DataFrame({'Open': 120., 'High': 121., 'Low': 119., 'Close': 120., 'Volume': 100}, index=daily_index)
    # Deliberately disable the new oversold guard in this legacy monotonic-fixture
    # test; its hypothesis is independent-direction discovery, not timely entry.
    config = replace(FuturesScanConfig(), short_oversold_rsi=0)
    result = FuturesIntradayBacktester(config).run(candles, 500, daily_history=daily)
    assert any(trade['side'] == side for trade in result['trades'])


def test_short_trigger_must_trade_before_backtest_enters():
    candles = frame()
    backtest = FuturesIntradayBacktester()
    assert backtest.simulate(candles, 1, 'SHORT', 'TEST', 500, entry_trigger=99.8) is None
    result = backtest.simulate(candles, 1, 'SHORT', 'TEST', 500, entry_trigger=99.98, invalidation=100.05)
    assert result['entry'] == 99.98
    assert result['entry_fill_basis'] == 'TRIGGER_TOUCHED_IN_NEXT_BAR'


def test_short_gap_below_trigger_or_uncovered_invalidation_is_not_chased():
    candles = frame()
    candles.iloc[1, candles.columns.get_loc('Open')] = 99
    assert FuturesIntradayBacktester().simulate(candles, 1, 'SHORT', 'TEST', 500, entry_trigger=100) is None
    candles = frame()
    assert FuturesIntradayBacktester().simulate(candles, 1, 'SHORT', 'TEST', 500,
        entry_trigger=100, invalidation=101) is None
