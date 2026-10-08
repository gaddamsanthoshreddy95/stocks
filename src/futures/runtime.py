"""Operational settings kept separate from trading thresholds."""
from dataclasses import dataclass, fields
from math import isfinite
import os

MODES = ('FULL_RESEARCH', 'DAILY_PREP', 'LIVE_SCAN', 'AFTER_MARKET_RESEARCH')


@dataclass(frozen=True)
class ScanRuntime:
    cache_directory: str = '.cache/futures_prepared'
    legacy_reports_directory: str = 'reports'
    research_ttl_seconds: int = 7*86400
    history_ttl_seconds: int = 7*86400
    news_ttl_seconds: int = 900
    workers: int = 4
    request_timeout_seconds: float = 5
    retries: int = 1
    live_deadline_seconds: float = 120
    after_market_deadline_seconds: float = 300
    after_market_news_budget_seconds: float = 60
    after_market_analysis_budget_seconds: float = 60
    live_news_budget_seconds: float = 12
    candle_refresh_seconds: int = 240
    quote_interval_seconds: float = 1.05
    historical_interval_seconds: float = .35
    require_margin: bool = True
    movement_basis: str = 'FUTURES'
    ledger_path: str = 'data/futures_executions.sqlite3'
    reconciliation_max_age_seconds: int = 120

    def __post_init__(self):
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and (not isfinite(value) or value < 0):
                raise ValueError(f'{f.name} must be non-negative')
        if not 1 <= self.workers <= 16 or self.request_timeout_seconds <= 0 or self.retries > 3:
            raise ValueError('Invalid concurrency, timeout or retry limit')
        if int(self.workers)!=self.workers or int(self.retries)!=self.retries:
            raise ValueError('Workers/retries must be integers')
        if self.quote_interval_seconds < 1 or self.historical_interval_seconds < 1/3:
            raise ValueError('Kite pacing must respect quote 1/s and history 3/s limits')
        if self.movement_basis != 'FUTURES':
            raise ValueError('Execution movement_basis must be FUTURES; underlying prices are research only')
        if not self.ledger_path or self.reconciliation_max_age_seconds <= 0:
            raise ValueError('Persistent ledger path and positive reconciliation freshness required')

    @classmethod
    def from_env(cls):
        defaults = cls()
        values = {}
        for f in fields(cls):
            base = getattr(defaults, f.name)
            raw = os.getenv('FUTURES_RUNTIME_'+f.name.upper())
            values[f.name] = base if raw is None else raw.lower() in {'true','1','yes'} if isinstance(base, bool) else type(base)(raw)
        return cls(**values)
