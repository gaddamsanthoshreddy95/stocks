"""Event-driven setup backtester with realistic next-bar execution."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

import pandas as pd


class SetupBacktester:
    def __init__(self, slippage_bps: float = 5, transaction_cost_bps: float = 10,
                 ambiguous_policy: str = "STOP_FIRST"):
        self.slippage_bps = slippage_bps
        self.transaction_cost_bps = transaction_cost_bps
        self.ambiguous_policy = ambiguous_policy

    def run(self, df: pd.DataFrame, signal_factory: Callable[[pd.DataFrame], list[dict[str, Any]]]) -> dict[str, Any]:
        trades = []
        for i in range(2, len(df) - 1):
            # Prefix-only invocation is the explicit look-ahead guard.
            for signal in signal_factory(df.iloc[:i + 1]):
                if signal.get("confirmation_timestamp") not in (None, df.index[i]):
                    continue
                trade = self._simulate(df, i + 1, signal)
                if trade:
                    trades.append(trade)
        return {"trades": trades, "metrics": self.metrics(trades), "groups": self._groups(trades)}

    def _simulate(self, df: pd.DataFrame, start: int, signal: dict[str, Any]) -> dict[str, Any] | None:
        bullish = signal["direction"] == "BULLISH"
        raw_entry = float(df.iloc[start]["Open"])
        entry = raw_entry * (1 + self.slippage_bps / 10000 * (1 if bullish else -1))
        stop, targets = float(signal["stop_loss"]), signal.get("targets", [])
        if not targets:
            return None
        target = float(targets[0])
        mae = mfe = 0.0
        for i in range(start, len(df)):
            row = df.iloc[i]
            adverse = entry - float(row["Low"]) if bullish else float(row["High"]) - entry
            favourable = float(row["High"]) - entry if bullish else entry - float(row["Low"])
            mae, mfe = max(mae, adverse), max(mfe, favourable)
            stop_hit = float(row["Low"]) <= stop if bullish else float(row["High"]) >= stop
            target_hit = float(row["High"]) >= target if bullish else float(row["Low"]) <= target
            if stop_hit or target_hit:
                if stop_hit and target_hit:
                    target_hit = self.ambiguous_policy == "TARGET_FIRST"
                exit_price = target if target_hit else (
                    min(stop, float(row["Open"])) if bullish else max(stop, float(row["Open"])))
                gross = (exit_price - entry) * (1 if bullish else -1)
                cost = (entry + exit_price) * self.transaction_cost_bps / 10000
                return {**signal, "entry_timestamp": df.index[start], "exit_timestamp": df.index[i],
                        "entry": entry, "exit": exit_price, "return": gross / entry,
                        "pnl": gross - cost, "mae": mae, "mfe": mfe,
                        "stop_hit": not target_hit, "target_hit": target_hit,
                        "holding_period": i - start + 1}
        return None

    @staticmethod
    def metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
        if not trades:
            return {"number_of_trades": 0}
        returns = pd.Series([t["return"] for t in trades])
        pnl = pd.Series([t["pnl"] for t in trades])
        equity, peak = pnl.cumsum(), pnl.cumsum().cummax()
        wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
        return {"number_of_trades": len(trades), "win_rate": float((pnl > 0).mean()),
                "average_return": float(returns.mean()), "median_return": float(returns.median()),
                "maximum_adverse_excursion": max(t["mae"] for t in trades),
                "maximum_favourable_excursion": max(t["mfe"] for t in trades),
                "average_risk_reward_achieved": float(sum(max(t.get("risk_reward", [0])) for t in trades) / len(trades)),
                "stop_hit_rate": sum(t["stop_hit"] for t in trades) / len(trades),
                "target_hit_rate": sum(t["target_hit"] for t in trades) / len(trades),
                "expectancy": float(pnl.mean()),
                "profit_factor": float(wins.sum() / abs(losses.sum())) if losses.sum() else float("inf"),
                "maximum_drawdown": float((equity - peak).min()),
                "average_holding_period": sum(t["holding_period"] for t in trades) / len(trades)}

    @staticmethod
    def _groups(trades: list[dict[str, Any]]) -> dict[str, Any]:
        grouped: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
        for trade in trades:
            for field in ("pattern_used", "reversal_model", "entry_mode", "zone_strength",
                          "breakout_quality", "timeframe", "confidence_bucket", "direction"):
                grouped[field][str(trade.get(field, "UNKNOWN"))].append(trade)
        return {field: {name: SetupBacktester.metrics(items) for name, items in values.items()}
                for field, values in grouped.items()}
