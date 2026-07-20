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


if __name__ == "__main__":
    unittest.main()
