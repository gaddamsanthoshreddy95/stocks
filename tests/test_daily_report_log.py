"""Tests for the daily-report output log lifecycle."""

import logging
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from main import _append_report_output, _configure_logging


class DailyReportLogTests(unittest.TestCase):
    def tearDown(self):
        logging.shutdown()

    def test_existing_log_is_replaced_before_report_is_appended(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "daily.log"
            path.parent.mkdir()
            path.write_text("stale report\n", encoding="utf-8")

            configured = _configure_logging(str(path))
            logging.getLogger("test.daily").info("runtime event")
            _append_report_output(configured, "FINAL REPORT")
            logging.shutdown()

            contents = path.read_text(encoding="utf-8")
            self.assertNotIn("stale report", contents)
            self.assertIn("runtime event", contents)
            self.assertTrue(contents.endswith("FINAL REPORT\n"))
