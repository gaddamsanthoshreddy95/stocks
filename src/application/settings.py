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
    candidate_ranking_mode: str = "EXPECTED_VALUE"
    quality_grade_a_plus: float = 92.0
    quality_grade_a: float = 85.0
    quality_grade_b_plus: float = 80.0
    quality_grade_b: float = 75.0
    quality_grade_c_plus: float = 70.0
    quality_grade_c: float = 65.0
    readiness_execute_bullish: float = 75.0
    readiness_execute_neutral: float = 80.0
    readiness_execute_cautious: float = 85.0
    readiness_execute_strong_bearish: float = 90.0
    readiness_prepare: float = 70.0
    readiness_watch_intraday: float = 55.0
    readiness_wait: float = 40.0
    option_approval_min_readiness: float = 75.0
    option_min_payoff_risk_reward: float = 1.0
    option_min_open_interest: int = 10_000
    option_min_volume: int = 1_000
    option_max_bid_ask_spread_percent: float = 5.0
    option_max_implied_volatility: float = 100.0
    option_max_quote_age_seconds: float = 120.0
    resistance_clearance_min_atr: float = 0.35
    entry_min_technical_score: float = 55.0
    entry_min_relative_volume: float = 0.75
    entry_min_sector_score: float = 50.0
    bearish_bullish_trade_min_relative_strength: float = 80.0
    uncertain_bullish_trade_min_relative_strength: float = 65.0
    stock_trade_absolute_rr_floor: float = 1.0
    candidate_min_liquidity_score: float = 40.0
    candidate_min_trust_score: float = 55.0
    selection_max_trades_per_sector: int = 2
    selection_stability_lookback_runs: int = 3
    selection_stability_min_appearances: int = 2
    entry_zone_below_atr: float = 0.25
    entry_zone_above_atr: float = 0.50
    setup_min_technical_score: float = 55.0
    setup_support_near_percent: float = 4.0
    setup_reversal_rsi: float = 35.0
    entry_confirmation_relative_volume: float = 1.2
    entry_min_close_location: float = 0.60
    entry_normal_extension_atr: float = 1.0
    entry_max_extension_atr: float = 1.50
    entry_no_chase_extension_atr: float = 2.0
    breakout_retest_tolerance_atr: float = 0.25
    breakout_consolidation_max_range_atr: float = 0.80
    entry_max_consecutive_bullish_candles: int = 4
    bullish_max_adverse_move_percent: float = 3.0
    bullish_min_adverse_hold_probability: float = 70.0
    bullish_barrier_horizon_days: int = 5
    bullish_barrier_minimum_samples: int = 60
    bullish_intraday_barrier_minimum_samples: int = 30
    bullish_max_technical_stop_percent: float = 3.0
    bullish_min_target_before_adverse_probability: float = 55.0
    bullish_min_no_overnight_gap_probability: float = 95.0
    bullish_min_relative_strength_score: float = 55.0
    bearish_max_technical_stop_percent: float = 3.0
    bearish_min_target_before_adverse_probability: float = 55.0
    bearish_min_no_overnight_gap_probability: float = 95.0
    bearish_max_relative_strength_percent: float = 0.0
    bearish_max_sector_score: float = 45.0
    option_call_resistance_near_percent: float = 1.5
    option_long_delta_min: float = 0.35
    option_long_delta_max: float = 0.70
    option_max_theta_premium_ratio: float = 0.08
    option_high_iv_warning: float = 55.0
    market_low_confidence_threshold: float = 50.0
    market_confirmed_confidence_threshold: float = 65.0
    news_analysis_required_for_execution: bool = True
    calibration_min_outcomes: int = 200
    event_risk_enabled: bool = True
    event_very_low_max: float = 20.0
    event_low_max: float = 40.0
    event_medium_max: float = 60.0
    event_extreme_min: float = 81.0
    crude_daily_move_warning: float = 3.0
    crude_daily_move_high: float = 5.0
    crude_daily_move_extreme: float = 8.0
    commodity_zscore_high: float = 2.0
    commodity_zscore_extreme: float = 3.0
    commodity_multiday_move_high: float = 6.0
    event_medium_position_multiplier: float = 0.75
    event_high_position_multiplier: float = 0.50
    event_extreme_position_multiplier: float = 0.25
    event_extreme_block_new_trades: bool = True
    event_extreme_block_overnight: bool = True
    earnings_same_day_block: bool = True
    event_data_unavailable_readiness_penalty: float = 5.0
    event_data_stale_readiness_penalty: float = 8.0
    event_data_complete_no_match_penalty: float = 0.0
    event_data_partial_coverage_penalty: float = 2.0
    event_data_not_requested_penalty: float = 0.0
    event_data_primary_unavailable_penalty: float = 3.0
    event_data_all_unavailable_penalty: float = 5.0
    event_data_fetch_failed_penalty: float = 8.0
    event_penalty_max_very_low: float = 5.0
    event_penalty_max_low: float = 10.0
    event_penalty_max_medium: float = 20.0
    event_penalty_max_high: float = 40.0
    event_penalty_max_extreme: float = 60.0
    event_data_max_age_minutes: float = 60.0
    event_stale_after_minutes: float = 180.0
    event_fresh_multiplier: float = 1.0
    event_delayed_multiplier: float = 0.8
    event_stale_multiplier: float = 0.5
    event_default_half_life_hours: float = 24.0
    geopolitical_half_life_hours: float = 72.0
    earnings_half_life_hours: float = 48.0
    commodity_shock_half_life_hours: float = 36.0
    event_risk_hard_block_score: float = 90.0
    event_gap_risk_hard_block_score: float = 90.0
    event_allow_intraday_exception: bool = True
    event_defined_risk_options_only_at_high: bool = True
    event_allow_test_overrides: bool = False
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
        if self.candidate_ranking_mode not in {"EXPECTED_VALUE", "QUALITY_SCORE", "AI_SCORE", "READINESS"}:
            raise ValueError("CANDIDATE_RANKING_MODE is invalid")
        grade_thresholds = (self.quality_grade_a_plus, self.quality_grade_a,
                            self.quality_grade_b_plus, self.quality_grade_b,
                            self.quality_grade_c_plus, self.quality_grade_c)
        if not all(0 <= value <= 100 for value in grade_thresholds) or any(
                left < right for left, right in zip(grade_thresholds, grade_thresholds[1:])):
            raise ValueError("Quality grade thresholds must descend from A+ through C")
        if self.calibration_min_outcomes < 1:
            raise ValueError("CALIBRATION_MIN_OUTCOMES must be positive")
        readiness_values = (self.readiness_execute_bullish, self.readiness_execute_neutral,
                            self.readiness_execute_cautious, self.readiness_execute_strong_bearish,
                            self.readiness_prepare, self.readiness_watch_intraday, self.readiness_wait)
        if not all(0 <= value <= 100 for value in readiness_values):
            raise ValueError("Readiness thresholds must be between 0 and 100")
        if not (self.readiness_prepare >= self.readiness_watch_intraday >= self.readiness_wait):
            raise ValueError("Readiness PREPARE/WATCH/WAIT thresholds must descend")
        if not 0 <= self.option_approval_min_readiness <= 100:
            raise ValueError("OPTION_APPROVAL_MIN_READINESS must be between 0 and 100")
        if self.resistance_clearance_min_atr < 0:
            raise ValueError("RESISTANCE_CLEARANCE_MIN_ATR cannot be negative")
        if self.option_min_payoff_risk_reward <= 0 or self.stock_trade_absolute_rr_floor <= 0:
            raise ValueError("Configured risk/reward floors must be positive")
        if self.option_min_open_interest < 0 or self.option_min_volume < 0:
            raise ValueError("Option liquidity thresholds cannot be negative")
        if self.option_max_bid_ask_spread_percent <= 0 or self.option_max_quote_age_seconds <= 0:
            raise ValueError("Option quote thresholds must be positive")
        for value in (self.entry_min_technical_score, self.entry_min_sector_score,
                      self.setup_min_technical_score, self.setup_reversal_rsi,
                      self.bearish_bullish_trade_min_relative_strength,
                      self.uncertain_bullish_trade_min_relative_strength):
            if not 0 <= value <= 100:
                raise ValueError("Entry and relative-strength thresholds must be between 0 and 100")
        if not 0 <= self.candidate_min_liquidity_score <= 100 or not 0 <= self.candidate_min_trust_score <= 100:
            raise ValueError("Candidate quality gates must be between 0 and 100")
        if (self.selection_max_trades_per_sector < 1
                or self.selection_stability_lookback_runs < 1
                or not 1 <= self.selection_stability_min_appearances <= self.selection_stability_lookback_runs):
            raise ValueError("Selection sector and stability limits must be positive")
        if self.entry_zone_below_atr < 0 or self.entry_zone_above_atr < 0:
            raise ValueError("Entry-zone ATR allowances cannot be negative")
        if not 0 <= self.option_long_delta_min <= self.option_long_delta_max <= 1:
            raise ValueError("Option long delta range is invalid")
        if self.setup_support_near_percent < 0 or self.entry_confirmation_relative_volume < 0:
            raise ValueError("Setup proximity and volume thresholds cannot be negative")
        if (not 0 <= self.entry_min_close_location <= 1
                or not 0 < self.entry_normal_extension_atr <= self.entry_max_extension_atr
                <= self.entry_no_chase_extension_atr):
            raise ValueError("Entry candle location and ATR extension settings are invalid")
        if self.breakout_retest_tolerance_atr < 0 or self.breakout_consolidation_max_range_atr <= 0:
            raise ValueError("Breakout retest and consolidation thresholds are invalid")
        if self.entry_max_consecutive_bullish_candles < 2:
            raise ValueError("ENTRY_MAX_CONSECUTIVE_BULLISH_CANDLES must be at least two")
        if (not 0 < self.bullish_max_adverse_move_percent < 100
                or not 0 <= self.bullish_min_adverse_hold_probability <= 100
                or self.bullish_barrier_horizon_days < 1
                or self.bullish_barrier_minimum_samples < 20
                or self.bullish_intraday_barrier_minimum_samples < 20):
            raise ValueError("Bullish adverse-move probability settings are invalid")
        if (not 0 < self.bullish_max_technical_stop_percent < 100
                or not 0 <= self.bullish_min_target_before_adverse_probability <= 100
                or not 0 <= self.bullish_min_no_overnight_gap_probability <= 100
                or not 0 <= self.bullish_min_relative_strength_score <= 100):
            raise ValueError("Bullish stock-selection thresholds are invalid")
        if (not 0 < self.bearish_max_technical_stop_percent < 100
                or not 0 <= self.bearish_min_target_before_adverse_probability <= 100
                or not 0 <= self.bearish_min_no_overnight_gap_probability <= 100
                or not -100 <= self.bearish_max_relative_strength_percent <= 100
                or not 0 <= self.bearish_max_sector_score <= 100):
            raise ValueError("Bearish stock-selection thresholds are invalid")
        if not 0 <= self.market_low_confidence_threshold <= self.market_confirmed_confidence_threshold <= 100:
            raise ValueError("Market confidence thresholds are invalid")
        if not (0 <= self.event_stale_multiplier <= self.event_delayed_multiplier
                <= self.event_fresh_multiplier <= 1):
            raise ValueError("Event freshness multipliers must satisfy stale <= delayed <= fresh <= 1")
        event_penalties = (
            self.event_data_complete_no_match_penalty, self.event_data_partial_coverage_penalty,
            self.event_data_not_requested_penalty, self.event_data_primary_unavailable_penalty,
            self.event_data_all_unavailable_penalty, self.event_data_fetch_failed_penalty,
        )
        if any(value < 0 for value in event_penalties):
            raise ValueError("Event data uncertainty penalties cannot be negative")
        event_caps = (self.event_penalty_max_very_low, self.event_penalty_max_low,
                      self.event_penalty_max_medium, self.event_penalty_max_high,
                      self.event_penalty_max_extreme)
        if any(value < 0 for value in event_caps) or any(
                left > right for left, right in zip(event_caps, event_caps[1:])):
            raise ValueError("Event penalty bounds must be non-negative and ascending")
        for value in (self.event_medium_position_multiplier, self.event_high_position_multiplier,
                      self.event_extreme_position_multiplier):
            if not 0 <= value <= 1:
                raise ValueError("Event position multipliers must be between 0 and 1")
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
        # Environment variables set from Windows cmd.exe are sometimes written as
        # `set TRADING_STRATEGY_MODE="both"`, which makes the quote characters part
        # of the value.  Treat surrounding whitespace/quotes as configuration
        # formatting rather than as part of the strategy name.
        raw_strategy_mode = os.getenv("TRADING_STRATEGY_MODE", "both")
        strategy_mode = raw_strategy_mode.strip().strip("'\"").strip().lower()
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
            raise ValueError(
                "TRADING_STRATEGY_MODE must be 'equity', 'short_put', or 'both' "
                f"(received {raw_strategy_mode!r})"
            )
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
            candidate_ranking_mode=os.getenv("CANDIDATE_RANKING_MODE", "EXPECTED_VALUE").upper(),
            quality_grade_a_plus=env_float("QUALITY_GRADE_A_PLUS", 92),
            quality_grade_a=env_float("QUALITY_GRADE_A", 85),
            quality_grade_b_plus=env_float("QUALITY_GRADE_B_PLUS", 80),
            quality_grade_b=env_float("QUALITY_GRADE_B", 75),
            quality_grade_c_plus=env_float("QUALITY_GRADE_C_PLUS", 70),
            quality_grade_c=env_float("QUALITY_GRADE_C", 65),
            readiness_execute_bullish=env_float("READINESS_EXECUTE_BULLISH", 75),
            readiness_execute_neutral=env_float("READINESS_EXECUTE_NEUTRAL", 80),
            readiness_execute_cautious=env_float("READINESS_EXECUTE_CAUTIOUS", 85),
            readiness_execute_strong_bearish=env_float("READINESS_EXECUTE_STRONG_BEARISH", 90),
            readiness_prepare=env_float("READINESS_PREPARE", 70),
            readiness_watch_intraday=env_float("READINESS_WATCH_INTRADAY", 55),
            readiness_wait=env_float("READINESS_WAIT", 40),
            option_approval_min_readiness=env_float("OPTION_APPROVAL_MIN_READINESS", 75),
            option_min_payoff_risk_reward=env_float("OPTION_MIN_PAYOFF_RISK_REWARD", 1),
            option_min_open_interest=env_int("OPTION_MIN_OPEN_INTEREST", 10000),
            option_min_volume=env_int("OPTION_MIN_VOLUME", 1000),
            option_max_bid_ask_spread_percent=env_float("OPTION_MAX_BID_ASK_SPREAD_PERCENT", 5),
            option_max_implied_volatility=env_float("OPTION_MAX_IMPLIED_VOLATILITY", 100),
            option_max_quote_age_seconds=env_float("OPTION_MAX_QUOTE_AGE_SECONDS", 120),
            resistance_clearance_min_atr=env_float("RESISTANCE_CLEARANCE_MIN_ATR", .35),
            entry_min_technical_score=env_float("ENTRY_MIN_TECHNICAL_SCORE", 55),
            entry_min_relative_volume=env_float("ENTRY_MIN_RELATIVE_VOLUME", .75),
            entry_min_sector_score=env_float("ENTRY_MIN_SECTOR_SCORE", 50),
            bearish_bullish_trade_min_relative_strength=env_float("BEARISH_BULLISH_TRADE_MIN_RELATIVE_STRENGTH", 80),
            uncertain_bullish_trade_min_relative_strength=env_float("UNCERTAIN_BULLISH_TRADE_MIN_RELATIVE_STRENGTH", 65),
            stock_trade_absolute_rr_floor=env_float("STOCK_TRADE_ABSOLUTE_RR_FLOOR", 1),
            candidate_min_liquidity_score=env_float("CANDIDATE_MIN_LIQUIDITY_SCORE", 40),
            candidate_min_trust_score=env_float("CANDIDATE_MIN_TRUST_SCORE", 55),
            selection_max_trades_per_sector=env_int("SELECTION_MAX_TRADES_PER_SECTOR", 2),
            selection_stability_lookback_runs=env_int("SELECTION_STABILITY_LOOKBACK_RUNS", 3),
            selection_stability_min_appearances=env_int("SELECTION_STABILITY_MIN_APPEARANCES", 2),
            entry_zone_below_atr=env_float("ENTRY_ZONE_BELOW_ATR", .25),
            entry_zone_above_atr=env_float("ENTRY_ZONE_ABOVE_ATR", .50),
            setup_min_technical_score=env_float("SETUP_MIN_TECHNICAL_SCORE", 55),
            setup_support_near_percent=env_float("SETUP_SUPPORT_NEAR_PERCENT", 4),
            setup_reversal_rsi=env_float("SETUP_REVERSAL_RSI", 35),
            entry_confirmation_relative_volume=env_float("ENTRY_CONFIRMATION_RELATIVE_VOLUME", 1.2),
            entry_min_close_location=env_float("ENTRY_MIN_CLOSE_LOCATION", .60),
            entry_normal_extension_atr=env_float("ENTRY_NORMAL_EXTENSION_ATR", 1.0),
            entry_max_extension_atr=env_float("ENTRY_MAX_EXTENSION_ATR", 1.50),
            entry_no_chase_extension_atr=env_float("ENTRY_NO_CHASE_EXTENSION_ATR", 2.0),
            breakout_retest_tolerance_atr=env_float("BREAKOUT_RETEST_TOLERANCE_ATR", .25),
            breakout_consolidation_max_range_atr=env_float(
                "BREAKOUT_CONSOLIDATION_MAX_RANGE_ATR", .80),
            entry_max_consecutive_bullish_candles=env_int(
                "ENTRY_MAX_CONSECUTIVE_BULLISH_CANDLES", 4),
            bullish_max_adverse_move_percent=env_float("BULLISH_MAX_ADVERSE_MOVE_PERCENT", 3),
            bullish_min_adverse_hold_probability=env_float(
                "BULLISH_MIN_ADVERSE_HOLD_PROBABILITY", 70),
            bullish_barrier_horizon_days=env_int("BULLISH_BARRIER_HORIZON_DAYS", 5),
            bullish_barrier_minimum_samples=env_int("BULLISH_BARRIER_MINIMUM_SAMPLES", 60),
            bullish_intraday_barrier_minimum_samples=env_int(
                "BULLISH_INTRADAY_BARRIER_MINIMUM_SAMPLES", 30),
            bullish_max_technical_stop_percent=env_float(
                "BULLISH_MAX_TECHNICAL_STOP_PERCENT", 3),
            bullish_min_target_before_adverse_probability=env_float(
                "BULLISH_MIN_TARGET_BEFORE_ADVERSE_PROBABILITY", 55),
            bullish_min_no_overnight_gap_probability=env_float(
                "BULLISH_MIN_NO_OVERNIGHT_GAP_PROBABILITY", 95),
            bullish_min_relative_strength_score=env_float(
                "BULLISH_MIN_RELATIVE_STRENGTH_SCORE", 55),
            bearish_max_technical_stop_percent=env_float(
                "BEARISH_MAX_TECHNICAL_STOP_PERCENT", 3),
            bearish_min_target_before_adverse_probability=env_float(
                "BEARISH_MIN_TARGET_BEFORE_ADVERSE_PROBABILITY", 55),
            bearish_min_no_overnight_gap_probability=env_float(
                "BEARISH_MIN_NO_OVERNIGHT_GAP_PROBABILITY", 95),
            bearish_max_relative_strength_percent=env_float(
                "BEARISH_MAX_RELATIVE_STRENGTH_PERCENT", 0),
            bearish_max_sector_score=env_float("BEARISH_MAX_SECTOR_SCORE", 45),
            option_call_resistance_near_percent=env_float("OPTION_CALL_RESISTANCE_NEAR_PERCENT", 1.5),
            option_long_delta_min=env_float("OPTION_LONG_DELTA_MIN", .35),
            option_long_delta_max=env_float("OPTION_LONG_DELTA_MAX", .70),
            option_max_theta_premium_ratio=env_float("OPTION_MAX_THETA_PREMIUM_RATIO", .08),
            option_high_iv_warning=env_float("OPTION_HIGH_IV_WARNING", 55),
            market_low_confidence_threshold=env_float("MARKET_LOW_CONFIDENCE_THRESHOLD", 50),
            market_confirmed_confidence_threshold=env_float("MARKET_CONFIRMED_CONFIDENCE_THRESHOLD", 65),
            news_analysis_required_for_execution=env_bool("NEWS_ANALYSIS_REQUIRED_FOR_EXECUTION", True),
            calibration_min_outcomes=env_int("CALIBRATION_MIN_OUTCOMES", 200),
            event_risk_enabled=env_bool("EVENT_RISK_ENABLED", True),
            event_very_low_max=env_float("EVENT_VERY_LOW_MAX", 20),
            event_low_max=env_float("EVENT_LOW_MAX", 40),
            event_medium_max=env_float("EVENT_MEDIUM_MAX", 60),
            event_extreme_min=env_float("EVENT_EXTREME_MIN", 81),
            crude_daily_move_warning=env_float("CRUDE_DAILY_MOVE_WARNING", 3),
            crude_daily_move_high=env_float("CRUDE_DAILY_MOVE_HIGH", 5),
            crude_daily_move_extreme=env_float("CRUDE_DAILY_MOVE_EXTREME", 8),
            commodity_zscore_high=env_float("COMMODITY_ZSCORE_HIGH", 2),
            commodity_zscore_extreme=env_float("COMMODITY_ZSCORE_EXTREME", 3),
            commodity_multiday_move_high=env_float("COMMODITY_MULTIDAY_MOVE_HIGH", 6),
            event_medium_position_multiplier=env_float("EVENT_MEDIUM_POSITION_MULTIPLIER", .75),
            event_high_position_multiplier=env_float("EVENT_HIGH_POSITION_MULTIPLIER", .5),
            event_extreme_position_multiplier=env_float("EVENT_EXTREME_POSITION_MULTIPLIER", .25),
            event_extreme_block_new_trades=env_bool("EVENT_EXTREME_BLOCK_NEW_TRADES", True),
            event_extreme_block_overnight=env_bool("EVENT_EXTREME_BLOCK_OVERNIGHT", True),
            earnings_same_day_block=env_bool("EARNINGS_SAME_DAY_BLOCK", True),
            event_data_unavailable_readiness_penalty=env_float("EVENT_DATA_UNAVAILABLE_READINESS_PENALTY", 5),
            event_data_stale_readiness_penalty=env_float("EVENT_DATA_STALE_READINESS_PENALTY", 8),
            event_data_complete_no_match_penalty=env_float("EVENT_DATA_COMPLETE_NO_MATCH_PENALTY", 0),
            event_data_partial_coverage_penalty=env_float("EVENT_DATA_PARTIAL_COVERAGE_PENALTY", 2),
            event_data_not_requested_penalty=env_float("EVENT_DATA_NOT_REQUESTED_PENALTY", 0),
            event_data_primary_unavailable_penalty=env_float("EVENT_DATA_PRIMARY_UNAVAILABLE_PENALTY", 3),
            event_data_all_unavailable_penalty=env_float("EVENT_DATA_ALL_UNAVAILABLE_PENALTY", 5),
            event_data_fetch_failed_penalty=env_float("EVENT_DATA_FETCH_FAILED_PENALTY", 8),
            event_penalty_max_very_low=env_float("EVENT_PENALTY_MAX_VERY_LOW", 5),
            event_penalty_max_low=env_float("EVENT_PENALTY_MAX_LOW", 10),
            event_penalty_max_medium=env_float("EVENT_PENALTY_MAX_MEDIUM", 20),
            event_penalty_max_high=env_float("EVENT_PENALTY_MAX_HIGH", 40),
            event_penalty_max_extreme=env_float("EVENT_PENALTY_MAX_EXTREME", 60),
            event_data_max_age_minutes=env_float("EVENT_DATA_MAX_AGE_MINUTES", 60),
            event_stale_after_minutes=env_float("EVENT_STALE_AFTER_MINUTES", 180),
            event_fresh_multiplier=env_float("EVENT_FRESH_MULTIPLIER", 1),
            event_delayed_multiplier=env_float("EVENT_DELAYED_MULTIPLIER", .8),
            event_stale_multiplier=env_float("EVENT_STALE_MULTIPLIER", .5),
            event_default_half_life_hours=env_float("EVENT_DEFAULT_HALF_LIFE_HOURS", 24),
            geopolitical_half_life_hours=env_float("GEOPOLITICAL_HALF_LIFE_HOURS", 72),
            earnings_half_life_hours=env_float("EARNINGS_HALF_LIFE_HOURS", 48),
            commodity_shock_half_life_hours=env_float("COMMODITY_SHOCK_HALF_LIFE_HOURS", 36),
            event_risk_hard_block_score=env_float("EVENT_RISK_HARD_BLOCK_SCORE", 90),
            event_gap_risk_hard_block_score=env_float("EVENT_GAP_RISK_HARD_BLOCK_SCORE", 90),
            event_allow_intraday_exception=env_bool("EVENT_ALLOW_INTRADAY_EXCEPTION", True),
            event_defined_risk_options_only_at_high=env_bool("EVENT_DEFINED_RISK_OPTIONS_ONLY_AT_HIGH", True),
            event_allow_test_overrides=env_bool("EVENT_ALLOW_TEST_OVERRIDES", False),
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
