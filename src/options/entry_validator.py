"""Validate an option entry using OI resistance and option Greeks."""

from __future__ import annotations


class OptionEntryValidator:
    """Turn option-chain context into explicit go/no-go conditions."""

    @staticmethod
    def validate(chain, trade: dict, direction: str, settings=None) -> dict:
        if settings is None:
            from src.application.settings import PlatformSettings
            settings = PlatformSettings()
        reasons, warnings = [], []
        approved = True
        calls = chain.calls
        call_resistance = max(calls, key=lambda item: item.open_interest) if calls else None
        spot = chain.spot_price
        if direction == "BULLISH" and call_resistance:
            distance = (call_resistance.strike - spot) / spot * 100
            if 0 <= distance <= settings.option_call_resistance_near_percent:
                approved = False
                reasons.append(f"spot is {distance:.2f}% below high Call-OI resistance {call_resistance.strike}")
            elif spot > call_resistance.strike and call_resistance.change_in_oi < 0:
                reasons.append(f"Call-OI resistance {call_resistance.strike} is unwinding after breakout")
            elif spot > call_resistance.strike:
                warnings.append(f"breakout above Call-OI resistance {call_resistance.strike} lacks OI unwinding confirmation")

        if not trade.get("available") or not trade.get("legs"):
            approved = False
            reasons.append((trade.get("rejection") or {}).get(
                "reason", trade.get("reason", "No executable option legs are available.")))

        long_leg = next((leg for leg in trade.get("legs", []) if leg.get("side") == "BUY"), None)
        credit_structure = str(trade.get("structure_type", "")).startswith("DEFINED_RISK_CREDIT")
        if long_leg and not credit_structure:
            delta = long_leg.get("delta")
            theta = long_leg.get("theta")
            premium = long_leg.get("premium", 0)
            iv = long_leg.get("implied_volatility", 0)
            if delta is not None and not settings.option_long_delta_min <= abs(delta) <= settings.option_long_delta_max:
                approved = False
                reasons.append(f"long-leg delta {delta:.2f} is outside the 0.35-0.70 target range")
            if theta is not None and premium and abs(theta) / premium > settings.option_max_theta_premium_ratio:
                approved = False
                reasons.append("theta decay exceeds 8% of premium per day")
            if iv and iv >= settings.option_high_iv_warning:
                warnings.append(f"high implied volatility ({iv:.1f}%) increases premium/vega risk")
            if long_leg.get("price_change") is not None and long_leg["price_change"] < 0:
                warnings.append("long option premium is falling")
        return {"approved": approved, "reasons": reasons, "warnings": warnings,
                "call_oi_resistance": call_resistance.strike if call_resistance else None,
                "call_oi_change": call_resistance.change_in_oi if call_resistance else None}
