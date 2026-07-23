"""Shared, NaN-safe OHLCV calculations used by price-action engines."""

from __future__ import annotations

import math
from typing import Any

import pandas as pd


def normalise_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Return a numeric, title-cased OHLCV copy without mutating the caller."""
    aliases = {str(c).lower(): c for c in df.columns}
    required = ("open", "high", "low", "close")
    if any(name not in aliases for name in required):
        raise ValueError("OHLC data requires Open, High, Low and Close columns")
    out = df.rename(columns={aliases[n]: n.title() for n in required}).copy()
    if "volume" in aliases:
        out = out.rename(columns={aliases["volume"]: "Volume"})
    for column in ("Open", "High", "Low", "Close", "Volume"):
        if column in out:
            out[column] = pd.to_numeric(out[column], errors="coerce")
    return out


def true_range(df: pd.DataFrame) -> pd.Series:
    previous = df["Close"].shift(1)
    return pd.concat(((df["High"] - df["Low"]).abs(),
                      (df["High"] - previous).abs(),
                      (df["Low"] - previous).abs()), axis=1).max(axis=1)


def atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder-style ATR using only current and earlier rows."""
    return true_range(df).ewm(alpha=1 / max(period, 1), adjust=False,
                              min_periods=1).mean()


def candle(row: pd.Series, atr: float | None = None) -> dict[str, Any]:
    values = [float(row.get(k, math.nan)) for k in ("Open", "High", "Low", "Close")]
    if not all(math.isfinite(v) for v in values):
        return {"valid": False}
    open_, high, low, close = values
    span = max(high - low, 0.0)
    body = abs(close - open_)
    safe_span = max(span, 1e-12)
    return {
        "valid": high >= max(open_, close) and low <= min(open_, close),
        "open": open_, "high": high, "low": low, "close": close,
        "body": body, "range": span, "body_ratio": body / safe_span,
        "upper_wick": max(0.0, high - max(open_, close)),
        "lower_wick": max(0.0, min(open_, close) - low),
        "bullish": close > open_, "bearish": close < open_,
        "atr": max(float(atr or span), 1e-12),
    }


def relative_volume(df: pd.DataFrame, index: int, lookback: int = 20) -> float:
    if "Volume" not in df or index < 0 or not math.isfinite(float(df.iloc[index].get("Volume", math.nan))):
        return 1.0
    prior = df["Volume"].iloc[max(0, index - lookback):index].dropna()
    mean = float(prior.mean()) if not prior.empty else float(df.iloc[index]["Volume"])
    return float(df.iloc[index]["Volume"]) / max(mean, 1e-12)


def prior_trend(df: pd.DataFrame, end: int, lookback: int = 8,
                min_change_ratio: float = .005) -> str:
    """Classify trend strictly before *end*; no future observations are read."""
    closes = df["Close"].iloc[max(0, end - lookback):end].dropna()
    if len(closes) < 4:
        return "RANGE"
    x = pd.Series(range(len(closes)), dtype=float)
    slope = float(x.cov(closes.reset_index(drop=True)) / max(x.var(), 1e-12))
    change = float(closes.iloc[-1] - closes.iloc[0])
    threshold = max(abs(float(closes.iloc[0])) * min_change_ratio, 1e-12)
    if slope > 0 and change > threshold:
        return "UPTREND"
    if slope < 0 and change < -threshold:
        return "DOWNTREND"
    return "RANGE"
