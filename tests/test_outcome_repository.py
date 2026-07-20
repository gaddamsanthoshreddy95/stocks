import tempfile
import unittest
from pathlib import Path

from src.learning.outcome_repository import OutcomeRepository


class OutcomeRepositoryTests(unittest.TestCase):
    def test_records_and_calibrates_completed_outcomes(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = OutcomeRepository(Path(directory) / "outcomes.json")
            ids = [repository.record_recommendation({"symbol": "SBIN", "strategy": "BUY", "ai_score": 80, "probability": 70}) for _ in range(20)]
            for index, identifier in enumerate(ids):
                self.assertTrue(repository.record_outcome(identifier, won=index < 15))
            self.assertEqual(repository.calibrated_probability("BUY"), 75.0)

    def test_records_and_calibrates_option_strategy_independently(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = OutcomeRepository(Path(directory) / "outcomes.json")
            trade = {
                "symbol": "SBIN", "strategy": "BUY", "ai_score": 80, "probability": 70,
                "short_put_strategy": {
                    "available": True, "strategy": "BULL_PUT_SPREAD", "net_credit": 2.5,
                    "candidate": {"expiry": "2026-08-27", "sold_put_strike": 750,
                                  "delta": -.2, "implied_volatility": 24,
                                  "probability_otm": 80},
                },
            }
            identifiers = [repository.record_recommendation(trade) for _ in range(20)]
            for index, identifier in enumerate(identifiers):
                repository.record_outcome(identifier, won=index < 16)
            self.assertEqual(repository.option_calibrated_probability("BULL_PUT_SPREAD"), 80.0)

    def test_records_execution_path_and_probability_calibration(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = OutcomeRepository(Path(directory) / "outcomes.json")
            trade = {
                "symbol": "OIL", "strategy": "BUY", "ai_score": 82,
                "probability": 80, "levels": {"entry": 100, "stop_loss": 95,
                "target_1": 108, "target_2": 112, "target_3": 115,
                "expected_reward": 8}, "expected_value": {"amount": 5.4},
                "trade_readiness": {"percentage": 88},
            }
            identifier = repository.record_recommendation(trade)
            repository.record_outcome(identifier, True, 6, 106, 9, -2)
            row = repository._read()[0]
            self.assertEqual(row["entry_price"], 100)
            self.assertEqual(row["exit_price"], 106)
            self.assertEqual(row["maximum_favorable_excursion_percent"], 9)
            self.assertEqual(row["maximum_adverse_excursion_percent"], -2)
            self.assertEqual(row["brier_score"], .04)
            summary = repository.learning_summary()
            self.assertEqual(summary["average_mfe_percent"], 9)
            self.assertEqual(summary["calibration_stage"], "CALIBRATING")

    def test_paper_orders_are_opened_and_closed_durably(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = OutcomeRepository(Path(directory) / "outcomes.json")
            identifier = repository.record_paper_entry(
                {"symbol": "JSWSTEEL", "order_id": "buy-1", "quantity": 10,
                 "filled_price": 100, "timestamp": "2026-01-01T00:00:00Z"},
                {"trade_plan": {"stop_loss": 95, "target1": 108,
                 "target2": 112, "target3": 115},
                 "decision": {"action": "BUY", "confidence": 80}},
            )
            self.assertEqual(
                repository.close_paper_trade("JSWSTEEL", 110, "sell-1"), identifier
            )
            row = repository._read()[0]
            self.assertEqual(row["outcome"], "WIN")
            self.assertEqual(row["return_percent"], 10)
