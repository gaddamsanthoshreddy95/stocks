from dataclasses import replace
from datetime import date, datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from src.application.platform import TradingPlatform
from src.application.settings import PlatformSettings
from src.quality.config import QualityConfig
from src.quality.engine import CandidateQualityEngine
from src.quality.futures_selection import (
    REQUIRED_CHECKS, active_contracts, assess_futures_selection, block_unqualified_trade,
    session_vwap_quality,
)
from src.quality.models import FundamentalSnapshot
from src.workflow.final_decision import FinalConsistencyValidator


def snapshot():
    return FundamentalSnapshot(
        "TCS", roe=20, roce=25, debt_to_equity=0, promoter_pledge=0,
        pe_ratio=25, sector_pe=25, delivery_percent=50, monthly_delivery_percent=40,
        fii_holding_percent=10, dii_holding_percent=8, promoter_holding_percent=45,
        fii_holding_change_pct_points=0, dii_holding_change_pct_points=1,
        promoter_holding_change_pct_points=0,
        quarterly_revenue_growth_pct=(4, 5, 6), quarterly_profit_growth_pct=(3, 4, 5),
        commentary_strength="VERY_STRONG", block_deal_price_impact=False,
    )


def provider(value):
    return SimpleNamespace(get_fundamentals=lambda symbol: value)


def contract():
    return {"name": "TCS", "tradingsymbol": "TCS99DECFUT", "expiry": "2099-12-31",
            "instrument_type": "FUT", "segment": "NFO-FUT", "lot_size": 100}


@pytest.mark.parametrize("name", REQUIRED_CHECKS)
def test_each_missing_or_failed_requirement_prevents_qualification(name):
    scores = {key: {"status": "PASS"} for key in REQUIRED_CHECKS}
    assert assess_futures_selection(scores, [contract()])["eligible"]
    for status in ("UNKNOWN", "PARTIAL", "FAIL"):
        result = assess_futures_selection({**scores, name: {"status": status}}, [contract()])
        assert not result["eligible"]
        assert name in result["failed_checks"] + result["unavailable_checks"]


def test_expired_contract_and_other_derivatives_do_not_qualify():
    fixtures = [contract(), {**contract(), "expiry": "2025-01-01"},
                {**contract(), "segment": "BFO-FUT"},
                {**contract(), "instrument_type": "CE"},
                {**contract(), "lot_size": 0}]
    assert len(active_contracts("TCS", fixtures, date(2026, 10, 8))) == 1
    scores = {key: {"status": "PASS"} for key in REQUIRED_CHECKS}
    assert not assess_futures_selection(scores, [])["eligible"]
    assert not assess_futures_selection(scores, None)["eligible"]


@pytest.mark.parametrize("field,value,gate", [
    ("fii_holding_percent", 1, "institutional_holding_quality"),
    ("dii_holding_percent", 1, "institutional_holding_quality"),
    ("promoter_holding_percent", 10, "promoter_holding_quality"),
    ("quarterly_profit_growth_pct", (3, -1, 5), "quarterly_results_quality"),
    ("block_deal_price_impact", True, "block_deal_quality"),
])
def test_weak_company_evidence_fails(field, value, gate):
    engine = CandidateQualityEngine(fundamental_provider=provider(replace(snapshot(), **{field: value})))
    checks = engine.stock_selection_quality(
        "TCS", sector_one_year_return=10, stock_one_year_return=20,
        news={"news_state": "NO_RELEVANT_NEWS"})
    assert checks[gate].status == "FAIL"


def test_rejected_selection_clears_execution_and_position():
    trade = {"final_action": "BUY", "action": "BUY", "recommendation": "BUY",
             "risk": {"quantity": 100}, "trade_eligibility": {"eligible": True}}
    block_unqualified_trade(trade, assess_futures_selection({}, None))
    FinalConsistencyValidator.validate(trade)
    assert not trade["trade_eligibility"]["eligible"]
    assert not trade["option_execution_valid"]
    assert trade["risk"]["quantity"] == 0


