"""
Decision Engine

This is the brain of the AI Trading Assistant.

Every engine contributes evidence and this class
produces the final trading decision.
"""


class DecisionEngine:

    @staticmethod
    def decide(report: dict):

        analysis = report["analysis"]

        entry = report["entry"]

        breakout = report["breakout"]
        setup = report.get("setup_evaluation", {})

        score = analysis.score

        risk_reward = entry["risk_reward"]

        # --------------------------------------------------
        # Rule 1
        # Weak Technical Structure
        # --------------------------------------------------

        category = setup.get("stage_1", {}).get("category", "REJECT")
        confirmed = bool((setup.get("entry_confirmation") or {}).get(
            "passed", setup.get("stage_2", {}).get("eligible", False)
        ))

        if category == "REJECT" or score < 40:

            return {

                "action": "AVOID",

                "confidence": 95,

                "reason": "Overall technical structure is weak."

            }

        # --------------------------------------------------
        # Rule 2
        # Confirmed Breakout
        # --------------------------------------------------

        if confirmed:

            return {

                "action": "BUY",

                "confidence": 95,

                "reason": f"{category} setup passed every entry-confirmation check."

            }

        # --------------------------------------------------
        # Rule 3
        # Good Risk Reward
        # --------------------------------------------------

        if category == "REVERSAL CANDIDATE":

            return {

                "action": "WATCH",

                "confidence": 70,

                "reason": "Oversold reversal candidate; wait for a bullish candle, volume above 1.2x, EMA20 recovery, and MACD confirmation."

            }

        # --------------------------------------------------
        # Rule 4
        # Watchlist
        # --------------------------------------------------

        if category in {"TREND FOLLOWING", "BREAKOUT", "PULLBACK", "WATCHLIST"}:

            return {

                "action": "WATCH",

                "confidence": 75,

                "reason": f"{category} setup detected, but entry confirmation is incomplete."

            }

        # --------------------------------------------------
        # Default
        # --------------------------------------------------

        return {

            "action": "WAIT",

            "confidence": 70,

            "reason": "Current setup is mixed. Wait for stronger confirmation."

        }
