import pandas as pd
import pytest

from src.analysis.reversal_detector import ReversalDetector
from src.breakout.breakout_detector import BreakoutDetector
from src.breakout.retest_detector import RetestDetector
from src.candlestick.triple_patterns import TripleCandlePatternDetector
from src.config.trading_config import TechnicalSetupConfig
from src.market_structure.structure_detector import MarketStructureDetector
from src.market_structure.support_resistance import SupportResistanceEngine
from src.strategy.setup_entry_engine import SetupEntryEngine


CFG = TechnicalSetupConfig(
    pivot_lookback=1, trend_lookback=5, min_pattern_body_ratio=.25,
    long_body_atr=.35, gap_tolerance_atr=.05, max_wick_body_ratio=.8,
    breakout_extended_atr=3, min_relative_volume=1.1, min_risk_reward=1,
)


def history(closes):
    return [{"Open": close + 1, "High": close + 2, "Low": close - 2,
             "Close": close, "Volume": 100} for close in closes]


def mirror(rows, ceiling=250):
    return [{"Open": ceiling - row["Open"], "High": ceiling - row["Low"],
             "Low": ceiling - row["High"], "Close": ceiling - row["Close"],
             "Volume": row.get("Volume", 100)} for row in rows]


PATTERNS = {
    "MORNING DOJI STAR": history([120, 116, 112, 108, 104]) + [
        {"Open": 105, "High": 106, "Low": 99, "Close": 100, "Volume": 100},
        {"Open": 99.8, "High": 100.2, "Low": 98, "Close": 99.9, "Volume": 90},
        {"Open": 100, "High": 105, "Low": 99, "Close": 104, "Volume": 140}],
    "BULLISH ABANDONED BABY": history([120, 116, 112, 108, 104]) + [
        {"Open": 105, "High": 106, "Low": 100, "Close": 101, "Volume": 100},
        {"Open": 98, "High": 98.2, "Low": 97.5, "Close": 98.05, "Volume": 90},
        {"Open": 99, "High": 104, "Low": 98.8, "Close": 103.5, "Volume": 140}],
    "THREE WHITE SOLDIERS": history([120, 116, 112, 108, 104]) + [
        {"Open": 103, "High": 107, "Low": 102.8, "Close": 106, "Volume": 100},
        {"Open": 105, "High": 109, "Low": 104.8, "Close": 108, "Volume": 120},
        {"Open": 107, "High": 111, "Low": 106.8, "Close": 110, "Volume": 140}],
    "BEARISH THREE-LINE STRIKE": history([130, 126, 122, 118, 114]) + [
        {"Open": 115, "High": 116, "Low": 111, "Close": 112, "Volume": 100},
        {"Open": 113, "High": 114, "Low": 109, "Close": 110, "Volume": 110},
        {"Open": 111, "High": 112, "Low": 107, "Close": 108, "Volume": 120},
        {"Open": 107.8, "High": 117, "Low": 107, "Close": 116, "Volume": 160}],
}


@pytest.mark.parametrize("name,rows", PATTERNS.items())
def test_every_new_bullish_pattern(name, rows):
    matches = TripleCandlePatternDetector(CFG).detect_all(pd.DataFrame(rows))
    assert name in [match["pattern"] for match in matches]
    result = next(match for match in matches if match["pattern"] == name)
    assert 0 <= result["confidence"] <= 100
    assert result["candle_indexes"] and result["condition_results"]


@pytest.mark.parametrize("bullish,bearish", [
    ("MORNING DOJI STAR", "EVENING DOJI STAR"),
    ("BULLISH ABANDONED BABY", "BEARISH ABANDONED BABY"),
    ("THREE WHITE SOLDIERS", "THREE BLACK CROWS"),
    ("BEARISH THREE-LINE STRIKE", "BULLISH THREE-LINE STRIKE"),
])
def test_every_new_bearish_pattern_is_a_true_mirror(bullish, bearish):
    matches = TripleCandlePatternDetector(CFG).detect_all(
        pd.DataFrame(mirror(PATTERNS[bullish])))
    assert bearish in [match["pattern"] for match in matches]


@pytest.mark.parametrize("name,rows", PATTERNS.items())
def test_every_pattern_rejects_wrong_prior_trend(name, rows):
    wrong_trend = mirror(history([120, 116, 112, 108, 104]))
    malformed = [*wrong_trend, *rows[-(4 if "STRIKE" in name else 3):]]
    assert name not in [match["pattern"] for match in
                        TripleCandlePatternDetector(CFG).detect_all(pd.DataFrame(malformed))]


