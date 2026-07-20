"""
Market Regime Engine

Converts raw market snapshot into
market intelligence.
"""

from src.models.decision_context import DecisionContext


class MarketRegime:

    @staticmethod
    def classify(snapshot):

        score = 0

        reasons = []

        # ----------------------------------------
        # INDIA
        # ----------------------------------------

        nifty = snapshot.get("india", {}).get("nifty")

        if nifty:

            cp = nifty["change_percent"]

            if cp > 1:

                score += 20
                reasons.append("NIFTY is strongly bullish.")

            elif cp > 0:

                score += 10
                reasons.append("NIFTY is positive.")

            elif cp < -1:

                score -= 20
                reasons.append("NIFTY is strongly bearish.")

            else:

                score -= 10
                reasons.append("NIFTY is weak.")

        # ----------------------------------------
        # BANK NIFTY
        # ----------------------------------------

        bank = snapshot.get("india", {}).get("banknifty")

        if bank:

            cp = bank["change_percent"]

            if cp > 1:

                score += 20
                reasons.append("BankNifty is leading the market.")

            elif cp > 0:

                score += 10
                reasons.append("BankNifty is positive.")

            elif cp < -1:

                score -= 20
                reasons.append("BankNifty is weak.")

            else:

                score -= 10
                reasons.append("BankNifty is under pressure.")

        # ----------------------------------------
        # US FUTURES
        # ----------------------------------------

        us = snapshot.get("global", {}).get("sp500_futures")

        if us:

            cp = us["change_percent"]

            if cp > 1:

                score += 20
                reasons.append("US Futures are strongly positive.")

            elif cp > 0:

                score += 10
                reasons.append("US Futures are positive.")

            elif cp < -1:

                score -= 20
                reasons.append("US Futures are weak.")

            else:

                score -= 10
                reasons.append("US Futures are negative.")

        # ----------------------------------------
        # INDIA VIX
        # ----------------------------------------

        vix = snapshot.get("volatility")

        if vix:

            cp = vix["change_percent"]

            if cp < -5:

                score += 20
                reasons.append("India VIX is falling.")

            elif cp > 5:

                score -= 20
                reasons.append("India VIX is rising.")

        # ----------------------------------------
        # MARKET REGIME
        # ----------------------------------------

        if score >= 60:

            regime = "STRONG_BULLISH"

        elif score >= 30:

            regime = "BULLISH"

        elif score >= 0:

            regime = "NEUTRAL"

        elif score >= -30:

            regime = "BEARISH"

        else:

            regime = "STRONG_BEARISH"

        confidence = min(abs(score), 100)

        return DecisionContext(

            engine="MARKET",

            status=regime,

            score=score,

            confidence=confidence,

            reasons=reasons,

            warnings=[],

            metadata={
                "snapshot_time": str(snapshot["timestamp"])
            }

        )
