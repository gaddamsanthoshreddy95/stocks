"""Live market, sector, and relative-strength enrichment with safe fallbacks."""

from __future__ import annotations

from copy import deepcopy
from threading import Lock
from time import monotonic
from collections import Counter

from src.market.market_regime import MarketRegime
from src.market.relative_strength import RelativeStrength
from src.market_data.market_data_hub import MarketDataHub
from src.sector.sector_strength import SectorStrength


class ContextEnrichment:
    """Collect context once per report and never fail the core trade scan."""

    _cache_lock = Lock()
    _market_cache = None
    _relative_strength_cache = {}
    market_cache_ttl_seconds = 5 * 60
    relative_strength_cache_ttl_seconds = 15 * 60

    def __init__(self, live: bool):
        self.live = live

    def market_and_sectors(self, force_refresh: bool = False):
        if not self.live:
            return ({"available": False, "regime": "UNAVAILABLE", "score": 0, "confidence": 0, "indices": {}}, {})
        with self._cache_lock:
            cached = self._market_cache
            if not force_refresh and cached and monotonic() - cached[0] < self.market_cache_ttl_seconds:
                return deepcopy(cached[1])
        try:
            snapshot = MarketDataHub().get_market_snapshot()
            regime = MarketRegime.classify(snapshot)
            indices = snapshot.get("india", {})
            market_result = {"available": True, "regime": regime.status, "score": regime.score,
                     "confidence": regime.confidence, "reasons": regime.reasons, "indices": indices,
                     "global": snapshot.get("global", {}),
                     "commodities": snapshot.get("commodities", {}),
                     "forex": snapshot.get("forex", {}),
                     "vix": snapshot.get("volatility")}
            try:
                sectors = SectorStrength().analyze()
            except Exception as exc:
                sectors = {}
                market_result["sector_data_status"] = "FAILED"
                market_result["sector_data_reason"] = exc.__class__.__name__
            result = (market_result, sectors)
        except Exception as exc:  # Provider failure must not stop recommendations.
            result = ({"available": False, "regime": "UNAVAILABLE", "score": 0, "confidence": 0,
                     "indices": {}, "reason": exc.__class__.__name__}, {})
        with self._cache_lock:
            type(self)._market_cache = (monotonic(), deepcopy(result))
        return result

    def relative_strength(self, symbol: str):
        if not self.live:
            return {"available": False, "status": "UNAVAILABLE", "rating": "UNAVAILABLE", "score": None}
        key = symbol.strip().upper().removesuffix(".NS")
        with self._cache_lock:
            cached = self._relative_strength_cache.get(key)
            if cached and monotonic() - cached[0] < self.relative_strength_cache_ttl_seconds:
                return deepcopy(cached[1])
        try:
            result = RelativeStrength.analyze(symbol)
            if not result or result.get("status") != "AVAILABLE":
                value = {"available": False, "status": (result or {}).get("status", "UNAVAILABLE"),
                         "rating": "UNAVAILABLE", "score": None,
                         "reason": (result or {}).get("reason", "Relative-strength calculation unavailable.")}
            else:
                value = {"available": True, **result}
        except Exception as exc:
            value = {"available": False, "status": "FAILED", "rating": "UNAVAILABLE",
                     "score": None, "reason": exc.__class__.__name__}
        with self._cache_lock:
            type(self)._relative_strength_cache[key] = (monotonic(), deepcopy(value))
        return value

    @staticmethod
    def finalize_relative_strength(results: list[dict], duplicate_ratio_limit: float = .75) -> dict:
        """Assign cross-sectional percentiles and fail safely on implausible duplicates."""
        available = [row for row in results
                     if row.get("status") == "AVAILABLE" and row.get("relative_strength") is not None]
        distribution = {"requested": len(results), "available": len(available),
                        "unavailable": len(results) - len(available), "warning": None}
        if not available:
            return distribution
        values = [round(float(row["relative_strength"]), 6) for row in available]
        value, count = Counter(values).most_common(1)[0]
        duplicate_ratio = count / len(values)
        distribution.update({"most_common_value": value, "most_common_count": count,
                             "most_common_ratio": round(duplicate_ratio, 3),
                             "raw_min": min(values), "raw_max": max(values)})
        if len(values) >= 5 and duplicate_ratio >= duplicate_ratio_limit:
            reason = (f"Relative-strength distribution failed sanity check: {count}/{len(values)} "
                      f"candidates share {value}.")
            for row in available:
                row.update({"available": False, "status": "FAILED", "score": None,
                            "rating": "UNAVAILABLE", "reason": reason})
            distribution.update({"available": 0, "unavailable": len(results), "warning": reason})
            return distribution
        ordered = sorted(set(values))
        for row in available:
            raw = round(float(row["relative_strength"]), 6)
            percentile = 50.0 if len(ordered) == 1 else 100 * ordered.index(raw) / (len(ordered) - 1)
            row["score"] = round(percentile, 2)
            row["score_model"] = "CROSS_SECTIONAL_PERCENTILE"
        distribution["score_distribution"] = [row["score"] for row in available]
        return distribution
