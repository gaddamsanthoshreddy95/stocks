import pandas as pd

from src.analysis.candle_utils import atr_series
from src.analysis.price_action_engine import PriceActionEngine
from src.config.trading_config import TechnicalSetupConfig
from src.multi_timeframe.alignment import MultiTimeframeAlignment
from src.backtesting.setup_backtester import SetupBacktester
from src.options.technical_validator import TechnicalOptionValidator
from src.risk.trade_levels import TradeLevelEngine
from src.scoring.setup_score_engine import SetupScoreEngine


def frame(rows):
    return pd.DataFrame(rows, index=pd.date_range("2025-01-01", periods=len(rows), freq="D"))


def test_price_action_handles_insufficient_and_nan_data():
    tiny = frame([{"Open": 1, "High": 2, "Low": 0, "Close": 1}])
    assert PriceActionEngine().analyze(tiny)["status"] == "INSUFFICIENT_DATA"
    bad = frame([{"Open": 1, "High": 2, "Low": 0, "Close": 1},
                 {"Open": 1, "High": 2, "Low": 0, "Close": float("nan")},
                 {"Open": 1, "High": 1, "Low": 1, "Close": 1}])
    result = PriceActionEngine().analyze(bad)
    assert result["status"] == "OK"


def test_atr_is_prefix_stable_no_lookahead():
    data = frame([{"Open": i, "High": i + 2, "Low": i - 1, "Close": i + 1} for i in range(1, 30)])
    prefix = atr_series(data.iloc[:20], 14)
    full = atr_series(data, 14)
    pd.testing.assert_series_equal(prefix, full.iloc[:20])


def test_multi_timeframe_uses_only_available_completed_candle():
    lower = pd.DataFrame({"Close": [1, 2, 3]}, index=pd.to_datetime(
        ["2025-01-02 09:00", "2025-01-02 12:00", "2025-01-03 09:00"]))
    higher = pd.DataFrame({"Trend": ["OLD", "NEW"]}, index=pd.to_datetime(
        ["2025-01-01 16:00", "2025-01-02 16:00"]))
    aligned = MultiTimeframeAlignment.align(lower, higher)
    assert list(aligned["HTF_Trend"]) == ["OLD", "OLD", "NEW"]


def test_higher_timeframe_resampling_excludes_unfinished_bucket():
    data = pd.DataFrame({
        "Open": [1, 2, 3], "High": [2, 3, 4], "Low": [0, 1, 2],
        "Close": [1.5, 2.5, 3.5], "Volume": [10, 20, 30],
    }, index=pd.to_datetime(["2025-01-01", "2025-01-02", "2025-01-06"]))
    result = MultiTimeframeAlignment.resample_completed(
        data, "W-FRI", pd.Timestamp("2025-01-06"))
    assert list(result.index) == [pd.Timestamp("2025-01-03")]
    assert result.iloc[0]["Volume"] == 30


def test_trade_levels_bullish_and_bearish_are_symmetric():
    engine = TradeLevelEngine(TechnicalSetupConfig(min_risk_reward=1))
    bull = engine.calculate("BULLISH", 100, 5, stop_method="ATR", target_method="RR")
    bear = engine.calculate("BEARISH", 100, 5, stop_method="ATR", target_method="RR")
    assert bull["valid"] and bear["valid"]
    assert bull["stop_loss"] < 100 < bear["stop_loss"]
    assert bull["risk_reward"] == bear["risk_reward"]


def test_every_stop_and_target_method_returns_auditable_result():
    engine = TradeLevelEngine(TechnicalSetupConfig(min_risk_reward=.5, max_stop_atr=10))
    zone = {"lower": 95, "upper": 96}
    confirmation = {"low": 97, "high": 103}
    stop_methods = ("CONFIRMATION_CANDLE", "SWING", "ZONE", "ATR", "STRUCTURE", "PATTERN")
    for method in stop_methods:
        result = engine.calculate(
            "BULLISH", 100, 5, confirmation_candle=confirmation, swing=96,
            zone=zone, pattern_invalidation=94, stop_method=method, target_method="RR")
        assert result["valid"] and result["stop_method"] == method
    target_arguments = {
        "ZONES": {"opposing_zones": [{"midpoint": 110}]},
        "PREVIOUS_SWING": {"previous_swing_target": 108},
        "MEASURED_MOVE": {"measured_move": 8},
        "NECKLINE_PROJECTION": {"neckline_projection": 109},
        "ATR_MULTIPLES": {},
        "RR": {},
    }
    for method, kwargs in target_arguments.items():
        result = engine.calculate("BULLISH", 100, 5, stop_method="ATR",
                                  target_method=method, **kwargs)
        assert result["valid"] and result["targets"]


def test_score_penalises_correlated_components():
    components = {name: 100 for name in TechnicalSetupConfig().score_weights}
    result = SetupScoreEngine().score(components)
    assert result["score"] < 100
    assert result["correlation_penalty"] > 0
    assert result["category"] == "HIGH_CONFIDENCE"


def test_option_overlay_warns_when_put_strike_is_inside_support():
    technical = {"zones": [{"type": "SUPPORT", "midpoint": 95, "lower": 94, "upper": 96,
                            "strength_score": 80}], "breakout": None, "retest": None}
    result = TechnicalOptionValidator.validate("BULLISH", 95, 100, technical)
    assert "SHORT_PUT_STRIKE_NOT_BELOW_SUPPORT_ZONE" in result["warnings"]


def test_zero_range_and_no_volume_are_safe():
    data = frame([
        {"Open": 100, "High": 100, "Low": 100, "Close": 100},
        {"Open": 100, "High": 101, "Low": 99, "Close": 100},
        {"Open": 100, "High": 100, "Low": 100, "Close": 100},
    ])
    result = PriceActionEngine().analyze(data)
    assert result["status"] == "OK"


def test_setup_backtester_gap_stop_and_ambiguous_bar_policy():
    data = frame([
        {"Open": 100, "High": 101, "Low": 99, "Close": 100},
        {"Open": 100, "High": 101, "Low": 99, "Close": 100},
        {"Open": 100, "High": 106, "Low": 94, "Close": 102},
        {"Open": 93, "High": 94, "Low": 90, "Close": 91},
    ])

    def signal(prefix):
        if len(prefix) == 3:
            return [{"direction": "BULLISH", "confirmation_timestamp": prefix.index[-1],
                     "stop_loss": 95, "targets": [105], "risk_reward": [1]}]
        return []

    result = SetupBacktester(0, 0, "STOP_FIRST").run(data, signal)
    assert result["metrics"]["number_of_trades"] == 1
    assert result["trades"][0]["stop_hit"]
