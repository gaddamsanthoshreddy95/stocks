import tempfile
import unittest
from pathlib import Path

from src.ui.database import ReportDatabase


class ReportDatabaseTests(unittest.TestCase):
    def test_report_round_trip_and_summary_index(self):
        with tempfile.TemporaryDirectory() as directory:
            database = ReportDatabase(Path(directory) / "reports.db")
            report = {
                "run_id": "run-1", "date": "2026-07-20",
                "market": {"regime": "NEUTRAL"},
                "summary": {"context_reviewed": 7, "trades_generated": 2},
                "trades": [{"symbol": "SBIN"}], "watchlist": [],
            }
            report_id = database.save_report(report, "cache")
            loaded = database.get_report(report_id)
            self.assertEqual(loaded["run_id"], "run-1")
            self.assertEqual(database.list_reports()[0]["candidates_reviewed"], 7)
            self.assertEqual(database.counts(), {"reports": 1, "generated_trades": 2})

    def test_saving_same_run_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            database = ReportDatabase(Path(directory) / "reports.db")
            report = {"run_id": "same", "date": "2026-07-20", "market": {}, "summary": {}}
            first = database.save_report(report, "cache")
            second = database.save_report(report, "cache")
            self.assertEqual(first, second)
            self.assertEqual(database.counts()["reports"], 1)

    def test_selected_report_can_be_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            database = ReportDatabase(Path(directory) / "reports.db")
            report_id = database.save_report(
                {"run_id": "delete-me", "date": "2026-07-20", "market": {}, "summary": {}},
                "cache",
            )
            self.assertTrue(database.delete_report(report_id))
            self.assertIsNone(database.get_report(report_id))
            self.assertFalse(database.delete_report(report_id))

    def test_actual_trade_round_trip_and_buy_pnl(self):
        with tempfile.TemporaryDirectory() as directory:
            database = ReportDatabase(Path(directory) / "reports.db")
            trade_id = database.add_actual_trade({
                "symbol": "SBIN", "instrument_type": "EQUITY", "side": "BUY",
                "quantity": 10, "entry_date": "2026-07-20", "entry_price": 800,
                "fees": 10,
            })
            self.assertEqual(database.actual_trade_summary()["open"], 1)
            pnl = database.close_actual_trade(trade_id, "2026-07-21", 810, 5)
            self.assertEqual(pnl, 85)
            self.assertEqual(database.list_actual_trades()[0]["status"], "CLOSED")
            self.assertEqual(database.actual_trade_summary()["realized_pnl"], 85)

    def test_actual_sell_trade_pnl_and_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            database = ReportDatabase(Path(directory) / "reports.db")
            trade_id = database.add_actual_trade({
                "symbol": "NIFTY", "instrument_type": "OPTION", "option_type": "PE",
                "strike": 25000, "expiry": "2026-07-30", "side": "SELL",
                "quantity": 75, "entry_date": "2026-07-20", "entry_price": 100,
            })
            self.assertEqual(database.close_actual_trade(trade_id, "2026-07-21", 80), 1500)
            self.assertTrue(database.delete_actual_trade(trade_id))
            self.assertEqual(database.list_actual_trades(), [])

    def test_candidate_trade_mark_and_bulk_report_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            database = ReportDatabase(Path(directory) / "reports.db")
            first = database.save_report(
                {"run_id": "run-a", "date": "2026-07-20", "market": {}, "summary": {}}, "cache")
            second = database.save_report(
                {"run_id": "run-b", "date": "2026-07-21", "market": {}, "summary": {}}, "cache")
            database.set_candidate_execution("run-a", "PREMIERENE", True)
            self.assertEqual(database.get_candidate_executions("run-a")["PREMIERENE"], "TRADED")
            self.assertEqual(database.delete_reports([first, second]), 2)
            self.assertEqual(database.list_reports(), [])
            self.assertEqual(database.get_candidate_executions("run-a"), {})

    def test_traded_suggestions_preserve_original_recommendation_status(self):
        with tempfile.TemporaryDirectory() as directory:
            database = ReportDatabase(Path(directory) / "reports.db")
            database.save_report({
                "run_id": "run-rejected", "date": "2026-07-20", "market": {}, "summary": {},
                "trades": [], "watchlist": [],
                "rejected": [{"symbol": "PREMIERENE", "status": "REJECTED",
                              "final_action": "REJECT", "execution_readiness_score": 55}],
            }, "cache")
            database.set_candidate_execution("run-rejected", "PREMIERENE", True)
            rows = database.list_traded_suggestions()
            self.assertEqual(rows[0]["symbol"], "PREMIERENE")
            self.assertEqual(rows[0]["original_status"], "REJECTED")
            self.assertEqual(rows[0]["original_action"], "REJECT")


if __name__ == "__main__":
    unittest.main()
