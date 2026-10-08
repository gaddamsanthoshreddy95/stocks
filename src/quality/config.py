"""Central validated configuration for experimental candidate-quality scoring."""

from __future__ import annotations

from dataclasses import dataclass, field
import os


def _weights(**values: float) -> dict[str, float]:
    return values


@dataclass(frozen=True)
class QualityConfig:
    ranking_mode: str = "SHADOW"
    rs_lookbacks: tuple[int, int, int] = (5, 20, 60)
    rs_weights: dict[str, float] = field(default_factory=lambda: _weights(
        nifty_5d=.15, nifty_20d=.30, nifty_60d=.20,
        sector_5d=.10, sector_20d=.20, sector_60d=.05,
    ))
    rs_score_bands: tuple[tuple[float, float], ...] = (
        (10, 100), (6, 90), (3, 80), (1, 65), (-1, 50), (-3, 35), (-6, 20),
    )
    ema_slope_lookbacks: tuple[int, int, int] = (5, 10, 20)
    bullish_rvol_threshold: float = 1.2
    breakout_rvol_threshold: float = 1.2
    bearish_rvol_threshold: float = 1.2
    minimum_candle_body_percent: float = .50
    bullish_close_location: float = .70
    bearish_close_location: float = .30
    minimum_buy_risk_reward: float = 1.5
    preferred_risk_reward: float = 2.0
    minimum_stop_atr: float = .25
    maximum_stop_atr: float = 3.0
    maximum_risk_percent: float = 3.0
    event_critical_days: int = 1
    event_high_risk_days: int = 3
    event_caution_days: int = 7
    maximum_initial_candidates_per_sector: int = 5
    maximum_final_positions_per_sector: int = 2
    maximum_sector_capital_percent: float = 25.0
    maximum_correlation: float = .85
    correlation_lookback: int = 60
    minimum_option_oi: int = 10_000
    minimum_option_volume: int = 1_000
    maximum_bid_ask_spread_percent: float = 5.0
    minimum_strike_distance_atr: float = 1.5
    minimum_strike_distance_percent: float = 5.0
    minimum_option_sell_score: float = 65.0
    maximum_margin_per_trade: float = 100_000.0
    maximum_sector_pe_deviation: float = .05
    minimum_roe_percent: float = 15.0
    minimum_roce_percent: float = 15.0
    block_earnings_option_selling: bool = True
    fundamental_missing_policy: str = "REJECT"
    event_missing_policy: str = "NEUTRAL"
    sector_missing_policy: str = "NEUTRAL"
    intraday_missing_policy: str = "RENORMALIZE"
    stock_quality_weights: dict[str, float] = field(default_factory=lambda: _weights(
        data_quality=.09, liquidity=.12, price_behaviour=.12, relative_strength=.18,
        sector_strength=.06, trend_quality=.13, fundamental_quality=.11,
        valuation_quality=.07, delivery_quality=.04, vwap_quality=.08,
    ))
    directional_weights: dict[str, float] = field(default_factory=lambda: _weights(
        stock_quality=.25, setup_quality=.25, entry_readiness=.20,
        risk_reward_quality=.10, path_quality=.05, market_alignment=.05,
        multi_timeframe=.05, event_safety=.05,
    ))
    final_directional_weights: dict[str, float] = field(default_factory=lambda: _weights(
        stock_quality=.35, setup_quality=.25, entry_readiness=.20,
        risk_reward_quality=.08, path_quality=.04, market_alignment=.03,
        multi_timeframe=.03, event_safety=.02,
    ))
    option_sell_weights: dict[str, float] = field(default_factory=lambda: _weights(
        underlying_quality=.15, fundamental_quality=.15, support_quality=.15,
        strike_safety=.15, option_liquidity=.15, volatility_premium=.10,
        event_safety=.10, return_on_capital=.05,
    ))
    hard_gate_thresholds: dict[str, float] = field(default_factory=lambda: {
        "data_quality": 60, "liquidity": 40, "stock_quality": 50,
        "price_behaviour": 60, "trend_quality": 60, "vwap_quality": 100,
        "setup_quality": 55, "entry_readiness": 55, "path_quality": 40,
        "event_safety": 40, "valuation_quality": 100, "delivery_quality": 100,
        "debt_free_quality": 100, "roe_quality": 100, "roce_quality": 100,
        "institutional_holding_quality": 100, "promoter_holding_quality": 100,
        "quarterly_results_quality": 100, "commentary_quality": 100,
        "block_deal_quality": 100, "recent_news_quality": 100,
        "sector_one_year_quality": 100, "sector_leadership_quality": 100,
    })

    def __post_init__(self) -> None:
        if self.ranking_mode not in {"LEGACY", "SHADOW", "COMPOSITE"}:
            raise ValueError("RANKING_MODE must be LEGACY, SHADOW, or COMPOSITE")
        for name, weights in (
            ("RS weights", self.rs_weights),
            ("stock-quality weights", self.stock_quality_weights),
            ("directional weights", self.directional_weights),
            ("final directional weights", self.final_directional_weights),
            ("option-sell weights", self.option_sell_weights),
        ):
            if abs(sum(weights.values()) - 1.0) > 1e-8:
                raise ValueError(f"{name} must total 1.0")
            if any(value < 0 or value > 1 for value in weights.values()):
                raise ValueError(f"{name} must contain values between 0 and 1")
        if any(value <= 0 for value in (*self.rs_lookbacks, *self.ema_slope_lookbacks)):
            raise ValueError("Quality lookbacks must be positive")
        if not 0 < self.minimum_stop_atr <= self.maximum_stop_atr:
            raise ValueError("MIN_STOP_ATR must not exceed MAX_STOP_ATR")
        if not 0 < self.minimum_buy_risk_reward <= self.preferred_risk_reward:
            raise ValueError("Risk/reward thresholds are invalid")
        if not 0 <= self.maximum_correlation <= 1:
            raise ValueError("MAX_CORRELATION must be between 0 and 1")
        if not 0 <= self.maximum_sector_pe_deviation <= 1:
            raise ValueError("MAX_SECTOR_PE_DEVIATION must be between 0 and 1")
        if self.minimum_roe_percent < 0 or self.minimum_roce_percent < 0:
            raise ValueError("Minimum ROE and ROCE must not be negative")
        if self.fundamental_missing_policy not in {"ALLOW", "WARN", "REJECT"}:
            raise ValueError("FUNDAMENTAL_MISSING_POLICY is invalid")

    @classmethod
    def from_env(cls) -> "QualityConfig":
        number = lambda name, default: float(os.getenv(name, str(default)))
        integer = lambda name, default: int(os.getenv(name, str(default)))
        boolean = lambda name, default: os.getenv(
            name, str(default)).strip().lower() in {"1", "true", "yes", "on"}
        return cls(
            ranking_mode=os.getenv("RANKING_MODE", "SHADOW").upper(),
            bullish_rvol_threshold=number("BULLISH_RVOL_THRESHOLD", 1.2),
            breakout_rvol_threshold=number("BREAKOUT_RVOL_THRESHOLD", 1.2),
            bearish_rvol_threshold=number("BEARISH_RVOL_THRESHOLD", 1.2),
            minimum_candle_body_percent=number("MIN_CANDLE_BODY_PCT", .5),
            bullish_close_location=number(
                "CLOSE_LOCATION_BULLISH_THRESHOLD", .7),
            bearish_close_location=number(
                "CLOSE_LOCATION_BEARISH_THRESHOLD", .3),
            minimum_buy_risk_reward=number("MIN_BUY_RISK_REWARD", 1.5),
            preferred_risk_reward=number("PREFERRED_RISK_REWARD", 2),
            minimum_stop_atr=number("MIN_STOP_ATR", .25),
            maximum_stop_atr=number("MAX_STOP_ATR", 3),
            maximum_risk_percent=number("MAX_RISK_PERCENT", 3),
            event_critical_days=integer("EVENT_CRITICAL_DAYS", 1),
            event_high_risk_days=integer("EVENT_HIGH_RISK_DAYS", 3),
            event_caution_days=integer("EVENT_CAUTION_DAYS", 7),
            maximum_initial_candidates_per_sector=integer(
                "MAX_INITIAL_CANDIDATES_PER_SECTOR", 5),
            maximum_final_positions_per_sector=integer(
                "MAX_FINAL_POSITIONS_PER_SECTOR", 2),
            maximum_sector_capital_percent=number(
                "MAX_SECTOR_CAPITAL_PERCENT", 25),
            maximum_correlation=number("MAX_CORRELATION", .85),
            correlation_lookback=integer("CORRELATION_LOOKBACK", 60),
            minimum_option_oi=integer("MIN_OPTION_OI", 10_000),
            minimum_option_volume=integer("MIN_OPTION_VOLUME", 1_000),
            maximum_bid_ask_spread_percent=number(
                "MAX_BID_ASK_SPREAD_PCT", 5),
            minimum_strike_distance_atr=number(
                "MIN_STRIKE_DISTANCE_ATR", 1.5),
            minimum_strike_distance_percent=number(
                "MIN_STRIKE_DISTANCE_PCT", 5),
            minimum_option_sell_score=number("MIN_OPTION_SELL_SCORE", 65),
            maximum_margin_per_trade=number("MAX_MARGIN_PER_TRADE", 100_000),
            maximum_sector_pe_deviation=number("MAX_SECTOR_PE_DEVIATION", .05),
            minimum_roe_percent=number("MIN_ROE_PERCENT", 15),
            minimum_roce_percent=number("MIN_ROCE_PERCENT", 15),
            block_earnings_option_selling=boolean(
                "BLOCK_EARNINGS_OPTION_SELLING", True),
            fundamental_missing_policy=os.getenv(
                "FUNDAMENTAL_MISSING_POLICY", "REJECT").upper(),
            event_missing_policy=os.getenv("EVENT_MISSING_POLICY", "NEUTRAL").upper(),
            sector_missing_policy=os.getenv("SECTOR_MISSING_POLICY", "NEUTRAL").upper(),
            intraday_missing_policy=os.getenv(
                "INTRADAY_MISSING_POLICY", "RENORMALIZE").upper(),
        )
