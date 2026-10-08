"""Mandatory futures research criteria, independent of technical ranking mode."""

from datetime import date, datetime, time
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from src.quality.models import QualityScore


REQUIRED_CHECKS = (
    "valuation_quality", "delivery_quality", "debt_free_quality",
    "roe_quality", "roce_quality", "institutional_holding_quality",
    "promoter_holding_quality", "quarterly_results_quality", "commentary_quality",
    "block_deal_quality", "recent_news_quality", "sector_one_year_quality",
    "sector_leadership_quality", "vwap_quality",
)


def session_vwap_quality(data: pd.DataFrame | None, current_price: float | None,
                         *, now: datetime | None = None) -> QualityScore:
    """Compare price with today's volume-weighted HLC3 from five-minute bars."""
    unknown = lambda reason: QualityScore(None, "UNKNOWN", 0, reason_codes=[reason])
    if data is None or data.empty or not {"High", "Low", "Close", "Volume"}.issubset(data):
        return unknown("SESSION_VWAP_DATA_MISSING")
    india = ZoneInfo("Asia/Kolkata")
    current = now or datetime.now(india)
    current = current.replace(tzinfo=india) if current.tzinfo is None else current.astimezone(india)
    try:
        frame = data.copy()
        index = pd.DatetimeIndex(frame.index)
        index = index.tz_localize(india) if index.tz is None else index.tz_convert(india)
        frame.index = index
        start = datetime.combine(current.date(), time(9, 15), india)
        end = min(current, datetime.combine(current.date(), time(15, 30), india))
        frame = frame.loc[(index >= start) & (index <= end)].sort_index()
        if frame.empty:
            return unknown("CURRENT_SESSION_VWAP_MISSING")
        if frame.index.has_duplicates or frame.index[0] != start:
            return unknown("SESSION_VWAP_OPENING_BARS_MISSING_OR_DUPLICATED")
        if (frame.index.to_series().diff().dropna() > pd.Timedelta(minutes=5)).any():
            return unknown("SESSION_VWAP_BARS_INCOMPLETE")
        if (end - frame.index[-1]).total_seconds() > 900:
            return unknown("SESSION_VWAP_DATA_STALE")
        values = frame[["High", "Low", "Close", "Volume"]].apply(pd.to_numeric, errors="coerce")
        if (not values.map(isfinite).all().all() or (values["Volume"] < 0).any()
                or (values[["High", "Low", "Close"]] <= 0).any().any()
                or (values["High"] < values["Low"]).any()):
            return unknown("SESSION_VWAP_DATA_INVALID")
        volume = float(values["Volume"].sum())
        if volume <= 0:
            return unknown("SESSION_VWAP_ZERO_VOLUME")
        price = float(current_price)
        if not isfinite(price) or price <= 0:
            return unknown("SESSION_VWAP_PRICE_INVALID")
        typical = values[["High", "Low", "Close"]].mean(axis=1)
        vwap = float((typical * values["Volume"]).sum() / volume)
    except (ValueError, TypeError, OverflowError):
        return unknown("SESSION_VWAP_DATA_INVALID")
    passed = price >= vwap
    return QualityScore(
        100 if passed else 0, "PASS" if passed else "FAIL", 100,
        {"current_price": price, "vwap": round(vwap, 4), "session_volume": volume,
         "distance_from_vwap_percent": round((price / vwap - 1) * 100, 3)},
        ["PRICE_AT_OR_ABOVE_SESSION_VWAP" if passed else "PRICE_BELOW_SESSION_VWAP"],
        warnings=["VWAP uses five-minute underlying-stock bars and HLC3; it is an estimate of trade-level VWAP."],
    )


def active_contracts(symbol: str, instruments: list[dict], today: date) -> list[dict]:
    contracts = []
    for item in instruments:
        if (item.get("instrument_type") != "FUT"
                or item.get("segment") != "NFO-FUT"
                or str(item.get("name", "")).upper() != symbol.upper()):
            continue
        try:
            expiry = date.fromisoformat(str(item.get("expiry"))[:10])
            lot_size = int(item.get("lot_size", 0))
        except (ValueError, TypeError):
            continue
        if expiry >= today and lot_size > 0:
            contracts.append({"tradingsymbol": item.get("tradingsymbol"),
                              "expiry": expiry.isoformat(), "lot_size": lot_size})
    return sorted(contracts, key=lambda item: item["expiry"])


def assess_futures_selection(scores: dict[str, Any], contracts: list[dict] | None) -> dict:
    checks = {}
    for name in REQUIRED_CHECKS:
        score = scores.get(name)
        details = score.to_dict() if hasattr(score, "to_dict") else dict(score or {})
        checks[name] = {**details, "status": details.get("status", "UNKNOWN"),
                       "passed": details.get("status") == "PASS"}
    checks["listed_futures_contract"] = {
        "status": "PASS" if contracts else "UNKNOWN" if contracts is None else "FAIL",
        "passed": bool(contracts), "contracts": contracts or [],
    }
    failed = [name for name, check in checks.items() if check["status"] == "FAIL"]
    missing = [name for name, check in checks.items()
               if check["status"] not in {"PASS", "FAIL"}]
    return {"eligible": not failed and not missing,
            "status": "REJECTED" if failed else "UNVERIFIED" if missing else "QUALIFIED",
            "failed_checks": failed, "unavailable_checks": missing, "checks": checks,
            "sector_leadership_basis": "Stock one-year return exceeds sector one-year return; a proxy for leadership, not index contribution.",
            "valuation_basis": "Positive stock PE within sector PE ±5% by default; relative valuation, not proof of intrinsic value.",
            "block_deal_basis": "No observed material impact; future price impact cannot be guaranteed."}


def block_unqualified_trade(trade: dict, assessment: dict) -> None:
    """Keep all execution fields consistent when a research requirement fails."""
    trade["futures_selection"] = assessment
    if assessment["eligible"]:
        return
    reason = "Futures selection criteria not met: " + ", ".join(
        assessment["failed_checks"] + assessment["unavailable_checks"])
    block_trade(trade, reason, "FUTURES_SELECTION_CRITERIA_NOT_MET")


def block_trade(trade: dict, reason: str, rejection_code: str) -> None:
    """Block an entry consistently without losing the other research evidence."""
    trade["status"] = "REJECTED"
    trade["final_action"] = trade["action"] = trade["recommendation"] = "REJECT"
    trade["selection_status"] = "AVOID"
    trade["selection_reason"] = reason
    trade["status_reasons"] = [*trade.get("status_reasons", []), reason]
    trade["entry_selection"] = {**trade.get("entry_selection", {}),
                                "status": "AVOID", "reason": reason}
    trade["final_decision"] = {**trade.get("final_decision", {}),
                               "action": "REJECT", "executable": False,
                               "reasons": [reason], "rejection_reasons": [reason]}
    trade["trade_eligibility"] = {
        **trade.get("trade_eligibility", {}), "eligible": False, "status": "REJECTED",
        "blocking_reasons": [*trade.get("trade_eligibility", {}).get("blocking_reasons", []), reason],
    }
    trade["risk"] = {**trade.get("risk", {}), "quantity": 0, "capital_used": 0,
                     "risk_amount": 0, "actual_risk": 0}
    trade["option_execution_valid"] = False
    trade["option_trade_approval"] = {
        **trade.get("option_trade_approval", {}), "status": "REJECTED", "approved": False,
        "rejection_codes": [*trade.get("option_trade_approval", {}).get("rejection_codes", []),
                            rejection_code],
    }
    trade["option_context"] = {**trade.get("option_context", {}), "execution": "REJECTED"}
