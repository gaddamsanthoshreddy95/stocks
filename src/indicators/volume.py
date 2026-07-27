"""
Volume Indicator

Calculates:
1. Current Volume
2. 20-Day Average Volume
3. Relative Volume (RVOL)
4. Volume Signal
"""

import pandas as pd


class VolumeIndicator:
    """
    Volume Analysis
    """

    @staticmethod
    def calculate(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
        """
        Calculate volume metrics.

        Parameters
        ----------
        df : pd.DataFrame
            Historical OHLCV Data

        period : int
            Rolling average period

        Returns
        -------
        pd.DataFrame
        """

        data = df.copy()

        # ------------------------------------------
        # Flatten MultiIndex columns (yfinance)
        # ------------------------------------------

        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)

        # ------------------------------------------
        # Validation
        # ------------------------------------------

        if "Volume" not in data.columns:
            raise ValueError("Volume column not found.")

        # ------------------------------------------
        # Average Volume
        # ------------------------------------------

        data["AVG_VOLUME"] = (
            data["Volume"]
            .rolling(window=period)
            .mean()
        )

        # ------------------------------------------
        # Relative Volume (RVOL)
        # ------------------------------------------

        data["RVOL"] = (
            data["Volume"]
            / data["AVG_VOLUME"]
        )

        # A live daily candle contains only volume accumulated so far. Comparing
        # it with a complete-day average makes every morning scan look illiquid.
        # Keep AVG_VOLUME as the full-day baseline for turnover/liquidity, but
        # pace RVOL by the elapsed fraction of the NSE session. Historical and
        # completed candles retain the original calculation above.
        if (
            not data.empty
            and bool(data.iloc[-1].get("IS_LIVE_CANDLE", False))
        ):
            progress = pd.to_numeric(
                pd.Series([data.iloc[-1].get("LIVE_SESSION_PROGRESS")]),
                errors="coerce",
            ).iloc[0]
            prior_average = (
                data["Volume"].iloc[:-1].tail(period).mean()
                if len(data) > 1 else float("nan")
            )
            if pd.notna(progress) and progress > 0 and pd.notna(prior_average):
                data.loc[data.index[-1], "AVG_VOLUME"] = prior_average
                expected_volume_so_far = max(float(prior_average) * float(progress), 1)
                data.loc[data.index[-1], "RVOL"] = (
                    float(data.iloc[-1]["Volume"]) / expected_volume_so_far
                )
                data.loc[data.index[-1], "VOLUME_PROGRESS"] = float(progress)
                data.loc[data.index[-1], "PROJECTED_VOLUME"] = (
                    float(data.iloc[-1]["Volume"]) / float(progress)
                )
                data.loc[data.index[-1], "VOLUME_CONFIDENCE"] = min(
                    100.0, max(10.0, float(progress) * 100)
                )

        # ------------------------------------------
        # Volume Signal
        # ------------------------------------------

        signals = []

        for rvol in data["RVOL"]:

            if pd.isna(rvol):
                signals.append("N/A")

            elif rvol >= 2:
                signals.append("VERY HIGH")

            elif rvol >= 1.2:
                signals.append("HIGH")

            elif rvol >= 0.8:
                signals.append("NORMAL")

            else:
                signals.append("LOW")

        data["VOLUME_SIGNAL"] = signals
        data["VOLUME_STATE"] = data["VOLUME_SIGNAL"]
        if (
            not data.empty
            and bool(data.iloc[-1].get("IS_LIVE_CANDLE", False))
            and float(data.iloc[-1].get("VOLUME_PROGRESS", 1.0)) < .08
        ):
            data.loc[data.index[-1], "VOLUME_STATE"] = "PENDING"

        return data
