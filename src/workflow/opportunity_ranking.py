"""Independent quality, actionability, and option-selling rankings."""

from __future__ import annotations

from typing import Any


ACTIONABILITY_BUCKETS = {
    "TRADE_READY": 5,
    "PREPARE": 4,
    "WAIT_FOR_CONFIRMATION": 3,
    "WATCHLIST": 2,
    "AVOID": 1,
}


def actionability_bucket(candidate: dict[str, Any]) -> tuple[str, int]:
    if candidate.get("status") == "TRADE" or candidate.get("trade_eligibility", {}).get("eligible"):
        return "TRADE_READY", ACTIONABILITY_BUCKETS["TRADE_READY"]
    action = str(candidate.get("final_action") or candidate.get("action") or "").upper()
    execution = str(candidate.get("execution_status") or "").upper()
    if action == "PREPARE":
        return "PREPARE", ACTIONABILITY_BUCKETS["PREPARE"]
    if action in {"WAIT_FOR_CONFIRMATION", "NO_TRADE"}:
        return "WAIT_FOR_CONFIRMATION", ACTIONABILITY_BUCKETS["WAIT_FOR_CONFIRMATION"]
    if candidate.get("status") == "WATCHLIST" or action == "WATCHLIST":
        return "WATCHLIST", ACTIONABILITY_BUCKETS["WATCHLIST"]
    if execution == "PREPARE":
        return "PREPARE", ACTIONABILITY_BUCKETS["PREPARE"]
    return "AVOID", ACTIONABILITY_BUCKETS["AVOID"]


def actionability_score(candidate: dict[str, Any]) -> float:
    """Score today's entry without duplicating any analytical indicator."""
    confirmation = float((candidate.get("entry_confirmation") or {}).get("score") or 0)
    score = (
        float(candidate.get("execution_readiness_score") or 0) * .35
        + float(candidate.get("final_candidate_score") or 0) * .25
        + confirmation * .20
        + float(candidate.get("risk_reward_quality_score") or 0) * .10
        + float(candidate.get("path_quality_score") or 0) * .10
    )
    return round(max(0.0, min(100.0, score)), 2)


def option_selling_eligibility(candidate: dict[str, Any]) -> tuple[bool, str]:
    approval = candidate.get("option_trade_approval") or {}
    structure = candidate.get("option_structure") or {}
    strategy = candidate.get("option_strategy") or {}
    if not approval.get("approved"):
        codes = approval.get("rejection_codes") or []
        return False, ", ".join(codes) if codes else "OPTION_NOT_APPROVED"
    if not structure.get("valid"):
        return False, "INVALID_OPTION_STRUCTURE"
    if not strategy.get("available"):
        return False, str(strategy.get("reason") or "OPTION_STRATEGY_UNAVAILABLE")
    return True, "ELIGIBLE"


def annotate_opportunity_rankings(candidates: list[dict[str, Any]]) -> None:
    """Attach three ranks without allowing quality to masquerade as readiness."""
    for candidate in candidates:
        bucket, priority = actionability_bucket(candidate)
        candidate["actionability_bucket"] = bucket
        candidate["actionability_bucket_priority"] = priority
        candidate["actionability_score"] = actionability_score(candidate)
        eligible, reason = option_selling_eligibility(candidate)
        candidate["option_selling_eligible"] = eligible
        candidate["option_selling_exclusion_reason"] = None if eligible else reason

    quality_order = sorted(
        candidates,
        key=lambda item: (
            float(item.get("final_candidate_score") or 0),
            float(item.get("quality_score") or 0),
            str(item.get("symbol") or ""),
        ),
        reverse=True,
    )
    action_order = sorted(
        candidates,
        key=lambda item: (
            int(item["actionability_bucket_priority"]),
            float(item["actionability_score"]),
            float(item.get("final_candidate_score") or 0),
            str(item.get("symbol") or ""),
        ),
        reverse=True,
    )
    option_order = sorted(
        (item for item in candidates if item["option_selling_eligible"]),
        key=lambda item: (
            float(item.get("option_sell_suitability_score") or 0),
            float(item["actionability_score"]),
            str(item.get("symbol") or ""),
        ),
        reverse=True,
    )
    for rank, candidate in enumerate(quality_order, 1):
        candidate["quality_rank"] = rank
    for rank, candidate in enumerate(action_order, 1):
        candidate["actionability_rank"] = rank
    for candidate in candidates:
        candidate["option_selling_rank"] = None
    for rank, candidate in enumerate(option_order, 1):
        candidate["option_selling_rank"] = rank


def actionability_sort_key(candidate: dict[str, Any]) -> tuple:
    return (
        int(candidate.get("actionability_bucket_priority") or 0),
        float(candidate.get("actionability_score") or 0),
        float(candidate.get("final_candidate_score") or 0),
    )
