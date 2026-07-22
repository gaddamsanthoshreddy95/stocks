"""Independent validation of executable option contract structures."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class OptionStructureValidator:
    @staticmethod
    def _age_seconds(value: str | None) -> float | None:
        if not value:
            return None
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            return max(0, (datetime.now(timezone.utc) - timestamp).total_seconds())
        except (TypeError, ValueError):
            return None

    @classmethod
    def validate(cls, option: dict[str, Any], settings) -> dict[str, Any]:
        trade = option.get("trade") or {}
        legs = trade.get("legs") or []
        failures: list[str] = []
        checks: dict[str, bool] = {}
        checks["available"] = bool(option.get("available") and trade.get("available"))
        checks["expiry"] = bool(trade.get("expiry") or option.get("expiry"))
        checks["legs"] = bool(legs)
        structure_type = str(trade.get("structure_type") or "")
        if structure_type.startswith("DEFINED_RISK_CREDIT"):
            buys = [leg for leg in legs if leg.get("side") == "BUY"]
            sells = [leg for leg in legs if leg.get("side") == "SELL"]
            strategy = str(trade.get("strategy") or "").upper().replace("_", " ")
            if strategy == "BULL PUT SPREAD":
                topology = (len(buys) == len(sells) == 1
                            and buys[0].get("option_type") == sells[0].get("option_type") == "PE"
                            and float(buys[0].get("strike", 0)) < float(sells[0].get("strike", 0)))
            elif strategy == "BEAR CALL SPREAD":
                topology = (len(buys) == len(sells) == 1
                            and buys[0].get("option_type") == sells[0].get("option_type") == "CE"
                            and float(buys[0].get("strike", 0)) > float(sells[0].get("strike", 0)))
            elif strategy == "IRON CONDOR":
                topology = len(buys) == len(sells) == 2
            else:
                topology = False
            checks["defined_risk_topology"] = topology
            checks["positive_credit"] = float(trade.get("net_credit") or 0) > 0
            checks["bounded_maximum_loss"] = float(trade.get("maximum_loss") or 0) > 0
        for index, leg in enumerate(legs):
            prefix = f"leg_{index}"
            bid, ask = float(leg.get("bid") or 0), float(leg.get("ask") or 0)
            midpoint = (bid + ask) / 2 if bid > 0 and ask > 0 else 0
            spread = (ask - bid) / midpoint * 100 if midpoint else float("inf")
            age = cls._age_seconds(leg.get("quote_timestamp"))
            checks[f"{prefix}_quotes"] = bid > 0 and ask >= bid and float(leg.get("last_price") or 0) > 0
            checks[f"{prefix}_spread"] = spread <= settings.option_max_bid_ask_spread_percent
            checks[f"{prefix}_open_interest"] = int(leg.get("open_interest") or 0) >= settings.option_min_open_interest
            checks[f"{prefix}_volume"] = int(leg.get("volume") or 0) >= settings.option_min_volume
            iv = float(leg.get("implied_volatility") or 0)
            checks[f"{prefix}_iv"] = 0 < iv <= settings.option_max_implied_volatility
            checks[f"{prefix}_strike"] = float(leg.get("strike") or 0) > 0
            checks[f"{prefix}_premium"] = float(leg.get("premium") or 0) > 0
            checks[f"{prefix}_lot_size"] = int(leg.get("lot_size") or 0) > 0 and int(leg.get("quantity") or 0) > 0
            checks[f"{prefix}_fresh"] = (not leg.get("quote_is_stale", False)
                                           and (age is None or age <= settings.option_max_quote_age_seconds))
        code_by_check = {
            "available": "OPTION_DATA_UNAVAILABLE", "expiry": "INVALID_EXPIRY", "legs": "NO_OPTION_LEGS",
            "quotes": "INVALID_QUOTES", "spread": "BID_ASK_SPREAD_TOO_WIDE",
            "open_interest": "OPEN_INTEREST_TOO_LOW", "volume": "VOLUME_TOO_LOW",
            "iv": "IV_UNAVAILABLE_OR_INVALID", "strike": "INVALID_STRIKE",
            "premium": "INVALID_PREMIUM", "lot_size": "INVALID_LOT_SIZE", "fresh": "STALE_QUOTES",
            "defined_risk_topology": "INVALID_CREDIT_SPREAD_HEDGE",
            "positive_credit": "NON_POSITIVE_CREDIT", "bounded_maximum_loss": "UNBOUNDED_OR_INVALID_LOSS",
        }
        for name, passed in checks.items():
            if not passed:
                suffix = next((key for key in code_by_check if name.endswith("_" + key)), name)
                failures.append(code_by_check.get(suffix, "STRUCTURE_VALIDATION_FAILED"))
        return {"result_type": "OptionStructureResult",
                "valid": not failures, "status": "VALID" if not failures else "INVALID",
                "quotes_fresh": "STALE_QUOTES" not in failures, "checks": checks,
                "rejection_codes": list(dict.fromkeys(failures))}
