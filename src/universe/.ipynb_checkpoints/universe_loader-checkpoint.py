"""
Universe Loader

Loads Zerodha instrument master.
"""

from pathlib import Path

import pandas as pd


class UniverseLoader:

    def __init__(self):

        project_root = Path(__file__).resolve().parents[2]

        self.instrument_file = (

            project_root
            / "data"
            / "instruments.csv"

        )

    def load(self):

        return pd.read_csv(self.instrument_file)