ZONE_R = {"type": "RESISTANCE", "lower": 109, "upper": 111, "midpoint": 110,
          "strength_score": 75, "classification": "STRONG", "touches": 3}
ZONE_S = {"type": "SUPPORT", "lower": 89, "upper": 91, "midpoint": 90,
          "strength_score": 75, "classification": "STRONG", "touches": 3}


@pytest.mark.parametrize("close,expected", [(107, "WEAK"), (104, "STANDARD"), (99, "STRONG")])
def test_resistance_rejection_strength_matrix(close, expected):
    rows = pd.DataFrame([
        {"Open": 100, "High": 111, "Low": 99, "Close": 110, "Volume": 100},
        {"Open": 110, "High": 113, "Low": 108, "Close": 109, "Volume": 100},
        {"Open": 109, "High": 110, "Low": close - 1, "Close": close, "Volume": 140},
    ])
    result = SupportResistanceEngine(CFG).detect_rejections(rows, [ZONE_R])[-1]
    assert result["raw_retracement_classification"] == expected


@pytest.mark.parametrize("close,expected", [(93, "WEAK"), (96, "STANDARD"), (101, "STRONG")])
def test_support_rejection_strength_matrix(close, expected):
    rows = mirror([
        {"Open": 100, "High": 111, "Low": 99, "Close": 110, "Volume": 100},
        {"Open": 110, "High": 113, "Low": 108, "Close": 109, "Volume": 100},
        {"Open": 109, "High": 110, "Low": (200 - close) - 1,
         "Close": 200 - close, "Volume": 140},
    ], ceiling=200)
    result = SupportResistanceEngine(CFG).detect_rejections(pd.DataFrame(rows), [ZONE_S])[-1]
    assert result["raw_retracement_classification"] == expected


def breakout_frame(bullish=True, wick_only=False):
    rows = [
        {"Open": 106, "High": 110, "Low": 105, "Close": 108, "Volume": 100},
        {"Open": 108, "High": 116, "Low": 107,
         "Close": 110 if wick_only else 115, "Volume": 180},
        {"Open": 114, "High": 118, "Low": 113, "Close": 117, "Volume": 160},
    ]
    return pd.DataFrame(rows if bullish else mirror(rows, 200))


def test_bullish_breakout_bearish_breakdown_and_wick_fakeout():
    bullish = BreakoutDetector(CFG).detect(breakout_frame(), [ZONE_R])["latest_event"]
    bearish_zone = {**ZONE_S, "lower": 89, "upper": 91, "midpoint": 90, "type": "SUPPORT"}
    bearish = BreakoutDetector(CFG).detect(breakout_frame(False), [bearish_zone])["latest_event"]
    fake = BreakoutDetector(CFG).detect(breakout_frame(wick_only=True), [ZONE_R])["events"][0]
    assert bullish["direction"] == "BULLISH" and bullish["quality"] in ("VALID", "STRONG")
    assert bearish["direction"] == "BEARISH" and bearish["quality"] in ("VALID", "STRONG")
    assert fake["quality"] == "FALSE_BREAKOUT"


def test_close_outside_then_immediate_return_is_fakeout():
    data = breakout_frame()
    data.loc[2, ["Open", "High", "Low", "Close"]] = [114, 115, 107, 109]
    result = BreakoutDetector(CFG).detect(data, [ZONE_R])["events"][0]
    assert result["quality"] == "FALSE_BREAKOUT"
    assert "IMMEDIATE_RETURN_INSIDE_ZONE" in result["reason_codes"]


def breakout_payload(direction="BULLISH"):
    zone = ZONE_R if direction == "BULLISH" else ZONE_S
    return {"quality": "STRONG", "direction": direction, "zone_crossed": zone,
            "breakout_candle": 1, "breakout_price": 113, "volume_ratio": 1.8}


