import pandas as pd

from src.workflow.stabilized_setup import assess_stabilized_setup


def _bars() -> pd.DataFrame:
    index = pd.date_range("2026-07-27 09:15", periods=12, freq="15min")
    rows = []
    for i in range(8):
        base = 104.0 + i * .05
        rows.append([base, base + 1.0, base - 1.0, base + .2, 1000])
    for i, volume in enumerate((750, 700, 650, 850)):
        base = 104.2 + i * .18
        rows.append([base, base + .45, base - .35, base + .25, volume])
    return pd.DataFrame(rows, index=index,
                        columns=["Open", "High", "Low", "Close", "Volume"])


def test_controlled_base_near_trigger_is_ready():
    result = assess_stabilized_setup(
        technical={
            "current_price": 104.95, "ema20": 102.5, "atr": 5,
            "rsi": 61, "relative_volume": .9,
        },
        levels={
            "entry": 104.95, "stop_loss": 101.5, "target_1": 112,
            "target_2": 118, "resistance": 105.25,
        },
        intraday=_bars(),
        market_alignment="ALIGNED",
        sector_score=75,
    )

    assert result["available"] is True
    assert result["state"] == "READY_NEAR_TRIGGER"
    assert result["score"] >= 70
    assert result["remaining_risk_reward"] >= 1.5
    assert result["checks"]["higher_lows"] is True
    assert result["checks"]["range_contraction"] is True


def test_large_completed_move_is_not_stabilized():
    bars = _bars()
    result = assess_stabilized_setup(
        technical={
            "current_price": 110, "ema20": 101, "atr": 4,
            "rsi": 78, "relative_volume": 3,
        },
        levels={
            "entry": 110, "stop_loss": 103, "target_1": 114,
            "target_2": 118, "resistance": 111,
        },
        intraday=bars,
        market_alignment="ALIGNED",
        sector_score=80,
    )

    assert result["state"] == "EXTENDED_NOT_STABILIZED"
    assert result["checks"]["controlled_intraday_move"] is False
    assert result["checks"]["normal_ema_extension"] is False
