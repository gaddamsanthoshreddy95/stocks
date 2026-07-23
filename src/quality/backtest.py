"""Frozen-date LEGACY versus COMPOSITE ranking comparison utilities."""

from __future__ import annotations

from typing import Any, Callable

import pandas as pd


def slice_as_of(data: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """Return only observations known on or before an evaluation timestamp."""
    return data.loc[data.index <= as_of].copy()


def compare_rankings(
    candidates: list[dict[str, Any]], limit: int,
    legacy_key: Callable[[dict[str, Any]], tuple],
) -> dict[str, Any]:
    legacy = sorted(candidates, key=legacy_key, reverse=True)
    composite = sorted(
        candidates,
        key=lambda item: (
            float(item.get("final_candidate_score") or 0),
            float(item.get("entry_readiness_score") or 0),
            float(item.get("risk_reward_quality_score") or 0),
        ),
        reverse=True,
    )
    legacy_rank = {item["symbol"]: index for index, item in enumerate(legacy, 1)}
    composite_rank = {item["symbol"]: index for index, item in enumerate(composite, 1)}
    legacy_symbols = [item["symbol"] for item in legacy[:limit]]
    composite_symbols = [item["symbol"] for item in composite[:limit]]
    return {
        "legacy_shortlist": legacy_symbols,
        "composite_shortlist": composite_symbols,
        "overlap": len(set(legacy_symbols) & set(composite_symbols)),
        "rows": [{
            "symbol": symbol,
            "legacy_rank": legacy_rank[symbol],
            "composite_rank": composite_rank[symbol],
            "rank_difference": legacy_rank[symbol] - composite_rank[symbol],
            "legacy_selected": symbol in legacy_symbols,
            "composite_selected": symbol in composite_symbols,
        } for symbol in legacy_rank],
    }


def outcome_metrics(outcomes: list[dict[str, float]]) -> dict[str, float]:
    """Calculate comparison metrics without fitting or optimizing weights."""
    returns = [float(item.get("return_percent", 0)) for item in outcomes]
    wins = [value for value in returns if value > 0]
    losses = [abs(value) for value in returns if value <= 0]
    win_rate = len(wins) / len(returns) if returns else 0
    average_win = sum(wins) / len(wins) if wins else 0
    average_loss = sum(losses) / len(losses) if losses else 0
    expectancy = win_rate * average_win - (1 - win_rate) * average_loss
    equity, peak, maximum_drawdown = 0.0, 0.0, 0.0
    for value in returns:
        equity += value
        peak = max(peak, equity)
        maximum_drawdown = max(maximum_drawdown, peak - equity)
    return {
        "count": len(returns), "win_rate": round(win_rate * 100, 2),
        "average_gain": round(average_win, 3),
        "average_loss": round(average_loss, 3),
        "expectancy": round(expectancy, 3),
        "maximum_drawdown": round(maximum_drawdown, 3),
        "average_adverse_excursion": round(
            sum(float(item.get("mae_percent", 0)) for item in outcomes)
            / len(outcomes), 3) if outcomes else 0,
        "average_favorable_excursion": round(
            sum(float(item.get("mfe_percent", 0)) for item in outcomes)
            / len(outcomes), 3) if outcomes else 0,
    }
