"""Persistent recommendation and outcome store for probability calibration."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


class OutcomeRepository:
    def __init__(self, path: str | Path = "data/cache/trades/outcomes.json"):
        self.path = Path(path)

    def _read(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return []

    def _write(self, rows: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(rows, indent=2))

    def record_recommendation(self, trade: dict[str, Any]) -> str:
        rows = self._read()
        identifier = f"{trade['symbol']}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        short_put = trade.get("short_put_strategy") or {}
        option = trade.get("option_strategy") or {}
        option_candidate = short_put.get("candidate") or {}
        rows.append({"id": identifier, "created_at": datetime.now(timezone.utc).isoformat(),
                     "symbol": trade["symbol"], "strategy": trade["strategy"],
                     "setup": trade.get("setup"),
                     "market_regime": trade.get("market_context", {}).get("regime"),
                     "sector": trade.get("sector"),
                     "direction": "BULLISH" if trade.get("recommendation") in {"BUY", "BUY ON DIP", "WATCH"} else "BEARISH",
                     "ai_score": trade["ai_score"], "estimated_probability": trade["probability"],
                     "entry_price": trade.get("levels", {}).get("entry"),
                     "stop_price": trade.get("levels", {}).get("stop_loss"),
                     "target_prices": [trade.get("levels", {}).get(name)
                                       for name in ("target_1", "target_2", "target_3")],
                     "expected_reward": trade.get("levels", {}).get("expected_reward"),
                     "initial_risk": (trade.get("levels", {}).get("entry", 0)
                                      - trade.get("levels", {}).get("stop_loss", 0))
                     if trade.get("levels", {}).get("entry") is not None
                     and trade.get("levels", {}).get("stop_loss") is not None else None,
                     "expected_value": trade.get("expected_value", {}).get("amount"),
                     "readiness_percent": trade.get("trade_readiness", {}).get("percentage"),
                     "option_strategy": short_put.get("strategy") or option.get("strategy"),
                     "option_approved": bool(short_put.get("available") or option.get("available")),
                     "option_expiry": option_candidate.get("expiry"),
                     "option_strike": option_candidate.get("sold_put_strike"),
                     "option_delta": option_candidate.get("delta"),
                     "option_iv": option_candidate.get("implied_volatility"),
                     "option_probability": option_candidate.get("probability_otm"),
                     "option_net_credit": short_put.get("net_credit"),
                     "outcome": None})
        self._write(rows)
        return identifier

    def record_paper_entry(self, order: dict[str, Any], analysis: dict[str, Any]) -> str:
        """Persist an actually filled paper BUY independently of recommendations."""
        rows = self._read()
        identifier = f"{order['symbol']}-PAPER-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        plan = analysis.get("trade_plan", {})
        rows.append({
            "id": identifier, "source": "PAPER_ORDER",
            "created_at": order.get("timestamp", datetime.now(timezone.utc).isoformat()),
            "entry_order_id": order.get("order_id"), "symbol": order["symbol"],
            "strategy": analysis.get("decision", {}).get("action", "BUY"),
            "direction": "BULLISH", "quantity": order.get("quantity"),
            "entry_price": order.get("filled_price"), "stop_price": plan.get("stop_loss"),
            "target_prices": [plan.get(name) for name in ("target1", "target2", "target3")],
            "estimated_probability": analysis.get("decision", {}).get("confidence"),
            "outcome": None,
        })
        self._write(rows)
        return identifier

    def close_paper_trade(self, symbol: str, exit_price: float,
                          exit_order_id: str | None = None) -> str | None:
        """Close the latest open, broker-originated paper trade for a symbol."""
        rows = self._read()
        for row in reversed(rows):
            if (row.get("source") == "PAPER_ORDER" and row.get("symbol") == symbol
                    and row.get("outcome") is None):
                entry = float(row["entry_price"])
                return_percent = (float(exit_price) - entry) * 100 / entry
                won = return_percent > 0
                predicted = min(100, max(0, float(row.get("estimated_probability") or 50))) / 100
                row.update({
                    "exit_order_id": exit_order_id, "exit_price": round(float(exit_price), 2),
                    "return_percent": round(return_percent, 2),
                    "outcome": "WIN" if won else "LOSS",
                    "probability_error": round((1.0 if won else 0.0) - predicted, 4),
                    "brier_score": round((predicted - (1.0 if won else 0.0)) ** 2, 4),
                    "maximum_favorable_excursion_percent": None,
                    "maximum_adverse_excursion_percent": None,
                    "closed_at": datetime.now(timezone.utc).isoformat(),
                })
                self._write(rows)
                return row["id"]
        return None

    def record_outcome(self, identifier: str, won: bool, return_percent: float | None = None,
                       exit_price: float | None = None, mfe_percent: float | None = None,
                       mae_percent: float | None = None) -> bool:
        rows = self._read()
        for row in rows:
            if row["id"] == identifier:
                actual = 1.0 if won else 0.0
                predicted = min(100, max(0, float(row.get("estimated_probability", 50)))) / 100
                row.update({"outcome": "WIN" if won else "LOSS", "return_percent": return_percent,
                            "exit_price": exit_price,
                            "maximum_favorable_excursion_percent": mfe_percent,
                            "maximum_adverse_excursion_percent": mae_percent,
                            "probability_error": round(actual - predicted, 4),
                            "brier_score": round((predicted - actual) ** 2, 4),
                            "closed_at": datetime.now(timezone.utc).isoformat()})
                self._write(rows)
                return True
        return False

    def calibrated_probability(self, strategy: str, minimum_samples: int = 20) -> float | None:
        rows = [row for row in self._read() if row.get("strategy") == strategy and row.get("outcome") in {"WIN", "LOSS"}]
        if len(rows) < minimum_samples:
            return None
        return round(sum(row["outcome"] == "WIN" for row in rows) * 100 / len(rows), 2)

    def option_calibrated_probability(self, option_strategy: str,
                                      minimum_samples: int = 20) -> float | None:
        rows = [row for row in self._read()
                if row.get("option_strategy") == option_strategy
                and row.get("outcome") in {"WIN", "LOSS"}]
        if len(rows) < minimum_samples:
            return None
        return round(sum(row["outcome"] == "WIN" for row in rows) * 100 / len(rows), 2)

    def contextual_probability(self, setup: str, market_regime: str,
                               minimum_samples: int = 20) -> float | None:
        rows = [row for row in self._read()
                if row.get("setup") == setup and row.get("market_regime") == market_regime
                and row.get("outcome") in {"WIN", "LOSS"}]
        if len(rows) < minimum_samples:
            return None
        return round(sum(row["outcome"] == "WIN" for row in rows) * 100 / len(rows), 2)

    def learning_summary(self) -> dict[str, Any]:
        completed = [row for row in self._read() if row.get("outcome") in {"WIN", "LOSS"}]

        def grouped(field: str) -> list[dict[str, Any]]:
            groups: dict[str, list[dict[str, Any]]] = {}
            for row in completed:
                value = row.get(field)
                if value:
                    groups.setdefault(str(value), []).append(row)
            result = []
            for value, rows in groups.items():
                wins = sum(row["outcome"] == "WIN" for row in rows)
                result.append({field: value, "recommendations": len(rows), "wins": wins,
                               "losses": len(rows) - wins,
                               "win_rate_percent": round(wins * 100 / len(rows), 2),
                               "confidence": "CALIBRATED" if len(rows) >= 20 else "EARLY"})
            return sorted(result, key=lambda item: (item["recommendations"], item["win_rate_percent"]), reverse=True)

        setup_regime: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in completed:
            if row.get("setup") and row.get("market_regime"):
                setup_regime.setdefault((row["setup"], row["market_regime"]), []).append(row)
        combinations = []
        for (setup, regime), rows in setup_regime.items():
            wins = sum(row["outcome"] == "WIN" for row in rows)
            combinations.append({"setup": setup, "market_regime": regime, "recommendations": len(rows),
                                 "wins": wins, "losses": len(rows) - wins,
                                 "win_rate_percent": round(wins * 100 / len(rows), 2),
                                 "confidence": "CALIBRATED" if len(rows) >= 20 else "EARLY"})
        combinations.sort(key=lambda item: item["recommendations"], reverse=True)
        brier_values = [row["brier_score"] for row in completed if row.get("brier_score") is not None]
        mfe_values = [row["maximum_favorable_excursion_percent"] for row in completed
                      if row.get("maximum_favorable_excursion_percent") is not None]
        mae_values = [row["maximum_adverse_excursion_percent"] for row in completed
                      if row.get("maximum_adverse_excursion_percent") is not None]
        return {"completed_outcomes": len(completed), "minimum_calibration_samples": 20,
                "recommended_validation_samples": 200,
                "calibration_stage": "VALIDATED" if len(completed) >= 200 else "CALIBRATING",
                "mean_brier_score": round(sum(brier_values) / len(brier_values), 4) if brier_values else None,
                "average_mfe_percent": round(sum(mfe_values) / len(mfe_values), 2) if mfe_values else None,
                "average_mae_percent": round(sum(mae_values) / len(mae_values), 2) if mae_values else None,
                "by_symbol": grouped("symbol"), "by_setup": grouped("setup"),
                "by_market_regime": grouped("market_regime"), "by_setup_and_regime": combinations}
