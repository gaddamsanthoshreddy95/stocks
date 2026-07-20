"""Authoritative final decision and pre-presentation consistency checks."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class FinalAction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    PREPARE = "PREPARE"
    WATCHLIST = "WATCHLIST"
    WAIT_FOR_CONFIRMATION = "WAIT_FOR_CONFIRMATION"
    NO_TRADE = "NO_TRADE"
    REJECT = "REJECT"


@dataclass(frozen=True)
class EntryConfirmationResult:
    passed: bool
    score: float
    passed_checks: tuple[str, ...]
    failed_checks: tuple[str, ...]
    timestamp: str

    @classmethod
    def from_checks(cls, checks: dict[str, bool], required: bool = True) -> "EntryConfirmationResult":
        passed_checks = tuple(str(name) for name, passed in checks.items() if passed)
        failed_checks = tuple(str(name) for name, passed in checks.items() if not passed)
        passed = (not failed_checks) or not required
        total = len(checks)
        score = round(100 * len(passed_checks) / total, 2) if total else (100.0 if passed else 0.0)
        return cls(passed, score, passed_checks, failed_checks, datetime.now(timezone.utc).isoformat())

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "EntryConfirmationResult":
        return cls(bool(value.get("passed")), float(value.get("score", 0)),
                   tuple(value.get("passed_checks") or ()), tuple(value.get("failed_checks") or ()),
                   str(value.get("timestamp") or datetime.now(timezone.utc).isoformat()))

    @classmethod
    def from_setup(cls, setup_evaluation: dict[str, Any], required: bool = True) -> "EntryConfirmationResult":
        canonical = setup_evaluation.get("entry_confirmation")
        if isinstance(canonical, dict):
            result = cls.from_dict(canonical)
            return result if required else cls(True, result.score, result.passed_checks,
                                               result.failed_checks, result.timestamp)
        stage = setup_evaluation.get("stage_2") or {}
        checks = stage.get("checks") or {}
        if checks:
            result = cls.from_checks(checks, required=required)
            if "eligible" not in stage:
                return result
            passed_checks, failed_checks = result.passed_checks, result.failed_checks
        else:
            failed_checks = tuple(str(item) for item in stage.get("missing", []))
            passed_checks = ()
        passed = bool(stage.get("eligible", False)) or not required
        total = len(passed_checks) + len(failed_checks)
        score = round(100 * len(passed_checks) / total, 2) if total else (100.0 if passed else 0.0)
        return cls(passed, score, passed_checks, failed_checks, datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["passed_checks"] = list(self.passed_checks)
        row["failed_checks"] = list(self.failed_checks)
        return row


@dataclass(frozen=True)
class FinalDecision:
    action: FinalAction
    executable: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)
    rejection_reasons: tuple[str, ...] = field(default_factory=tuple)
    decided_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    engine: str = "FinalDecisionEngine/v1"

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["action"] = self.action.value
        row["reasons"] = list(self.reasons)
        row["rejection_reasons"] = list(self.rejection_reasons)
        return row


class FinalDecisionEngine:
    """The only authority allowed to turn recommendation evidence into an action."""

    @staticmethod
    def decide(*, direction: str, entry: EntryConfirmationResult, readiness_status: str,
               eligible: bool, hard_block: bool, critical_failure: bool,
               news_complete: bool, event_complete: bool = True,
               news_waived: bool = False, event_waived: bool = False,
               reasons: list[str] | None = None) -> FinalDecision:
        reasons = list(dict.fromkeys(reasons or []))
        if critical_failure:
            return FinalDecision(FinalAction.REJECT, False, tuple(reasons), tuple(reasons))
        if hard_block:
            return FinalDecision(FinalAction.NO_TRADE, False, tuple(reasons), tuple(reasons))
        if (not news_complete and not news_waived) or (not event_complete and not event_waived):
            missing = []
            if not news_complete and not news_waived: missing.append("news")
            if not event_complete and not event_waived: missing.append("event")
            reason = f"{' and '.join(missing).title()} analysis is incomplete and was not explicitly waived."
            return FinalDecision(FinalAction.WAIT_FOR_CONFIRMATION, False,
                                 tuple([*reasons, reason]))
        if not entry.passed:
            return FinalDecision(FinalAction.WAIT_FOR_CONFIRMATION, False, tuple(reasons))
        if readiness_status == "EXECUTE" and eligible:
            action = FinalAction.SELL if direction == "BEARISH" else FinalAction.BUY
            return FinalDecision(action, True, tuple(reasons))
        if readiness_status == "PREPARE":
            return FinalDecision(FinalAction.PREPARE, False, tuple(reasons))
        if readiness_status in {"WATCH_INTRADAY", "WATCHLIST"}:
            return FinalDecision(FinalAction.WATCHLIST, False, tuple(reasons))
        if readiness_status == "WAIT":
            return FinalDecision(FinalAction.WAIT_FOR_CONFIRMATION, False, tuple(reasons))
        return FinalDecision(FinalAction.NO_TRADE, False, tuple(reasons), tuple(reasons))


class ConsistencyError(ValueError):
    """Raised when a recommendation contains an impossible executable state."""


class FinalConsistencyValidator:
    EXECUTABLE_ACTIONS = {FinalAction.BUY.value, FinalAction.SELL.value}

    @classmethod
    def validate(cls, trade: dict[str, Any]) -> None:
        errors: list[str] = []
        action = trade.get("final_action")
        eligibility = trade.get("trade_eligibility") or {}
        entry = trade.get("entry_confirmation") or {}
        option_approval = trade.get("option_trade_approval") or {}
        option_structure = trade.get("option_structure") or {}
        event = trade.get("event_risk") or {}
        news = trade.get("news") or {}
        position = trade.get("risk") or {}
        if action in cls.EXECUTABLE_ACTIONS and not eligibility.get("eligible"):
            errors.append("executable action has non-executable eligibility")
        if option_approval.get("status") == "APPROVED" and not entry.get("passed"):
            errors.append("option approved with failed entry confirmation")
        if option_approval.get("status") == "APPROVED" and not option_structure.get("valid"):
            errors.append("option approved with invalid structure")
        if option_approval.get("status") == "APPROVED" and option_structure.get("quotes_fresh") is False:
            errors.append("stale option quotes marked approved")
        if event.get("event_risk_level") == "VERY_LOW":
            explained = (float(event.get("market_wide_score", 0)) > 0
                         or float(event.get("event_data_uncertainty_penalty", 0)) > 0)
            if float(event.get("volatility_penalty", 0)) > 0 or (float(event.get("readiness_penalty", 0)) > 5 and not explained):
                errors.append("VERY_LOW event has unexplained high penalties")
        if news.get("news_state") in {"NOT_FETCHED", "FETCH_FAILED"} and news.get("sentiment") == "NEUTRAL":
            errors.append("unavailable news represented as neutral sentiment")
        if action not in cls.EXECUTABLE_ACTIONS and int(position.get("quantity", 0) or 0) != 0:
            errors.append("blocked/non-executable trade has non-zero executable position")
        if trade.get("action") != action or trade.get("recommendation") != action:
            errors.append("multiple conflicting final actions")
        if errors:
            raise ConsistencyError("; ".join(errors))
