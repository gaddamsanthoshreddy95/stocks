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
                CREATE TABLE IF NOT EXISTS actual_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    instrument_type TEXT NOT NULL CHECK(instrument_type IN ('EQUITY','OPTION')),
                    side TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
                    strategy TEXT,
                    option_type TEXT CHECK(option_type IS NULL OR option_type IN ('CE','PE')),
                    strike REAL,
                    expiry TEXT,
                    quantity INTEGER NOT NULL CHECK(quantity > 0),
                    entry_date TEXT NOT NULL,
                    entry_price REAL NOT NULL CHECK(entry_price > 0),
                    stop_loss REAL,
                    target_price REAL,
                    status TEXT NOT NULL DEFAULT 'OPEN' CHECK(status IN ('OPEN','CLOSED')),
                    exit_date TEXT,
                    exit_price REAL,
                    fees REAL NOT NULL DEFAULT 0,
                    realized_pnl REAL,
                    notes TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_actual_trades_status
                    ON actual_trades(status, entry_date DESC);
                CREATE TABLE IF NOT EXISTS candidate_execution_marks (
                    run_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    execution_status TEXT NOT NULL CHECK(execution_status IN ('TRADED','NOT_TRADED')),
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, symbol)
                );
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

    def delete_report(self, report_id: int) -> bool:
        return self.delete_reports([report_id]) == 1

    def delete_reports(self, report_ids: list[int]) -> int:
        ids = sorted({int(item) for item in report_ids})
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with closing(self._connect()) as connection:
            run_ids = [row["run_id"] for row in connection.execute(
                f"SELECT run_id FROM report_runs WHERE id IN ({placeholders})", ids
            ).fetchall()]
            cursor = connection.execute(
                f"DELETE FROM report_runs WHERE id IN ({placeholders})", ids
            )
            if run_ids:
                run_placeholders = ",".join("?" for _ in run_ids)
                connection.execute(
                    f"DELETE FROM candidate_execution_marks WHERE run_id IN ({run_placeholders})",
                    run_ids,
                )
            connection.commit()
            return int(cursor.rowcount)

    def set_candidate_execution(self, run_id: str, symbol: str, traded: bool) -> None:
        run_id, symbol = str(run_id).strip(), str(symbol).strip().upper().removesuffix(".NS")
        if not run_id or not symbol:
            raise ValueError("Run ID and symbol are required.")
        with closing(self._connect()) as connection:
            connection.execute(
                """INSERT INTO candidate_execution_marks
                   (run_id, symbol, execution_status, updated_at) VALUES (?, ?, ?, ?)
                   ON CONFLICT(run_id, symbol) DO UPDATE SET
                     execution_status=excluded.execution_status,
                     updated_at=excluded.updated_at""",
                (run_id, symbol, "TRADED" if traded else "NOT_TRADED",
                 datetime.now(timezone.utc).isoformat()),
            )
            connection.commit()

    def get_candidate_executions(self, run_id: str) -> dict[str, str]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT symbol, execution_status FROM candidate_execution_marks WHERE run_id = ?",
                (str(run_id),),
            ).fetchall()
        return {str(row["symbol"]): str(row["execution_status"]) for row in rows}

    def add_actual_trade(self, trade: dict[str, Any]) -> int:
        symbol = str(trade.get("symbol", "")).strip().upper().removesuffix(".NS")
        instrument = str(trade.get("instrument_type", "EQUITY")).upper()
        side = str(trade.get("side", "BUY")).upper()
        quantity = int(trade.get("quantity", 0))
        entry_price = float(trade.get("entry_price", 0))
        if not symbol or instrument not in {"EQUITY", "OPTION"} or side not in {"BUY", "SELL"}:
            raise ValueError("Symbol, instrument type, or side is invalid.")
        if quantity <= 0 or entry_price <= 0:
            raise ValueError("Quantity and entry price must be positive.")
        option_type = str(trade.get("option_type") or "").upper() or None
        if instrument == "OPTION" and option_type not in {"CE", "PE"}:
            raise ValueError("Option trades require CE or PE.")
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """INSERT INTO actual_trades (
                    created_at, symbol, instrument_type, side, strategy, option_type,
                    strike, expiry, quantity, entry_date, entry_price, stop_loss,
                    target_price, fees, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (datetime.now(timezone.utc).isoformat(), symbol, instrument, side,
                 str(trade.get("strategy") or ""), option_type,
                 float(trade["strike"]) if trade.get("strike") else None,
                 str(trade.get("expiry") or "") or None, quantity,
                 str(trade.get("entry_date")), entry_price,
                 float(trade["stop_loss"]) if trade.get("stop_loss") else None,
                 float(trade["target_price"]) if trade.get("target_price") else None,
                 max(0.0, float(trade.get("fees") or 0)), str(trade.get("notes") or "")),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def list_actual_trades(self, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM actual_trades"
        parameters: tuple[Any, ...] = ()
        if status:
            status = status.upper()
            if status not in {"OPEN", "CLOSED"}:
                raise ValueError("Trade status must be OPEN or CLOSED.")
            query += " WHERE status = ?"
            parameters = (status,)
        query += " ORDER BY entry_date DESC, id DESC"
        with closing(self._connect()) as connection:
            return [dict(row) for row in connection.execute(query, parameters).fetchall()]

    def close_actual_trade(self, trade_id: int, exit_date: str, exit_price: float,
                           additional_fees: float = 0) -> float:
        exit_price, additional_fees = float(exit_price), max(0.0, float(additional_fees))
        if exit_price <= 0:
            raise ValueError("Exit price must be positive.")
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM actual_trades WHERE id = ?", (int(trade_id),)
            ).fetchone()
            if not row or row["status"] != "OPEN":
                raise ValueError("Open trade was not found.")
            direction = 1 if row["side"] == "BUY" else -1
            fees = float(row["fees"] or 0) + additional_fees
            pnl = (exit_price - float(row["entry_price"])) * int(row["quantity"]) * direction - fees
            connection.execute(
                """UPDATE actual_trades SET status='CLOSED', exit_date=?, exit_price=?,
                   fees=?, realized_pnl=? WHERE id=?""",
                (str(exit_date), exit_price, fees, round(pnl, 2), int(trade_id)),
            )
            connection.commit()
            return round(pnl, 2)

    def delete_actual_trade(self, trade_id: int) -> bool:
        with closing(self._connect()) as connection:
            cursor = connection.execute("DELETE FROM actual_trades WHERE id = ?", (int(trade_id),))
            connection.commit()
            return cursor.rowcount == 1

    def actual_trade_summary(self) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """SELECT COUNT(*) total,
                          SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) open_count,
                          SUM(CASE WHEN status='CLOSED' THEN 1 ELSE 0 END) closed_count,
                          COALESCE(SUM(realized_pnl), 0) realized_pnl
                   FROM actual_trades"""
            ).fetchone()
        return {"total": int(row["total"] or 0), "open": int(row["open_count"] or 0),
                "closed": int(row["closed_count"] or 0),
                "realized_pnl": round(float(row["realized_pnl"] or 0), 2)}

    def counts(self) -> dict[str, int]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """SELECT COUNT(*) AS reports,
                          COALESCE(SUM(trades_generated), 0) AS generated_trades
                   FROM report_runs"""
            ).fetchone()
        return {"reports": int(row["reports"]), "generated_trades": int(row["generated_trades"])}
