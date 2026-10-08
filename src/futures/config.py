"""Configurable hypotheses; scores are not calibrated probabilities."""
from dataclasses import dataclass, fields
from math import isfinite
import os


@dataclass(frozen=True)
class FuturesScanConfig:
    minimum_score: float = 60
    watch_score: float = 40
    review_per_direction: int = 20
    minimum_history: int = 200
    minimum_historical_signals: int = 30
    target_fraction: float = .003
    stop_fraction: float = .002
    minimum_net_rr: float = 1
    maximum_move_percent: float = 2
    maximum_atr_consumed: float = 1
    maximum_extension_atr: float = 1.5
    maximum_vwap_extension_percent: float = 1
    minimum_adx: float = 25
    minimum_rvol: float = 1.2
    bearish_rsi_low: float = 30
    bearish_rsi_high: float = 45
    bullish_rsi_low: float = 55
    bullish_rsi_high: float = 70
    maximum_slippage_bps: float = 10
    maximum_participation: float = .1
    short_covering_price_percent: float = .05
    short_covering_oi_percent: float = .1
    short_oversold_rsi: float = 30
    short_fresh_breakdown_bars: int = 2
    short_retest_tolerance_atr: float = .15
    short_maximum_trigger_distance_percent: float = .1
    maximum_lots: int = 1
    intraday_exit_hour: int = 15
    intraday_exit_minute: int = 20
    entry_cutoff_hour: int = 15
    entry_cutoff_minute: int = 15
    manual_exit_warning_minutes: int = 10
    maximum_daily_entries: int = 2
    out_of_sample_fraction: float = .3
    bearish_trend_weight: float = .25
    bearish_momentum_weight: float = .20
    bearish_volume_weight: float = .15
    bearish_structure_weight: float = .15
    bearish_sector_weight: float = .10
    bearish_opportunity_weight: float = .15
    bullish_trend_weight: float = .25
    bullish_momentum_weight: float = .20
    bullish_volume_weight: float = .15
    bullish_structure_weight: float = .15
    bullish_sector_weight: float = .10
    bullish_opportunity_weight: float = .15

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            if not isfinite(value) or value < 0:
                raise ValueError(f'{field.name} must be finite and non-negative')
        for direction in ('bullish', 'bearish'):
            if abs(sum(self.weights(direction).values()) - 1) > 1e-8:
                raise ValueError(f'{direction} weights must sum to one')
        for name in ('review_per_direction', 'minimum_history', 'minimum_historical_signals', 'maximum_lots', 'short_fresh_breakdown_bars'):
            if getattr(self, name) < 1 or int(getattr(self, name)) != getattr(self, name):
                raise ValueError(f'{name} must be a positive integer')
        if self.minimum_history < 200:
            raise ValueError('minimum_history must allow EMA200 warmup')
        if not 0 < self.target_fraction < 1 or not 0 < self.stop_fraction < 1:
            raise ValueError('target/stop fractions must be between zero and one')
        if not 0 < self.out_of_sample_fraction < 1 or not 0 < self.maximum_participation <= 1:
            raise ValueError('out-of-sample fraction and participation are invalid')
        if not 0 <= self.watch_score <= self.minimum_score <= 100:
            raise ValueError('score thresholds are invalid')
        if not 0 <= self.bearish_rsi_low < self.bearish_rsi_high <= 50:
            raise ValueError('bearish RSI range is invalid')
        if not 0 <= self.short_oversold_rsi < 50:
            raise ValueError('SHORT oversold floor must be below 50')
        if not 50 <= self.bullish_rsi_low < self.bullish_rsi_high <= 100:
            raise ValueError('bullish RSI range is invalid')
        if (int(self.intraday_exit_hour) != self.intraday_exit_hour
                or int(self.intraday_exit_minute) != self.intraday_exit_minute
                or not 0 <= self.intraday_exit_hour <= 23
                or not 0 <= self.intraday_exit_minute <= 59
                or not (9, 20) <= (self.intraday_exit_hour, self.intraday_exit_minute) < (15, 30)):
            raise ValueError('intraday exit time must be between 09:20 and 15:29 IST')
        if self.maximum_daily_entries != 2:
            raise ValueError('The approved global limit is exactly two executed entry orders per day')
        if (self.entry_cutoff_hour != int(self.entry_cutoff_hour) or self.entry_cutoff_minute != int(self.entry_cutoff_minute)
                or not 0 <= self.entry_cutoff_hour <= 23 or not 0 <= self.entry_cutoff_minute <= 59
                or not (9, 20) <= (self.entry_cutoff_hour, self.entry_cutoff_minute) <= (self.intraday_exit_hour, self.intraday_exit_minute)):
            raise ValueError('Entry cutoff must be between 09:20 and the intraday exit cutoff')
        if self.manual_exit_warning_minutes != int(self.manual_exit_warning_minutes):
            raise ValueError('Manual exit warning minutes must be an integer')

    def weights(self, direction):
        return {name: getattr(self, f'{direction}_{name}_weight')
                for name in ('trend', 'momentum', 'volume', 'structure', 'sector', 'opportunity')}

    @classmethod
    def from_env(cls):
        defaults = cls()
        return cls(**{field.name: (int if isinstance(getattr(defaults, field.name), int) else float)(
            os.getenv('FUTURES_SCAN_' + field.name.upper(), getattr(defaults, field.name)))
            for field in fields(cls)})