def test_successful_failed_and_no_retest_states():
    successful = pd.DataFrame([
        {"Open": 107, "High": 109, "Low": 106, "Close": 108, "Volume": 100},
        {"Open": 108, "High": 115, "Low": 108, "Close": 114, "Volume": 180},
        {"Open": 113, "High": 114, "Low": 110, "Close": 112, "Volume": 90},
        {"Open": 112, "High": 117, "Low": 111, "Close": 116, "Volume": 170},
    ])
    result = RetestDetector(CFG).detect(successful, breakout_payload(), [ZONE_R], "BULLISH")
    assert result["retest_quality"] in ("VALID", "STRONG")
    failed = successful.copy()
    failed.loc[2, ["Open", "High", "Low", "Close"]] = [112, 113, 106, 108]
    assert RetestDetector(CFG).detect(failed, breakout_payload(), [ZONE_R])["retest_type"] == "FAILED_RETEST"
    continuation = successful.copy()
    continuation.loc[2:, ["Open", "High", "Low", "Close"]] = [[115, 119, 114, 118], [118, 122, 117, 121]]
    cfg = TechnicalSetupConfig(**{**CFG.__dict__, "no_retest_min_bars": 2})
    assert RetestDetector(cfg).detect(continuation, breakout_payload(), [ZONE_R])["retest_type"] == "NO_RETEST_CONTINUATION"


def test_successful_bearish_retest_is_mirrored():
    bullish = pd.DataFrame([
        {"Open": 107, "High": 109, "Low": 106, "Close": 108, "Volume": 100},
        {"Open": 108, "High": 115, "Low": 108, "Close": 114, "Volume": 180},
        {"Open": 113, "High": 114, "Low": 110, "Close": 112, "Volume": 90},
        {"Open": 112, "High": 117, "Low": 111, "Close": 116, "Volume": 170},
    ])
    bearish = pd.DataFrame(mirror(bullish.to_dict("records"), 200))
    payload = breakout_payload("BEARISH")
    payload["breakout_price"] = 87
    result = RetestDetector(CFG).detect(bearish, payload, [ZONE_S], "BEARISH")
    assert result["retest_quality"] in ("VALID", "STRONG")
    assert result["entry_price"] < ZONE_S["lower"]


def zigzag(values):
    return pd.DataFrame([{"Open": value, "High": value + .5, "Low": value - .5,
                          "Close": value, "Volume": 100} for value in values])


def test_structure_classifies_swings_bos_choch_and_failures():
    result = MarketStructureDetector(CFG).analyze(
        zigzag([100, 105, 101, 108, 103, 110, 102, 99, 104, 97, 101]))
    kinds = {event["event_type"] for event in result["events"]}
    assert {"HH", "HL", "LH", "LL"} & kinds
    assert {"BOS", "CHOCH"}.issubset(kinds)
    assert result["structure"] == "BEARISH"


def test_head_shoulders_and_inverse_detection_are_break_confirmed():
    bearish = zigzag([100, 110, 102, 116, 103, 110, 101, 98, 96])
    results = ReversalDetector(CFG).detect(bearish)
    assert any(item["setup"] == "HEAD AND SHOULDERS" for item in results)
    bullish = zigzag([120, 110, 118, 104, 117, 110, 119, 122, 125])
    results = ReversalDetector(CFG).detect(bullish)
    assert any(item["setup"] == "INVERSE HEAD AND SHOULDERS" for item in results)


def test_double_top_and_double_bottom_are_break_confirmed():
    top = ReversalDetector(CFG).detect(zigzag([100, 110, 102, 110.2, 101, 98, 96]))
    bottom = ReversalDetector(CFG).detect(zigzag([120, 110, 118, 109.8, 119, 122, 125]))
    assert any(item["setup"] == "DOUBLE TOP" for item in top)
    assert any(item["setup"] == "DOUBLE BOTTOM" for item in bottom)


def test_all_entry_modes():
    retest = {"entry_price": 112, "confidence": 80}
    breakout = {"quality": "STRONG", "breakout_price": 113, "confidence": 85}
    pattern = {"pattern": "MORNING DOJI STAR", "invalidation_price": 105}
    entry_zone = {**ZONE_S, "lower": 108, "upper": 110, "midpoint": 109}
    entries = SetupEntryEngine(CFG).build(
        "BULLISH", "TEST", 112, 4, zone=entry_zone, pattern=pattern,
        structure="BULLISH", breakout=breakout, retest=retest,
        confirmation_price=112, opposing_zones=[])
    assert {entry["entry_mode"] for entry in entries} == set(SetupEntryEngine.MODES)


def test_pattern_history_is_prefix_stable():
    rows = pd.DataFrame(PATTERNS["MORNING DOJI STAR"] + history([106, 108, 110]))
    full = TripleCandlePatternDetector(CFG).detect_all(rows)
    prefix = TripleCandlePatternDetector(CFG).detect_all(rows.iloc[:-2])
    cutoff = rows.index[-3]
    assert prefix == [item for item in full if item["candle_indexes"][-1] <= cutoff]
