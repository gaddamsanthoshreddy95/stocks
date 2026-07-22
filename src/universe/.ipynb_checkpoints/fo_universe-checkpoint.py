"""
F&O Universe
"""

from src.universe.universe_loader import UniverseLoader
from src.universe.universe_filter import UniverseFilter


class FOUniverse:

    def __init__(self):

        self.loader = UniverseLoader()

    def get_symbols(self):

        df = self.loader.load()

        return UniverseFilter.fo_stocks(df)