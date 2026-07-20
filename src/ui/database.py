"""Small SQLite repository for immutable UI report snapshots."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any


class ReportDatabase:
    """Persist generated reports without changing the analytical backend."""

    def __init__(self, path: str | Path = "data/ui/stock_analyzer.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS report_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL UNIQUE,
                    generated_at TEXT NOT NULL,
                    report_date TEXT NOT NULL,
                    market_data_source TEXT NOT NULL,
                    market_regime TEXT,
                    candidates_reviewed INTEGER NOT NULL DEFAULT 0,
                    trades_generated INTEGER NOT NULL DEFAULT 0,
                    report_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_report_runs_generated_at
                    ON report_runs(generated_at DESC);
                """
            )
            connection.commit()

    def save_report(self, report: dict[str, Any], market_data_source: str) -> int:
        generated_at = datetime.now(timezone.utc).isoformat()
        run_id = str(report.get("run_id") or f"ui-{generated_at}")
        summary = report.get("summary", {})
        payload = json.dumps(report, default=str, separators=(",", ":"))
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT INTO report_runs (
                    run_id, generated_at, report_date, market_data_source,
                    market_regime, candidates_reviewed, trades_generated, report_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET report_json=excluded.report_json
                """,
                (run_id, generated_at, str(report.get("date", "")), market_data_source,
                 str(report.get("market", {}).get("regime", "UNAVAILABLE")),
                 int(summary.get("context_reviewed", 0)),
                 int(summary.get("trades_generated", 0)), payload),
            )
            connection.commit()
            row = connection.execute("SELECT id FROM report_runs WHERE run_id = ?", (run_id,)).fetchone()
            return int(row["id"] if row else cursor.lastrowid)

    def list_reports(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT id, run_id, generated_at, report_date, market_data_source,
                          market_regime, candidates_reviewed, trades_generated
                   FROM report_runs ORDER BY generated_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_report(self, report_id: int) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT report_json FROM report_runs WHERE id = ?", (int(report_id),)
            ).fetchone()
        return json.loads(row["report_json"]) if row else None

    def counts(self) -> dict[str, int]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """SELECT COUNT(*) AS reports,
                          COALESCE(SUM(trades_generated), 0) AS generated_trades
                   FROM report_runs"""
            ).fetchone()
        return {"reports": int(row["reports"]), "generated_trades": int(row["generated_trades"])}
