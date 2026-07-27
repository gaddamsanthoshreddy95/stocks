import pandas as pd
import pytest

from src.data_provider.kite_data_provider import KiteDataProvider
from src.indicators.volume import VolumeIndicator
from src.trade_plan.trade_plan import TradePlanEngine


def _volume_frame(live_volume=10_000, progress=.1):
    index = pd.date_range("2026-06-01", periods=21, freq="B")
    frame = pd.DataFrame({"Volume": [100_000] * 20 + [live_volume]}, index=index)
    frame.loc[index[-1], "IS_LIVE_CANDLE"] = True
    frame.loc[index[-1], "LIVE_SESSION_PROGRESS"] = progress
    return frame


def test_live_rvol_compares_accumulated_volume_with_expected_volume_so_far():
    result = VolumeIndicator.calculate(_volume_frame())

    assert result.iloc[-1]["AVG_VOLUME"] == pytest.approx(100_000)
    assert result.iloc[-1]["RVOL"] == pytest.approx(1.0)
    assert result.iloc[-2]["RVOL"] == pytest.approx(1.0)


def test_completed_daily_candle_keeps_full_day_rvol_calculation():
    frame = _volume_frame()
    frame.loc[frame.index[-1], "IS_LIVE_CANDLE"] = False

    result = VolumeIndicator.calculate(frame)

    assert result.iloc[-1]["AVG_VOLUME"] == pytest.approx(95_500)
    assert result.iloc[-1]["RVOL"] == pytest.approx(10_000 / 95_500)


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        ("2026-07-27 09:15:00+05:30", 1 / 375),
        ("2026-07-27 12:22:30+05:30", .5),
        ("2026-07-27 15:30:00+05:30", 1.0),
        ("2026-07-27 16:00:00+05:30", 1.0),
    ],
)
def test_nse_session_progress(timestamp, expected):
    assert KiteDataProvider._live_session_progress(pd.Timestamp(timestamp)) == pytest.approx(expected)


def test_trade_plan_exposes_rejection_and_confirmed_breakout_scenarios():
    plan = TradePlanEngine.generate({
        "current_price": 100,
        "support": 95,
        "resistance": 103,
        "next_resistance": 115,
        "resistance_levels": [103, 115],
        "atr": 5,
        "breakout_probability": 70,
    })

    assert plan.scenarios["rejection_target"]["target"] == 103
    assert plan.scenarios["confirmed_breakout"]["trigger"] == 103
    assert plan.scenarios["confirmed_breakout"]["target"] == 115
    assert plan.scenarios["confirmed_breakout"]["status"] == "ACTIONABLE"
