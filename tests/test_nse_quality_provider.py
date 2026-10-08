import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import requests

from src.quality.config import QualityConfig
from src.quality.nse_provider import NseFundamentalProvider


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class Session:
    def __init__(self):
        self.headers = {}
        self.calls = []

    def get(self, url, *, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        if params and params.get("section") == "trade_info":
            return Response({"securityWiseDP": {
                "deliveryToTradedQuantity": "42.5",
            }})
        if params:
            return Response({"metadata": {
                "pdSymbolPe": "25.5", "pdSectorPe": "24.0",
                "lastUpdateTime": "08-Oct-2026 15:30:00",
            }})
        return Response({})


class UnavailableSession(Session):
    def get(self, url, *, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        raise requests.ConnectionError("NSE unavailable")


def test_nse_provider_fetches_and_caches_quote_and_delivery():
    session = Session()
    provider = NseFundamentalProvider(session=session)

    snapshot = provider.get_fundamentals("RELIANCE")
    cached = provider.get_fundamentals("RELIANCE")

    assert snapshot == cached
    assert snapshot.pe_ratio == 25.5
    assert snapshot.sector_pe == 24.0
    assert snapshot.delivery_percent == 42.5
    assert snapshot.source == "NSE"
    assert len(session.calls) == 3


def test_nse_quality_thresholds_are_configurable():
    config = QualityConfig()
    assert config.maximum_sector_pe_deviation == 0.05
    assert config.minimum_roe_percent == 15
    assert config.minimum_roce_percent == 15
    assert abs(sum(config.stock_quality_weights.values()) - 1) < 1e-8


def test_nse_outage_is_cached_across_symbols():
    session = UnavailableSession()
    provider = NseFundamentalProvider(session=session)

    assert provider.get_fundamentals("AAA") is None
    assert provider.get_fundamentals("BBB") is None
    assert len(session.calls) == 1