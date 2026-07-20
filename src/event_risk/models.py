from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class StrEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class EventCategory(StrEnum):
    COMPANY = "COMPANY"
    EARNINGS = "EARNINGS"
    CORPORATE_ACTION = "CORPORATE_ACTION"
    REGULATORY = "REGULATORY"
    COMMODITY = "COMMODITY"
    MACRO = "MACRO"
    CENTRAL_BANK = "CENTRAL_BANK"
    ECONOMIC_CALENDAR = "ECONOMIC_CALENDAR"
    GEOPOLITICAL = "GEOPOLITICAL"
    SECTOR = "SECTOR"
    MARKET_WIDE = "MARKET_WIDE"
    CURRENCY = "CURRENCY"
    SHIPPING = "SHIPPING"
    WEATHER = "WEATHER"
    POLICY = "POLICY"
    ELECTION = "ELECTION"
    CYBER_SECURITY = "CYBER_SECURITY"


class EventSeverity(StrEnum):
    VERY_LOW = "VERY_LOW"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


class EventDirection(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    MIXED = "MIXED"
    UNCERTAIN = "UNCERTAIN"
    VOLATILITY_ONLY = "VOLATILITY_ONLY"
    POSITIVE_BUT_VOLATILE = "POSITIVE_BUT_VOLATILE"
    NEGATIVE_AND_VOLATILE = "NEGATIVE_AND_VOLATILE"
    NEUTRAL = "NEUTRAL"


class EventStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    ACTIVE = "ACTIVE"
    ESCALATING = "ESCALATING"
    DE_ESCALATING = "DE_ESCALATING"
    RESOLVED = "RESOLVED"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class EventSourceType(StrEnum):
    NEWS = "NEWS"
    ECONOMIC_CALENDAR = "ECONOMIC_CALENDAR"
    COMPANY_CALENDAR = "COMPANY_CALENDAR"
    MARKET_DATA = "MARKET_DATA"
    COMMODITY_DATA = "COMMODITY_DATA"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"
    CACHED_SOURCE = "CACHED_SOURCE"
    FALLBACK_RULE = "FALLBACK_RULE"


class EventImpactDuration(StrEnum):
    INTRADAY = "INTRADAY"
    ONE_TO_THREE_DAYS = "ONE_TO_THREE_DAYS"
    THREE_TO_SEVEN_DAYS = "THREE_TO_SEVEN_DAYS"
    ONE_TO_FOUR_WEEKS = "ONE_TO_FOUR_WEEKS"
    ONE_TO_THREE_MONTHS = "ONE_TO_THREE_MONTHS"
    LONG_TERM = "LONG_TERM"


@dataclass
class EventRiskItem:
    event_id: str
    title: str
    normalized_event_type: str
    category: EventCategory
    severity: EventSeverity
    direction: EventDirection = EventDirection.UNCERTAIN
    status: EventStatus = EventStatus.ACTIVE
    source_type: EventSourceType = EventSourceType.NEWS
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    event_start: datetime | None = None
    event_end: datetime | None = None
    expected_duration: EventImpactDuration = EventImpactDuration.ONE_TO_THREE_DAYS
    raw_score: float = 0.0
    decayed_score: float = 0.0
    confidence: float = 0.5
    freshness_score: float = 100.0
    materiality: float = 1.0
    gap_risk: float = 0.0
    affected_sectors: list[str] = field(default_factory=list)
    affected_symbols: list[str] = field(default_factory=list)
    affected_regions: list[str] = field(default_factory=list)
    affected_commodities: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    source_titles: list[str] = field(default_factory=list)
    source_urls_or_ids: list[str] = field(default_factory=list)
    is_scheduled: bool = False
    is_confirmed: bool = False
    is_duplicate: bool = False
    duplicate_of: str | None = None
    half_life_hours: float = 24.0
    decay_model: str = "EXPONENTIAL"
    expiry_time: datetime | None = None
    canonical_event_id: str | None = None
    related_article_ids: list[str] = field(default_factory=list)
    event_version: int = 1
    meaningful_update_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        for key, value in list(row.items()):
            if isinstance(value, Enum):
                row[key] = value.value
            elif isinstance(value, datetime):
                row[key] = value.isoformat()
        return row


@dataclass
class EventRiskAssessment:
    symbol: str
    company_type: str | None
    sector: str | None
    event_risk_score: float
    event_risk_level: str
    event_direction: str
    event_confidence: float
    event_freshness: float
    positive_event_score: float
    negative_event_score: float
    volatility_event_score: float
    gap_risk_score: float
    base_readiness: float
    adjusted_readiness: float
    readiness_penalty: float
    readiness_bonus: float
    position_size_multiplier: float
    effective_risk_multiplier: float
    probability_adjustment: float
    hard_block: bool
    block_reason: str | None
    overnight_hold_allowed: bool
    overnight_risk_reason: str | None
    strategy_restrictions: list[str]
    matched_events: list[dict[str, Any]]
    reasons: list[str]
    warnings: list[str]
    data_state: str
    primary_category: str = "NONE"
    impact_duration: str = "INTRADAY"
    decay_model: str = "EXPONENTIAL"
    current_decay_factor: float = 1.0
    directional_bonus: float = 0.0
    volatility_penalty: float = 0.0
    gap_risk_penalty: float = 0.0
    uncertainty_penalty: float = 0.0
    manual_override_applied: bool = False
    stock_specific_score: float = 0.0
    sector_specific_score: float = 0.0
    market_wide_score: float = 0.0
    classification_confidence: float = 0.0
    source_freshness: float = 0.0
    effective_confidence: float = 0.0
    freshness_state: str = "UNAVAILABLE"
    market_wide_scaling: dict[str, Any] = field(default_factory=dict)
    event_data_uncertainty_penalty: float = 0.0
    primary_event_freshness_state: str = "UNAVAILABLE"
    aggregate_source_coverage_state: str = "UNAVAILABLE"
    supporting_sources_freshness_state: str = "UNAVAILABLE"
    event_data_availability_state: str = "UNAVAILABLE"
    overnight_block_cause: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
