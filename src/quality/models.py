"""Typed models for explainable stock-selection quality."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class QualityScore:
    """One normalized score with explicit availability and evidence."""

    score: float | None
    status: str
    confidence: float = 100.0
    factors: dict[str, float | None] = field(default_factory=dict)
    reason_codes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FundamentalSnapshot:
    symbol: str
    market_cap: float | None = None
    revenue_growth: float | None = None
    profit_growth: float | None = None
    roe: float | None = None
    roce: float | None = None
    debt_to_equity: float | None = None
    total_debt: float | None = None
    interest_coverage: float | None = None
    operating_cash_flow: float | None = None
    free_cash_flow: float | None = None
    promoter_pledge: float | None = None
    pe_ratio: float | None = None
    sector_pe: float | None = None
    delivery_percent: float | None = None
    monthly_delivery_percent: float | None = None
    fii_holding_percent: float | None = None
    dii_holding_percent: float | None = None
    promoter_holding_percent: float | None = None
    fii_holding_change_pct_points: float | None = None
    dii_holding_change_pct_points: float | None = None
    promoter_holding_change_pct_points: float | None = None
    quarterly_revenue_growth_pct: tuple[float, ...] | None = None
    quarterly_profit_growth_pct: tuple[float, ...] | None = None
    commentary_strength: str | None = None
    block_deal_price_impact: bool | None = None
    source: str = "UNKNOWN"
    as_of: str | None = None
    evidence: dict[str, dict[str, Any]] = field(default_factory=dict)


class FundamentalDataProvider(Protocol):
    def get_fundamentals(self, symbol: str) -> FundamentalSnapshot | None:
        """Return published fundamentals or None; never synthesize values."""


@dataclass(frozen=True)
class CandidateQualityAssessment:
    """All independent quality concepts required by the selection contract."""

    scores: dict[str, QualityScore]
    hard_gates: dict[str, bool]
    strengths: list[str]
    risks: list[str]
    hard_gate_failures: list[str]
    analysis_confidence_score: float
    analysis_confidence_status: str
    final_candidate_score: float
    directional_trade_suitability_score: float
    option_sell_suitability_score: float | None
    composite_recommendation: str
    reason_codes: list[str]
    timings: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["scores"] = {name: score.to_dict() for name, score in self.scores.items()}
        return payload
