"""Leak-free alignment of completed higher-timeframe data."""

from __future__ import annotations

import pandas as pd


class MultiTimeframeAlignment:
    @staticmethod
    def resample_completed(data: pd.DataFrame, frequency: str,
                           as_of: pd.Timestamp | None = None) -> pd.DataFrame:
        """Aggregate OHLCV and discard the still-forming final bucket.

        Output labels are the interval's right edge, which is when the higher
        candle first becomes legally visible to a lower-timeframe signal.
        """
        if not isinstance(data.index, pd.DatetimeIndex) or not data.index.is_monotonic_increasing:
            raise ValueError("resampling requires a sorted DatetimeIndex")
        rules = {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
        if "Volume" in data:
            rules["Volume"] = "sum"
        result = data.resample(frequency, label="right", closed="right").agg(rules).dropna(
            subset=["Open", "High", "Low", "Close"])
        cutoff = as_of if as_of is not None else data.index[-1]
        return result[result.index <= cutoff]

    @staticmethod
    def align(lower: pd.DataFrame, higher: pd.DataFrame,
              higher_rule: str = "completed_only") -> pd.DataFrame:
        """Backward-asof join; a higher candle becomes visible at its close.

        Both indexes must be monotonic ``DatetimeIndex`` values.  Callers that
        label candles by opening time must pass higher data whose index has
        already been shifted to the candle close.
        """
        if not isinstance(lower.index, pd.DatetimeIndex) or not isinstance(higher.index, pd.DatetimeIndex):
            raise ValueError("multi-timeframe alignment requires DatetimeIndex")
        if not lower.index.is_monotonic_increasing or not higher.index.is_monotonic_increasing:
            raise ValueError("time indexes must be sorted")
        left = lower.reset_index(names="_timestamp")
        right = higher.add_prefix("HTF_").reset_index(names="_timestamp")
        return pd.merge_asof(left, right, on="_timestamp", direction="backward",
                             allow_exact_matches=True).set_index("_timestamp")
