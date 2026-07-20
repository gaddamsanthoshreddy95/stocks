"""Runtime settings for the public application layer."""

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class PlatformSettings:
    """Safe defaults that can be overridden through environment variables."""

    capital: float = 100_000.0
    risk_percent: float = 1.0
    option_capital: float = 2_500_000.0
    option_risk_per_trade: float = 100_000.0
    allow_live_trading: bool = False
    market_data_source: str = "kite"
    trading_strategy_mode: str = "both"
    equity_min_risk_reward: float = 1.5
    equity_b_grade_min_risk_reward: float = 1.3
    equity_watchlist_min_risk_reward: float = 1.2
    ranking_shortlist_size: int = 20
    short_put_min_otm_percent: float = 8.0
    short_put_max_otm_percent: float = 10.0
    short_put_min_dte: int = 7
    short_put_max_dte: int = 35
    short_put_target_delta_min: float = 0.10
    short_put_target_delta_max: float = 0.20
    short_put_min_open_interest: int = 1000
    short_put_min_volume: int = 100
    short_put_max_bid_ask_spread_percent: float = 10.0
    short_put_min_premium: float = 0.50
    short_put_min_return_on_risk_percent: float = 1.0
    short_put_min_return_on_margin_percent: float = 0.5
    short_put_min_probability_otm: float = 75.0
    short_put_min_atr_coverage: float = 1.5
    short_put_require_strike_below_support: bool = True
    short_put_max_risk_per_trade: float = 100_000.0
    short_put_allow_naked: bool = False
    short_put_prefer_credit_spread: bool = True
    short_put_hedge_width_steps: int = 1
    short_put_event_risk_block: bool = True
    short_put_max_portfolio_exposure_percent: float = 25.0
    short_put_max_sector_exposure_percent: float = 10.0
    short_put_max_correlated_exposure_percent: float = 15.0
    news_ai_model: str = "ProsusAI/finbert"
    news_spacy_model: str = "en_core_web_sm"

    def __post_init__(self) -> None:
        if self.trading_strategy_mode not in {"equity", "short_put", "both"}:
            raise ValueError("TRADING_STRATEGY_MODE must be 'equity', 'short_put', or 'both'")
        if self.equity_min_risk_reward <= 0:
            raise ValueError("EQUITY_MIN_RISK_REWARD must be positive")
        if not (0 < self.equity_watchlist_min_risk_reward
                <= self.equity_b_grade_min_risk_reward
                <= self.equity_min_risk_reward):
            raise ValueError("Equity risk/reward thresholds must satisfy C <= B <= A")
        if not 1 <= self.ranking_shortlist_size <= 30:
            raise ValueError("RANKING_SHORTLIST_SIZE must be between 1 and 30")
        if not 0 < self.short_put_min_otm_percent <= self.short_put_max_otm_percent < 100:
            raise ValueError("Short-Put OTM range is invalid")
        if not 0 <= self.short_put_min_dte <= self.short_put_max_dte:
            raise ValueError("Short-Put DTE range is invalid")
        if not 0 <= self.short_put_target_delta_min <= self.short_put_target_delta_max <= 1:
            raise ValueError("Short-Put absolute delta range is invalid")
        if self.short_put_hedge_width_steps < 1:
            raise ValueError("SHORT_PUT_HEDGE_WIDTH_STEPS must be at least one")
        if self.short_put_max_risk_per_trade <= 0:
            raise ValueError("SHORT_PUT_MAX_RISK_PER_TRADE must be positive")
        for value in (self.short_put_max_portfolio_exposure_percent,
                      self.short_put_max_sector_exposure_percent,
                      self.short_put_max_correlated_exposure_percent):
            if not 0 < value <= 100:
                raise ValueError("Short-Put exposure limits must be between 0 and 100")

    @classmethod
    def from_environment(cls) -> "PlatformSettings":
        capital = float(os.getenv("TRADING_CAPITAL", "100000"))
        risk_percent = float(os.getenv("TRADING_RISK_PERCENT", "1"))
        option_capital = float(os.getenv("OPTION_CAPITAL", "2500000"))
        option_risk = float(os.getenv("OPTION_RISK_PER_TRADE", "100000"))
        allow_live = os.getenv("ALLOW_LIVE_TRADING", "false").lower() == "true"
        market_data_source = os.getenv("MARKET_DATA_SOURCE", "kite").lower()
        strategy_mode = os.getenv("TRADING_STRATEGY_MODE", "both").lower()
        if capital <= 0:
            raise ValueError("TRADING_CAPITAL must be positive")
        if not 0 < risk_percent <= 100:
            raise ValueError("TRADING_RISK_PERCENT must be between 0 and 100")
        if option_capital <= 0:
            raise ValueError("OPTION_CAPITAL must be positive")
        if not 0 < option_risk <= option_capital:
            raise ValueError("OPTION_RISK_PER_TRADE must be positive and no greater than OPTION_CAPITAL")
        if market_data_source not in {"kite", "cache"}:
            raise ValueError("MARKET_DATA_SOURCE must be 'kite' or 'cache'")
        if strategy_mode not in {"equity", "short_put", "both"}:
            raise ValueError("TRADING_STRATEGY_MODE must be 'equity', 'short_put', or 'both'")
        env_float = lambda name, default: float(os.getenv(name, str(default)))
        env_int = lambda name, default: int(os.getenv(name, str(default)))
        env_bool = lambda name, default: os.getenv(name, str(default).lower()).lower() == "true"
        min_otm = env_float("SHORT_PUT_MIN_OTM_PERCENT", 8.0)
        max_otm = env_float("SHORT_PUT_MAX_OTM_PERCENT", 10.0)
        min_dte = env_int("SHORT_PUT_MIN_DTE", 7)
        max_dte = env_int("SHORT_PUT_MAX_DTE", 35)
        if not 0 < min_otm <= max_otm < 100:
            raise ValueError("Short-Put OTM percentages must satisfy 0 < minimum <= maximum < 100")
        if not 0 <= min_dte <= max_dte:
            raise ValueError("Short-Put DTE range is invalid")
        return cls(
            capital=capital,
            risk_percent=risk_percent,
            option_capital=option_capital,
            option_risk_per_trade=option_risk,
            allow_live_trading=allow_live,
            market_data_source=market_data_source,
            trading_strategy_mode=strategy_mode,
            equity_min_risk_reward=env_float("EQUITY_MIN_RISK_REWARD", 1.5),
            equity_b_grade_min_risk_reward=env_float("EQUITY_B_GRADE_MIN_RISK_REWARD", 1.3),
            equity_watchlist_min_risk_reward=env_float("EQUITY_WATCHLIST_MIN_RISK_REWARD", 1.2),
            ranking_shortlist_size=env_int("RANKING_SHORTLIST_SIZE", 20),
            short_put_min_otm_percent=min_otm,
            short_put_max_otm_percent=max_otm,
            short_put_min_dte=min_dte,
            short_put_max_dte=max_dte,
            short_put_target_delta_min=env_float("SHORT_PUT_TARGET_DELTA_MIN", .10),
            short_put_target_delta_max=env_float("SHORT_PUT_TARGET_DELTA_MAX", .20),
            short_put_min_open_interest=env_int("SHORT_PUT_MIN_OPEN_INTEREST", 1000),
            short_put_min_volume=env_int("SHORT_PUT_MIN_VOLUME", 100),
            short_put_max_bid_ask_spread_percent=env_float("SHORT_PUT_MAX_BID_ASK_SPREAD_PERCENT", 10),
            short_put_min_premium=env_float("SHORT_PUT_MIN_PREMIUM", .5),
            short_put_min_return_on_risk_percent=env_float("SHORT_PUT_MIN_RETURN_ON_RISK_PERCENT", 1),
            short_put_min_return_on_margin_percent=env_float("SHORT_PUT_MIN_RETURN_ON_MARGIN_PERCENT", .5),
            short_put_min_probability_otm=env_float("SHORT_PUT_MIN_PROBABILITY_OTM", 75),
            short_put_min_atr_coverage=env_float("SHORT_PUT_MIN_ATR_COVERAGE", 1.5),
            short_put_require_strike_below_support=env_bool("SHORT_PUT_REQUIRE_STRIKE_BELOW_SUPPORT", True),
            short_put_max_risk_per_trade=env_float("SHORT_PUT_MAX_RISK_PER_TRADE", 100000),
            short_put_allow_naked=env_bool("SHORT_PUT_ALLOW_NAKED", False),
            short_put_prefer_credit_spread=env_bool("SHORT_PUT_PREFER_CREDIT_SPREAD", True),
            short_put_hedge_width_steps=env_int("SHORT_PUT_HEDGE_WIDTH_STEPS", 1),
            short_put_event_risk_block=env_bool("SHORT_PUT_EVENT_RISK_BLOCK", True),
            short_put_max_portfolio_exposure_percent=env_float("SHORT_PUT_MAX_PORTFOLIO_EXPOSURE_PERCENT", 25),
            short_put_max_sector_exposure_percent=env_float("SHORT_PUT_MAX_SECTOR_EXPOSURE_PERCENT", 10),
            short_put_max_correlated_exposure_percent=env_float("SHORT_PUT_MAX_CORRELATED_EXPOSURE_PERCENT", 15),
            news_ai_model=os.getenv("NEWS_FINBERT_MODEL", "ProsusAI/finbert"),
            news_spacy_model=os.getenv("NEWS_SPACY_MODEL", "en_core_web_sm"),
        )
