"""Shortlist diversification and correlation controls."""

from __future__ import annotations

from collections import Counter
from typing import Any

import pandas as pd


def apply_soft_sector_cap(
    candidates: list[dict[str, Any]], maximum: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep overflow as reserve rather than discarding strong candidates."""
    counts: Counter[str] = Counter()
    selected, reserve = [], []
    for candidate in candidates:
        sector = str(candidate.get("sector") or "UNKNOWN")
        if sector == "UNKNOWN" or counts[sector] < maximum:
            selected.append(candidate)
            counts[sector] += 1
        else:
            candidate["portfolio_reason_codes"] = [
                *candidate.get("portfolio_reason_codes", []), "SECTOR_CAP_REACHED"]
            reserve.append(candidate)
    return selected, reserve


def correlation_matrix(
    histories: dict[str, pd.DataFrame], lookback: int,
) -> pd.DataFrame:
    returns = {
        symbol: pd.to_numeric(data["Close"], errors="coerce").pct_change().tail(lookback)
        for symbol, data in histories.items() if data is not None and not data.empty
    }
    return pd.DataFrame(returns).corr(min_periods=max(10, lookback // 2))


def reduce_correlated_exposure(
    candidates: list[dict[str, Any]], histories: dict[str, pd.DataFrame],
    maximum_correlation: float, lookback: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Retain the earlier/higher-ranked candidate from highly correlated pairs."""
    matrix = correlation_matrix(histories, lookback)
    selected, reserve, conflicts = [], [], []
    for candidate in candidates:
        symbol = candidate["symbol"]
        conflict = next((
            kept for kept in selected
            if symbol in matrix.index and kept["symbol"] in matrix.columns
            and float(matrix.loc[symbol, kept["symbol"]]) >= maximum_correlation
        ), None)
        if conflict is None:
            selected.append(candidate)
            continue
        candidate["portfolio_reason_codes"] = [
            *candidate.get("portfolio_reason_codes", []),
            "HIGH_CORRELATION_WITH_EXISTING_POSITION",
            "REPLACED_BY_HIGHER_RANKED_DIVERSIFIED_CANDIDATE",
        ]
        reserve.append(candidate)
        conflicts.append({
            "retained": conflict["symbol"], "reserved": symbol,
            "correlation": round(float(matrix.loc[symbol, conflict["symbol"]]), 3),
        })
    return selected, reserve, conflicts
