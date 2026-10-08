"""Evidence-based upcoming catalysts, separate from execution approval."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def timestamp(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp if stamp.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def ist_time(value):
    stamp = timestamp(value)
    return stamp.astimezone(IST).strftime("%d %b %Y, %I:%M %p IST") if stamp else "Not verified"


def assess_catalyst(item, now=None):
    now = now or datetime.now(timezone.utc)
    risk = item.get("event_risk") or {}
    upcoming = []
    for event in risk.get("matched_events", []):
        start = timestamp(event.get("event_start"))
        if (start and now < start <= now + timedelta(days=7)
                and event.get("is_scheduled") and event.get("is_confirmed")
                and event.get("source_urls_or_ids")
                and event.get("status") in {"SCHEDULED", "ACTIVE", "ESCALATING"}
                and event.get("freshness_score", 0) > 0):
            upcoming.append({"title": event.get("title"), "time": start.isoformat(),
                             "time_ist": ist_time(start.isoformat()),
                             "category": event.get("category"),
                             "expected_direction": event.get("direction", "UNCERTAIN"),
                             "sources": event["source_urls_or_ids"],
                             "affected_symbols": event.get("affected_symbols", []),
                             "affected_sectors": event.get("affected_sectors", [])})
    upcoming.sort(key=lambda event: event["time"])
    timing = item.get("setup_entry_timing") or item.get("entry_selection") or {}
    status = timing.get("status")
    if status == "TOO LATE":
        state = "ALREADY_EXTENDED"
    elif upcoming and status in {"WAIT FOR BREAKOUT", "WAIT FOR PULLBACK", "BUY NOW"}:
        state = "UPCOMING_CATALYST_WATCHLIST"
    elif upcoming:
        state = "UPCOMING_CATALYST_SETUP_UNVERIFIED"
    else:
        state = "NO_VERIFIED_UPCOMING_CATALYST"
    return {"status": state, "upcoming_events": upcoming,
            "entry_timing": timing,
            "session_price_change_percent": (item.get("today_news_alignment") or {}).get("price_change_percent"),
            "reaction_status": "UNKNOWN",
            "reaction_reason": "No verified price comparison immediately before and after the news; session change does not establish a news reaction.",
            "trade_approved": bool((item.get("trade_eligibility") or {}).get("eligible")
                                   and (item.get("futures_selection") or {}).get("eligible")
                                   and item.get("final_action") == "BUY"),
            "event_risk_level": risk.get("event_risk_level", "UNKNOWN"),
            "event_coverage": risk.get("event_data_availability_state", "UNAVAILABLE"),
            "event_hard_block": risk.get("hard_block"),
            "note": "An upcoming catalyst identifies a possible move, not its direction or a guaranteed reaction. Watchlist inclusion does not override event-risk or execution gates."}
