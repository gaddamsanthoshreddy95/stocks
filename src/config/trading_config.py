"""
Trading Configuration

All configurable thresholds used by the AI Trading Assistant.
Changing a value here affects the entire application.
"""

import os
from dataclasses import dataclass, field

EQUITY_MIN_RISK_REWARD = float(os.getenv("EQUITY_MIN_RISK_REWARD", "1.5"))
# Opt-in blend preserves historical ranking calibration while making the new
# component score part of the established ScoreEngine (set 0.0-1.0 by env).
PRICE_ACTION_SCORE_WEIGHT = float(os.getenv("PRICE_ACTION_SCORE_WEIGHT", "0.0"))

# ==========================================================
# Trend
# ==========================================================

EMA_SHORT = 20
EMA_MEDIUM = 50
EMA_LONG = 200

# ==========================================================
# RSI
# ==========================================================

RSI_OVERSOLD = 30
RSI_WEAK = 45
RSI_NEUTRAL = 55
RSI_STRONG = 70
RSI_OVERBOUGHT = 75

# ==========================================================
# Volume
# ==========================================================

RVOL_VERY_HIGH = 2.0
RVOL_HIGH = 1.5
RVOL_NORMAL = 1.0
RVOL_LOW = 0.75

# ==========================================================
# Volatility (ATR %)
# ==========================================================

ATR_LOW = 1.5
ATR_NORMAL = 3.0
ATR_HIGH = 5.0

# ==========================================================
# Support / Resistance
# ==========================================================

SUPPORT_NEAR = 2.0
RESISTANCE_NEAR = 2.0

# ==========================================================
# Breakout
# ==========================================================

BREAKOUT_RVOL = 1.5
BREAKOUT_RSI_MAX = 75

# ==========================================================
# Score Weights
# ==========================================================

SIGNAL_WEIGHTS = {

    "Trend": 0.30,

    "Momentum": 0.20,

    "Volume": 0.15,

    "Volatility": 0.15,

    "Support": 0.20

}

# ==========================================================
# Recommendation Thresholds
# ==========================================================

STRONG_BUY = 85
BUY = 70
WATCHLIST = 55
NEUTRAL = 40


@dataclass(frozen=True)
class TechnicalSetupConfig:
    """Central thresholds for price-action setup detection.

    Ratios are decimal fractions and ATR values are multiples.  Keeping the
    configuration immutable makes it safe to share between pipeline stages.
    """

    pivot_lookback: int = 2
    trend_lookback: int = 8
    trend_min_change_ratio: float = 0.005
    min_pattern_body_ratio: float = 0.45
    doji_body_ratio: float = 0.10
    long_body_atr: float = 0.60
    max_wick_body_ratio: float = 0.60
    atr_period: int = 14
    gap_tolerance_atr: float = 0.10
    zone_width_atr: float = 0.50
    touch_tolerance_atr: float = 0.25
    min_zone_touches: int = 2
    max_zone_touches_before_weakening: int = 5
    breakout_atr_threshold: float = 0.10
    breakout_extended_atr: float = 2.0
    retest_tolerance_atr: float = 0.35
    retest_max_bars: int = 10
    min_relative_volume: float = 1.20
    confirmation_mode: str = "CLOSE"
    min_risk_reward: float = 1.50
    max_stop_atr: float = 3.0
    stop_atr_buffer: float = 0.20
    higher_timeframe_enabled: bool = False
    higher_timeframe_rule: str = "completed_only"
    higher_timeframe_frequency: str = "W-FRI"
    require_breakout_confirmation: bool = False
    no_retest_min_bars: int = 3
    consolidation_lookback: int = 10
    consolidation_max_atr: float = 2.0
    round_number_weight: float = 3.0
    ema_confluence_atr: float = 0.25
    vwap_confluence_atr: float = 0.25
    next_zone_min_atr: float = 1.0
    volume_lookback: int = 20
    entry_next_candle: bool = False
    entry_expiry_bars: int = 10
    slippage_bps: float = 5.0
    transaction_cost_bps: float = 10.0
    ambiguous_bar_policy: str = "STOP_FIRST"
    analysis_lookback_bars: int = 500
    signal_history_bars: int = 60
    pattern_similarity_atr: float = 0.50
    min_pattern_separation: int = 2
    rejection_standard_ratio: float = 0.50
    rejection_strong_ratio: float = 1.0
    score_weights: dict[str, float] = field(default_factory=lambda: {
        "trend": .10, "market_structure": .12, "candlestick_pattern": .10,
        "support_resistance": .10, "breakout": .09, "retest": .10,
        "volume": .07, "volatility": .05, "momentum": .07,
        "opposing_zone_distance": .07, "risk_reward": .08,
        "higher_timeframe": .03, "option_selling": .02,
    })
    classification_thresholds: dict[str, float] = field(default_factory=lambda: {
        "reject": 35, "low_confidence": 50, "watchlist": 65,
        "valid_setup": 78, "high_confidence": 100,
    })


TECHNICAL_SETUP_CONFIG = TechnicalSetupConfig()
