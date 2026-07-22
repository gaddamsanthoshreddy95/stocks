"""
Universe Filter

Creates the F&O stock universe.
"""

import pandas as pd


class UniverseFilter:

    @staticmethod
    def fo_stocks(df: pd.DataFrame):

        df = df.copy()

        # Only NFO Equity Futures

        df = df[

            (df["exchange"] == "NFO")

            &

            (df["instrument_type"] == "FUT")

        ]

        # Remove duplicate symbols

        df = df.drop_duplicates("name")

        return sorted(df["name"].tolist())