def test_futures_scan_uses_verified_data_and_rejects_missing_monthly_delivery(monkeypatch):
    monkeypatch.setattr("src.quality.futures_selection.session_vwap_quality",
                        lambda data, price: session_vwap_quality(
                            data, price, now=datetime(2026, 10, 8, 9, 25)))
    platform = TradingPlatform.__new__(TradingPlatform)
    platform.settings = PlatformSettings()
    candidate = {"symbol": "TCS", "current_price": 100, "technical_score": 80}
    platform.suggest_stocks = lambda *args, **kwargs: {
        "suggestions": [candidate], "market_data_source": "kite", "universe_size": 219}
    stock = pd.DataFrame({"Close": range(100, 400)})
    sector = pd.DataFrame({"Close": range(300, 600)})
    platform.provider = SimpleNamespace(
        get_nfo_instruments=lambda: [contract()],
        get_data=lambda symbol: stock if symbol == "TCS" else sector,
        get_session_intraday=lambda symbol: session_bars(price=100),
    )
    news = lambda symbol: {"news_state": "NO_RELEVANT_NEWS"}
    qualified = platform.audit_futures_fundamentals(fundamental_provider=provider(snapshot()), news_provider=news)
    assert [item["symbol"] for item in qualified["suggestions"]] == ["TCS"]
    missing = platform.audit_futures_fundamentals(
        fundamental_provider=provider(replace(snapshot(), monthly_delivery_percent=None)),
        news_provider=lambda symbol: pytest.fail("News should be deferred"))
    assert not missing["suggestions"]
    assert "delivery_quality" in missing["reviewed"][0]["futures_selection"]["unavailable_checks"]


def test_strict_selection_defaults_on_even_in_shadow_mode():
    config = QualityConfig()
    assert config.ranking_mode == "SHADOW"
    assert config.strict_futures_selection


def test_futures_suggestions_preserve_full_pipeline_and_require_entry_approval(monkeypatch):
    platform = TradingPlatform.__new__(TradingPlatform)
    platform.settings = PlatformSettings(quality_config=QualityConfig(strict_futures_selection=False))
    called = []
    selection = assess_futures_selection(
        {name: {"status": "PASS"} for name in REQUIRED_CHECKS}, [contract()])
    ready = {"symbol": "TCS", "final_action": "BUY", "futures_selection": selection,
             "trade_eligibility": {"eligible": True}}
    waiting = {**ready, "symbol": "INFY", "final_action": "WAIT_FOR_CONFIRMATION",
               "trade_eligibility": {"eligible": False}}
    missing = {**ready, "symbol": "OTHER", "futures_selection": assess_futures_selection({}, None)}

    def daily_report(runner, limit, minimum_score):
        assert runner.settings.quality_config.strict_futures_selection
        called.append((limit, minimum_score))
        return {"trades": [ready, waiting, missing], "futures_review": [ready, waiting, missing],
                "summary": {"stocks_scanned": 219}, "filter_stages": [{"stage": "news"}],
                "sector_ranking": [{"sector": "IT"}]}

    monkeypatch.setattr(TradingPlatform, "daily_report", daily_report)
    result = platform.suggest_futures(10, 55)
    assert called == [(10, 55)]
    assert [item["symbol"] for item in result["suggestions"]] == ["TCS"]
    assert result["filter_stages"] == [{"stage": "news"}]
    assert not platform.settings.quality_config.strict_futures_selection


def session_bars(price=None):
    prices = [100, 200] if price is None else [price, price]
    return pd.DataFrame(
        {"High": prices, "Low": prices, "Close": prices, "Volume": [10, 30]},
        index=pd.to_datetime(["2026-10-08 09:15", "2026-10-08 09:20"]),
    )


def test_session_vwap_uses_volume_weights_and_resets_each_day():
    previous = session_bars(price=1000)
    previous.index -= pd.Timedelta(days=1)
    bars = pd.concat([previous, session_bars()])
    now = datetime(2026, 10, 8, 9, 25)
    result = session_vwap_quality(bars, 176, now=now)
    assert result.status == "PASS"
    assert result.factors["vwap"] == 175
    assert session_vwap_quality(bars, 174, now=now).status == "FAIL"
    assert session_vwap_quality(bars, 175, now=now).status == "PASS"


@pytest.mark.parametrize("kind", ["previous_session", "stale", "zero_volume", "partial", "future", "invalid"])
def test_unavailable_or_invalid_session_vwap_cannot_pass(kind):
    bars = session_bars()
    now = datetime(2026, 10, 8, 9, 25)
    if kind == "previous_session":
        bars.index -= pd.Timedelta(days=1)
    elif kind == "stale":
        now = datetime(2026, 10, 8, 10, 0)
    elif kind == "zero_volume":
        bars["Volume"] = 0
    elif kind == "partial":
        bars.index += pd.Timedelta(minutes=30)
        now = datetime(2026, 10, 8, 10, 0)
    elif kind == "future":
        bars.index += pd.Timedelta(days=1)
    else:
        bars.loc[bars.index[0], "Volume"] = float("nan")
    assert session_vwap_quality(bars, 1000, now=now).status == "UNKNOWN"
