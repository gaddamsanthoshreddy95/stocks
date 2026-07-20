from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ShortPutRejectionCode(StrEnum):
    UNDERLYING_NOT_BULLISH = "UNDERLYING_NOT_BULLISH"
    REVERSAL_NOT_CONFIRMED = "REVERSAL_NOT_CONFIRMED"
    NO_VALID_EXPIRY = "NO_VALID_EXPIRY"
    NO_STRIKE_IN_OTM_BAND = "NO_STRIKE_IN_OTM_BAND"
    STRIKE_ABOVE_SUPPORT = "STRIKE_ABOVE_SUPPORT"
    ATR_COVERAGE_TOO_LOW = "ATR_COVERAGE_TOO_LOW"
    DELTA_OUT_OF_RANGE = "DELTA_OUT_OF_RANGE"
    PROBABILITY_TOO_LOW = "PROBABILITY_TOO_LOW"
    OPEN_INTEREST_TOO_LOW = "OPEN_INTEREST_TOO_LOW"
    VOLUME_TOO_LOW = "VOLUME_TOO_LOW"
    BID_ASK_SPREAD_TOO_WIDE = "BID_ASK_SPREAD_TOO_WIDE"
    PREMIUM_TOO_LOW = "PREMIUM_TOO_LOW"
    EVENT_RISK = "EVENT_RISK"
    NEGATIVE_NEWS = "NEGATIVE_NEWS"
    MAX_LOSS_EXCEEDED = "MAX_LOSS_EXCEEDED"
    CAPITAL_INSUFFICIENT = "CAPITAL_INSUFFICIENT"
    NO_VALID_HEDGE = "NO_VALID_HEDGE"
    INVALID_QUOTES = "INVALID_QUOTES"
    IV_UNAVAILABLE = "IV_UNAVAILABLE"
    GREEKS_UNAVAILABLE = "GREEKS_UNAVAILABLE"
    OPTION_DATA_UNAVAILABLE = "OPTION_DATA_UNAVAILABLE"
    MARKET_CONTEXT_CONFLICT = "MARKET_CONTEXT_CONFLICT"
    SECTOR_CONTEXT_WEAK = "SECTOR_CONTEXT_WEAK"
    PORTFOLIO_EXPOSURE_EXCEEDED = "PORTFOLIO_EXPOSURE_EXCEEDED"
    SECTOR_EXPOSURE_EXCEEDED = "SECTOR_EXPOSURE_EXCEEDED"
    CORRELATION_DATA_UNAVAILABLE = "CORRELATION_DATA_UNAVAILABLE"


@dataclass(slots=True)
class ShortPutRejection:
    code: str
    reason: str


@dataclass(slots=True)
class ShortPutLeg:
    side: str
    symbol: str
    strike: float
    premium: float
    bid: float
    ask: float
    quantity: int


@dataclass(slots=True)
class ShortPutCandidate:
    symbol: str
    spot_price: float
    expiry: str
    dte: int
    sold_put_strike: float
    strike_distance: float
    strike_distance_percent: float
    support: float | None
    distance_below_support: float | None
    atr: float
    atr_coverage: float
    option_premium: float
    bid: float
    ask: float
    bid_ask_spread_percent: float
    volume: int
    open_interest: int
    change_in_open_interest: int | None
    implied_volatility: float | None
    delta: float | None
    probability_otm: float | None
    probability_source: str
    probability_quality: str
    lot_size: int
    change_in_oi_reliable: bool = False
    historically_calibrated_probability: float | None = None
    historical_calibration: str = "UNAVAILABLE"


@dataclass(slots=True)
class ShortPutEvaluation:
    liquidity_status: str
    risk_status: str
    approved: bool
    rejections: list[ShortPutRejection] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ShortPutStrategyPlan:
    available: bool
    symbol: str
    underlying_setup: str
    strategy: str | None = None
    candidate: ShortPutCandidate | None = None
    sold_leg: ShortPutLeg | None = None
    hedge_leg: ShortPutLeg | None = None
    net_credit: float = 0.0
    maximum_profit: float = 0.0
    maximum_loss: float = 0.0
    capital_required: float = 0.0
    return_on_risk_percent: float = 0.0
    return_on_margin_percent: float = 0.0
    breakeven: float = 0.0
    lots: int = 0
    evaluation: ShortPutEvaluation | None = None
    rejection_code: str | None = None
    rejection_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    margin_source: str = "DEFINED_MAXIMUM_LOSS"
    exposure_status: str = "AVAILABLE_CAPITAL_CHECKED"
    strike_search: dict = field(default_factory=dict)
    annualized_return_on_margin_percent: float = 0.0
    actionable_recommendations: list[str] = field(default_factory=list)
    best_rejected_candidate: dict | None = None
