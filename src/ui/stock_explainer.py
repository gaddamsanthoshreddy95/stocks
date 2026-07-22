"""Evidence-grounded explanations for daily-report stock decisions."""

from __future__ import annotations

import re
from typing import Any


def _normalise_symbol(value: Any) -> str:
    return str(value or "").strip().upper().removesuffix(".NS")


def report_candidates(report: dict[str, Any]) -> dict[str, tuple[str, dict[str, Any]]]:
    """Index every final report outcome, including non-recommendations."""
    candidates: dict[str, tuple[str, dict[str, Any]]] = {}
    for bucket, label in (("trades", "TRADE"), ("watchlist", "WATCHLIST"),
                          ("rejected", "REJECTED")):
        for candidate in report.get(bucket, []) or []:
            symbol = _normalise_symbol(candidate.get("symbol"))
            if symbol:
                candidates[symbol] = (label, candidate)
    return candidates


def _number(value: Any, digits: int = 1, suffix: str = "") -> str:
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "unavailable"


def _money(value: Any) -> str:
    try:
        return f"₹{float(value):,.2f}"
    except (TypeError, ValueError):
        return "unavailable"


def _unique_text(values: list[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _find_symbol(question: str, candidates: dict[str, Any]) -> str | None:
    normalised = _normalise_symbol(question)
    if normalised in candidates:
        return normalised
    words = set(re.findall(r"[A-Z0-9&-]+", question.upper()))
    matches = [symbol for symbol in candidates if symbol in words]
    return matches[0] if len(matches) == 1 else None


def explain_stock_question(report: dict[str, Any] | None, question: str) -> str:
    """Answer a stock question strictly from the supplied report snapshot."""
    if not report:
        return "No report is loaded. Generate or open a daily report first."
    candidates = report_candidates(report)
    symbol = _find_symbol(question, candidates)
    if not symbol:
        available = ", ".join(sorted(candidates)[:20])
        if not candidates:
            return "This report contains no final candidates to explain."
        return ("Please include one stock symbol from this report. Available symbols: "
                f"{available}{'…' if len(candidates) > 20 else ''}.")

    bucket, candidate = candidates[symbol]
    recorded_status = str(candidate.get("status") or bucket).upper()
    action = str(candidate.get("final_action") or candidate.get("action") or "UNAVAILABLE").upper()
    selection_status = str(candidate.get("selection_status") or
                           (candidate.get("entry_selection") or {}).get("status") or "UNAVAILABLE")
    executable = bucket == "TRADE" and recorded_status == "TRADE"
    if executable:
        verdict = f"{symbol} was an executable TRADE candidate with action {action}."
    elif bucket == "WATCHLIST":
        verdict = (f"{symbol} was not an executable trade. It was placed on WATCHLIST "
                   f"with entry status {selection_status}.")
    else:
        verdict = f"{symbol} was not suggested for execution; it was REJECTED with action {action}."

    positives: list[Any] = []
    selection_reason = candidate.get("selection_reason")
    if selection_reason:
        positives.append(selection_reason)
    setup = candidate.get("setup") or candidate.get("strategy")
    if setup:
        positives.append(f"Detected setup: {setup}")
    scores = candidate.get("scores") or {}
    quality = candidate.get("quality_score", scores.get("quality_score"))
    technical = candidate.get("technical_score", scores.get("technical_score"))
    readiness = candidate.get("execution_readiness_score", scores.get("execution_readiness_score"))
    relative = candidate.get("relative_strength") or {}
    filters = candidate.get("stock_selection_filters") or {}
    entry = candidate.get("entry_confirmation") or candidate.get("entry_selection") or {}
    if technical is not None:
        positives.append(f"Technical score: {_number(technical)}/100")
    if quality is not None:
        positives.append(f"Quality score: {_number(quality)}/100")
    if readiness is not None:
        positives.append(f"Execution readiness: {_number(readiness)}/100")
    if relative.get("score") is not None:
        positives.append(f"Relative strength: {_number(relative.get('score'))} ({relative.get('status', 'unclassified')})")
    if filters.get("passed") is True:
        positives.append("Capital-protection stock-selection filters passed")
    passed_entry_checks = entry.get("passed_checks") or []
    if entry.get("passed") is True:
        positives.append(f"Entry confirmation passed ({len(passed_entry_checks)} checks)")
    elif passed_entry_checks:
        positives.append(f"Entry confirmation passed {len(passed_entry_checks)} individual checks, but not every gate")

    blockers = _unique_text([
        *(filters.get("blocking_reasons") or []), *(filters.get("failed_checks") or []),
        *(candidate.get("rejection_reasons") or []), *(candidate.get("reasons") or []),
        *(entry.get("failed_checks") or []),
    ])
    if executable:
        blockers = [item for item in blockers if item not in (candidate.get("reasons") or [])]

    levels = dict(candidate.get("levels") or {})
    targets = candidate.get("targets") or []
    levels.setdefault("entry", candidate.get("entry"))
    levels.setdefault("stop_loss", candidate.get("stop"))
    levels.setdefault("target_1", targets[0] if targets else candidate.get("target"))
    adverse = candidate.get("adverse_move_risk") or {}
    lines = [f"**Decision: {verdict}**", "", "**Why it reached the final list**"]
    if positives:
        lines.extend(f"- {item}" for item in _unique_text(positives))
    else:
        lines.append("- The report recorded no positive decision evidence.")
    lines.extend(["", "**Execution and risk evidence**",
                  f"- Entry {_money(levels.get('entry'))}; stop {_money(levels.get('stop_loss'))}; "
                  f"target 1 {_money(levels.get('target_1'))}; risk/reward {_number(levels.get('risk_reward'))}:1."])
    if adverse:
        lines.append(
            "- Estimated probability of staying inside the adverse-move barrier: "
            f"{_number(adverse.get('probability_stays_above_adverse_barrier'), 1, '%')}; "
            "target before barrier: "
            f"{_number(adverse.get('probability_target_before_adverse_barrier'), 1, '%')}; "
            "no overnight gap beyond barrier: "
            f"{_number(adverse.get('probability_no_overnight_gap_beyond_barrier'), 1, '%')}."
        )
    if blockers:
        lines.extend(["", "**Warnings / failed gates**"])
        lines.extend(f"- {item}" for item in blockers[:8])
    elif executable:
        lines.extend(["", "All recorded final execution gates passed for this report snapshot."])

    generated = report.get("generated_at") or report.get("timestamp") or report.get("date") or "unknown"
    lines.extend(["", f"Evidence source: report run `{report.get('run_id', 'unknown')}`, generated {generated}. "
                  "This explains that snapshot; it does not guarantee a target or replace a fresh quote check."])
    return "\n".join(lines)
