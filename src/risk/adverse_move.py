"""Empirical bullish target-versus-adverse-barrier analysis."""

from __future__ import annotations

import pandas as pd


class AdverseMoveRisk:
    @staticmethod
    def assess_intraday(intraday: pd.DataFrame, target_percent: float,
                        adverse_percent: float = 3.0, horizon_days: int = 5,
                        minimum_samples: int = 40, direction: str = "BULLISH") -> dict:
        """Use ordered intraday bars to resolve target, drawdown, and gap order."""
        required = {"Open", "High", "Low", "Close"}
        if (intraday is None or intraday.empty or not required.issubset(intraday.columns)
                or not isinstance(intraday.index, pd.DatetimeIndex)):
            return {"available": False, "reason": "Ordered intraday OHLC history is unavailable.",
                    "sample_count": 0, "data_resolution": "UNAVAILABLE"}
        data = intraday.sort_index().copy()
        direction = direction.upper()
        if direction not in {"BULLISH", "BEARISH"}:
            return {"available": False, "reason": f"Unsupported direction: {direction}",
                    "sample_count": 0, "data_resolution": "15_MINUTE"}
        for column in required:
            data[column] = pd.to_numeric(data[column], errors="coerce")
        data = data.dropna(subset=list(required))
        data["SESSION"] = data.index.normalize()
        sessions = list(data["SESSION"].drop_duplicates())
        summaries = data.groupby("SESSION").agg(Open=("Open", "first"), High=("High", "max"),
                                                 Low=("Low", "min"), Close=("Close", "last"))
        summaries["SMA20"] = summaries["Close"].rolling(20).mean()
        summaries["RETURN5"] = summaries["Close"].pct_change(5)
        observations = []
        for index in range(20, len(sessions) - horizon_days):
            session = sessions[index]
            row = summaries.loc[session]
            entry = float(row["Close"])
            comparable = (entry > float(row["SMA20"]) and float(row["RETURN5"]) > 0
                          if direction == "BULLISH" else
                          entry < float(row["SMA20"]) and float(row["RETURN5"]) < 0)
            if entry <= 0 or not comparable:
                continue
            target = entry * (1 + target_percent / 100) if direction == "BULLISH" else entry * (1 - target_percent / 100)
            barrier = entry * (1 - adverse_percent / 100) if direction == "BULLISH" else entry * (1 + adverse_percent / 100)
            future_sessions = sessions[index + 1:index + horizon_days + 1]
            future = data[data["SESSION"].isin(future_sessions)]
            first_session = future_sessions[0]
            first_open = float(future[future["SESSION"] == first_session].iloc[0]["Open"])
            overnight_gap = first_open <= barrier if direction == "BULLISH" else first_open >= barrier
            adverse_first = overnight_gap
            target_first = False
            if not adverse_first:
                for _, candle in future.iterrows():
                    hit_adverse = (float(candle["Low"]) <= barrier if direction == "BULLISH"
                                   else float(candle["High"]) >= barrier)
                    hit_target = (float(candle["High"]) >= target if direction == "BULLISH"
                                  else float(candle["Low"]) <= target)
                    if hit_adverse:  # conservative if both occur in one 15-minute bar
                        adverse_first = True
                        break
                    if hit_target:
                        target_first = True
                        break
            worst = ((float(future["Low"].min()) / entry - 1) * 100 if direction == "BULLISH"
                     else (float(future["High"].max()) / entry - 1) * 100)
            observations.append((target_first, adverse_first, overnight_gap, worst))
        count = len(observations)
        if count < minimum_samples:
            return {"available": False, "reason": f"Only {count} comparable intraday paths; "
                    f"{minimum_samples} required.", "sample_count": count, "data_resolution": "15_MINUTE"}
        drawdowns = sorted(item[3] for item in observations)
        return {
            "available": True, "model": "ORDERED_INTRADAY_OVERNIGHT_BARRIER_STUDY",
            "data_resolution": "15_MINUTE", "direction": direction, "horizon_days": horizon_days,
            "target_percent": round(target_percent, 3),
            "adverse_barrier_percent": round(adverse_percent, 3), "sample_count": count,
            "probability_stays_above_adverse_barrier": round(
                100 * sum(not item[1] for item in observations) / count, 2),
            "probability_target_before_adverse_barrier": round(
                100 * sum(item[0] for item in observations) / count, 2),
            "probability_no_overnight_gap_beyond_barrier": round(
                100 * sum(not item[2] for item in observations) / count, 2),
            "probability_overnight_gap_beyond_barrier": round(
                100 * sum(item[2] for item in observations) / count, 2),
            "median_worst_drawdown_percent": round(drawdowns[count // 2], 2),
            "same_bar_both_hit_policy": "ADVERSE_FIRST_CONSERVATIVE",
        }

    @staticmethod
    def assess(history: pd.DataFrame, target_percent: float, adverse_percent: float = 3.0,
               horizon_days: int = 5, minimum_samples: int = 60) -> dict:
        required = {"High", "Low", "Close"}
        if history is None or history.empty or not required.issubset(history.columns):
            return {"available": False, "reason": "OHLC history is unavailable.", "sample_count": 0}
        data = history.copy()
        for column in required:
            data[column] = pd.to_numeric(data[column], errors="coerce")
        data = data.dropna(subset=list(required))
        if len(data) < max(25, horizon_days + 21) or target_percent <= 0 or adverse_percent <= 0:
            return {"available": False, "reason": "Insufficient history or invalid barriers.",
                    "sample_count": 0}
        data["SMA20"] = data["Close"].rolling(20).mean()
        data["RETURN5"] = data["Close"].pct_change(5)
        observations = []
        for index in range(20, len(data) - horizon_days):
            entry = float(data.iloc[index]["Close"])
            if entry <= 0 or not (entry > float(data.iloc[index]["SMA20"])
                                  and float(data.iloc[index]["RETURN5"]) > 0):
                continue
            target = entry * (1 + target_percent / 100)
            barrier = entry * (1 - adverse_percent / 100)
            target_first = False
            adverse_seen = False
            for offset in range(1, horizon_days + 1):
                candle = data.iloc[index + offset]
                hit_target = float(candle["High"]) >= target
                hit_adverse = float(candle["Low"]) <= barrier
                # Intraday ordering is unknowable from daily bars. When both
                # occur on one candle, use the conservative adverse-first rule.
                if hit_adverse:
                    adverse_seen = True
                    break
                if hit_target:
                    target_first = True
                    break
            future = data.iloc[index + 1:index + horizon_days + 1]
            worst_drawdown = (float(future["Low"].min()) / entry - 1) * 100
            observations.append((target_first, adverse_seen, worst_drawdown))
        sample_count = len(observations)
        if sample_count < minimum_samples:
            return {"available": False, "reason": f"Only {sample_count} comparable bullish windows; "
                    f"{minimum_samples} required.", "sample_count": sample_count}
        stayed_above = sum(not item[1] for item in observations)
        target_first = sum(item[0] for item in observations)
        drawdowns = sorted(item[2] for item in observations)
        median_drawdown = drawdowns[len(drawdowns) // 2]
        return {
            "available": True, "model": "EMPIRICAL_BULLISH_BARRIER_STUDY",
            "horizon_days": horizon_days, "target_percent": round(target_percent, 3),
            "adverse_barrier_percent": round(adverse_percent, 3), "sample_count": sample_count,
            "probability_stays_above_adverse_barrier": round(stayed_above * 100 / sample_count, 2),
            "probability_target_before_adverse_barrier": round(target_first * 100 / sample_count, 2),
            "median_worst_drawdown_percent": round(median_drawdown, 2),
            "same_day_both_hit_policy": "ADVERSE_FIRST_CONSERVATIVE",
        }
