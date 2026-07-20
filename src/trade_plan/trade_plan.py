"""
Trade Plan Engine

Generates a complete trade plan using
support, resistance and risk-reward.
"""

from src.models.trade_plan import TradePlan
from src.config.trading_config import EQUITY_MIN_RISK_REWARD


class TradePlanEngine:

    BREAKOUT_THRESHOLD = 65.0
    STRONG_BREAKOUT_THRESHOLD = 80.0

    @staticmethod
    def generate(entry_report, breakout_probability=None):

        current_price = entry_report["current_price"]

        support = entry_report["support"]

        resistance = entry_report["resistance"]

        probability = float(
            entry_report.get("breakout_probability", 0)
            if breakout_probability is None else breakout_probability
        )

        # ----------------------------------------------------

        if support is None:

            return TradePlan(

                entry=0.0,

                stop_loss=0.0,

                target1=0.0,

                target2=0.0,

                target3=0.0,

                risk=0.0,

                reward=0.0,

                risk_reward=0.0,

                quality="UNKNOWN"

            )

        # ----------------------------------------------------

        entry = current_price

        atr = max(float(entry_report.get("atr") or 0), 0)
        minimum_distance = max(abs(entry - support), atr * 0.75, entry * 0.0075)
        stop_loss = entry - minimum_distance
        risk = abs(entry - stop_loss)
        atr_projection_2 = entry + max(atr * 2, risk * 2)
        atr_projection_3 = entry + max(atr * 3, risk * 3)
        target1 = resistance if resistance is not None and resistance > entry else entry + max(atr, risk)
        candidates = [
            float(level) for level in entry_report.get("resistance_levels", [])
            if float(level) > target1
        ]
        next_resistance = entry_report.get("next_resistance")
        if next_resistance and float(next_resistance) > target1:
            candidates.append(float(next_resistance))
        target2 = min(candidates) if candidates else atr_projection_2
        target2 = max(target2, target1)
        target3 = max(atr_projection_3, target2)

        rewards = [max(0, target - entry) for target in (target1, target2, target3)]
        nearest_reward = rewards[0]
        resistance_is_close = nearest_reward < atr if atr > 0 else False
        breakout_adjusted = resistance_is_close and probability >= TradePlanEngine.BREAKOUT_THRESHOLD
        strong_breakout = resistance_is_close and probability >= TradePlanEngine.STRONG_BREAKOUT_THRESHOLD
        if strong_breakout:
            # At very high conviction, treat the first level as the breakout
            # trigger and manage the trade against the next credible target.
            expected_reward = rewards[1]
            target_basis = "SECOND_TARGET_BREAKOUT"
        elif breakout_adjusted:
            expected_reward = .5 * rewards[0] + .3 * rewards[1] + .2 * rewards[2]
            target_basis = "BREAKOUT_WEIGHTED_TARGETS"
        else:
            expected_reward = nearest_reward
            target_basis = "NEAREST_RESISTANCE"
        reward = expected_reward
        risk_reward = expected_reward / risk if risk > 0 else 0
        quality = "GOOD" if risk_reward >= 2 else "AVERAGE" if risk_reward >= EQUITY_MIN_RISK_REWARD else "POOR"
        diagnostics = []
        if resistance_is_close:
            diagnostics.append(
                f"Nearest resistance is only {nearest_reward:.2f} away versus ATR {atr:.2f}."
            )
        if strong_breakout:
            diagnostics.append(
                f"Strong breakout probability ({probability:.0f}%) treats nearest resistance as a trigger and uses the second target."
            )
        elif breakout_adjusted:
            diagnostics.append(
                f"Credible breakout ({probability:.0f}%) activates weighted targets beyond nearest resistance."
            )
        elif resistance_is_close:
            diagnostics.append(
                f"Target too close; breakout probability {probability:.0f}% is below the {TradePlanEngine.BREAKOUT_THRESHOLD:.0f}% requirement."
            )

        # ----------------------------------------------------

        return TradePlan(

            entry=round(entry, 2),

            stop_loss=round(stop_loss, 2),

            target1=round(target1, 2),

            target2=round(target2, 2),

            target3=round(target3, 2),

            risk=round(risk, 2),

            reward=round(reward, 2),

            risk_reward=round(risk_reward, 2),

            quality=quality,

            expected_reward=round(expected_reward, 2),

            nearest_target_reward=round(nearest_reward, 2),

            target_basis=target_basis,

            breakout_probability=round(probability, 2),

            diagnostics=diagnostics,

        )
