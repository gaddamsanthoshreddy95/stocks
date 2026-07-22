"""Read-only, secret-safe tools shared by the UI, OpenAI, and MCP surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from src.ui.database import ReportDatabase


_BLOCKED_NAMES = {".env", ".env.local", ".env.production", "secrets.toml"}
_BLOCKED_PARTS = {".git", ".venv", "__pycache__", ".cache", "data", "reports"}
_ALLOWED_SUFFIXES = {".py", ".md", ".toml", ".json", ".yaml", ".yml", ".txt", ".bat"}


@dataclass(frozen=True)
class UIContext:
    page: str = "AI Assistant"
    symbol: str | None = None
    report_id: int | None = None
    active_tab: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"page": self.page, "symbol": self.symbol, "report_id": self.report_id,
                "active_tab": self.active_tab}


class StockAnalyzerTools:
    """Expose bounded reads without credentials, arbitrary SQL, or shell execution."""

    def __init__(self, database: ReportDatabase, repository_root: str | Path):
        self.database = database
        self.root = Path(repository_root).resolve()

    def latest_report(self) -> dict[str, Any] | None:
        rows = self.database.list_reports(1)
        return self.database.get_report(int(rows[0]["id"])) if rows else None

    def report(self, report_id: int | None = None) -> dict[str, Any] | None:
        return self.database.get_report(int(report_id)) if report_id else self.latest_report()

    @staticmethod
    def _candidate_from(report: dict[str, Any] | None, symbol: str) -> dict[str, Any] | None:
        normalized = str(symbol).strip().upper().removesuffix(".NS")
        for bucket in ("trades", "watchlist", "rejected"):
            for candidate in (report or {}).get(bucket, []):
                if str(candidate.get("symbol", "")).upper().removesuffix(".NS") == normalized:
                    return {**candidate, "_report_bucket": bucket.upper()}
        return None

    def candidate(self, symbol: str, report_id: int | None = None) -> dict[str, Any]:
        report = self.report(report_id)
        candidate = self._candidate_from(report, symbol)
        if not report:
            return {"available": False, "reason": "No saved report is available."}
        if not candidate:
            return {"available": False, "reason": f"{symbol.upper()} is not in the selected report.",
                    "run_id": report.get("run_id")}
        return {"available": True, "run_id": report.get("run_id"),
                "report_date": report.get("date"), "candidate": candidate}

    def candidate_summary(self, symbol: str, report_id: int | None = None) -> dict[str, Any]:
        result = self.candidate(symbol, report_id)
        if not result.get("available"):
            return result
        item = result["candidate"]
        keys = ("symbol", "status", "final_action", "selection_status", "selection_reason",
                "quality_grade", "quality_score", "execution_readiness_score", "probability",
                "rejection_reason", "reasons")
        return {"available": True, "run_id": result["run_id"],
                "report_date": result["report_date"],
                "decision": {key: item.get(key) for key in keys},
                "levels": item.get("levels") or {},
                "adverse_move_risk": item.get("adverse_move_risk") or {},
                "stock_selection_filters": item.get("stock_selection_filters") or {},
                "entry_confirmation": item.get("entry_confirmation") or {},
                "market": item.get("market") or item.get("market_context") or {},
                "sector": item.get("sector_context") or {},
                "news": item.get("news") or {}, "event_risk": item.get("event_risk") or {},
                "relative_strength": item.get("relative_strength") or {},
                "evidence": item.get("evidence") or item.get("evidence_summary") or {},
                "option_approval": item.get("option_trade_approval") or {}}

    def selected_stocks(self, report_id: int | None = None) -> dict[str, Any]:
        report = self.report(report_id)
        if not report:
            return {"available": False, "reason": "No saved report is available.", "items": []}
        items = []
        for bucket in ("trades", "watchlist", "rejected"):
            for item in report.get(bucket, []):
                adverse = item.get("adverse_move_risk") or {}
                items.append({"symbol": item.get("symbol"), "bucket": bucket.upper(),
                              "status": item.get("status"), "action": item.get("final_action"),
                              "quality": item.get("quality_score"),
                              "readiness": item.get("execution_readiness_score"),
                              "reason": item.get("selection_reason") or item.get("rejection_reason"),
                              "adverse_move_risk": {
                                  "available": adverse.get("available", False),
                                  "horizon_days": adverse.get("horizon_days"),
                                  "target_percent": adverse.get("target_percent"),
                                  "adverse_barrier_percent": adverse.get("adverse_barrier_percent"),
                                  "probability_adverse_barrier_before_target": adverse.get(
                                      "probability_adverse_barrier_before_target"),
                                  "probability_target_before_adverse_barrier": adverse.get(
                                      "probability_target_before_adverse_barrier"),
                                  "sample_count": adverse.get("sample_count"),
                                  "reliability": adverse.get("reliability"),
                                  "reason": adverse.get("reason"),
                              }})
        return {"available": True, "run_id": report.get("run_id"),
                "report_date": report.get("date"), "items": items}

    def _safe_project_file(self, relative_path: str) -> Path:
        candidate = (self.root / str(relative_path)).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("Project path escapes the repository root.")
        relative = candidate.relative_to(self.root)
        if (candidate.name in _BLOCKED_NAMES or any(part in _BLOCKED_PARTS for part in relative.parts)
                or candidate.suffix.lower() not in _ALLOWED_SUFFIXES):
            raise ValueError("This file is excluded from assistant access.")
        return candidate

    def read_project_file(self, relative_path: str, start_line: int = 1,
                          max_lines: int = 160) -> dict[str, Any]:
        path = self._safe_project_file(relative_path)
        if not path.is_file():
            return {"available": False, "reason": "File does not exist."}
        start, limit = max(1, int(start_line)), max(1, min(int(max_lines), 300))
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        selected = lines[start - 1:start - 1 + limit]
        return {"available": True, "path": str(path.relative_to(self.root)),
                "start_line": start, "end_line": start + len(selected) - 1,
                "content": "\n".join(selected), "truncated": start - 1 + limit < len(lines)}

    def search_project(self, query: str, max_results: int = 12) -> dict[str, Any]:
        terms = [term.lower() for term in re.findall(r"[A-Za-z0-9_]{3,}", str(query))][:8]
        if not terms:
            return {"query": query, "results": []}
        results = []
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(self.root)
            if (path.name in _BLOCKED_NAMES or any(part in _BLOCKED_PARTS for part in relative.parts)
                    or path.suffix.lower() not in _ALLOWED_SUFFIXES):
                continue
            try:
                for line_number, line in enumerate(
                        path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    lowered = line.lower()
                    score = sum(term in lowered for term in terms)
                    if score:
                        results.append({"path": str(relative), "line": line_number,
                                        "text": line.strip()[:300], "score": score})
            except OSError:
                continue
        results.sort(key=lambda row: (-row["score"], row["path"], row["line"]))
        return {"query": query, "results": results[:max(1, min(int(max_results), 30))]}

    def context_for_question(self, question: str, ui: UIContext | None = None) -> dict[str, Any]:
        ui = ui or UIContext()
        context: dict[str, Any] = {"ui": ui.as_dict(), "selected": self.selected_stocks(ui.report_id)}
        if ui.symbol:
            context["candidate"] = self.candidate_summary(ui.symbol, ui.report_id)
        code_words = {"code", "function", "file", "implementation", "calculate", "calculation",
                      "bug", "error", "fix", "architecture", "test", "project"}
        if code_words.intersection(set(re.findall(r"[a-z]+", question.lower()))):
            search = self.search_project(question)
            context["project_search"] = search
            snippets = []
            seen = set()
            for match in search["results"]:
                if match["path"] in seen:
                    continue
                seen.add(match["path"])
                snippets.append(self.read_project_file(
                    match["path"], max(1, int(match["line"]) - 20), 80))
                if len(snippets) == 3:
                    break
            context["project_snippets"] = snippets
        return context
