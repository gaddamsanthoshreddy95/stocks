import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.ui.database import ReportDatabase
from ui_app import DailyReportJobs


class BackgroundReportJobTests(unittest.TestCase):
    def test_report_continues_and_saves_without_page_request(self):
        with tempfile.TemporaryDirectory() as directory:
            database = ReportDatabase(Path(directory) / "reports.db")
            report = {
                "run_id": "background-1", "date": "2026-07-21",
                "summary": {"context_reviewed": 1, "trades_generated": 0},
                "market": {"regime": "NEUTRAL"},
            }
            platform = SimpleNamespace(
                settings=SimpleNamespace(market_data_source="cache"),
                daily_report=lambda limit, score, month: report,
            )
            jobs = DailyReportJobs()

            job_id = jobs.submit(platform, database, 5, 40, None)
            result = jobs.future(job_id).result(timeout=5)

            self.assertEqual(result["report"], report)
            self.assertEqual(database.list_reports()[0]["run_id"], "background-1")


if __name__ == "__main__":
    unittest.main()
