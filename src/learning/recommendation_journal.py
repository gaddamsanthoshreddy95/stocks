"""Append-only JSONL journal for every generated recommendation."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import Lock
from typing import Any


class RecommendationJournal:
    _lock = Lock()

    def __init__(self, root: str | Path = "data/recommendations"):
        self.root = Path(root)

    def append(self, run_id: str, recommendation: dict[str, Any]) -> Path:
        timestamp = datetime.now(timezone.utc)
        path = self.root / f"{timestamp:%Y}" / f"{timestamp:%m}" / f"{timestamp:%d}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "run_id": run_id,
            "timestamp": timestamp.isoformat(),
            "symbol": recommendation.get("symbol"),
            "setup": recommendation.get("setup"),
            "scores": {key: recommendation.get(key) for key in
                       ("technical_score", "quality_score", "ai_score", "execution_readiness_score")},
            "event_risk": recommendation.get("event_risk"),
            "news_state": (recommendation.get("news") or {}).get("news_state"),
            "entry_confirmation": recommendation.get("entry_confirmation"),
            "entry": (recommendation.get("levels") or {}).get("entry"),
            "stop": (recommendation.get("levels") or {}).get("stop_loss"),
            "targets": (recommendation.get("levels") or {}).get("targets"),
            "final_action": recommendation.get("final_action"),
            "option_status": recommendation.get("option_trade_approval"),
            "position": recommendation.get("risk"),
            "reasons": recommendation.get("ai_reasoning", []),
            "rejection_reasons": (recommendation.get("trade_eligibility") or {}).get("blocking_reasons", []),
        }
        payload = (json.dumps(record, sort_keys=True, separators=(",", ":"), default=str) + "\n").encode()
        with self._lock:
            descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                os.write(descriptor, payload)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return path

    def recent_selected_symbols(self, limit: int = 3) -> list[set[str]]:
        """Return selected symbols grouped by the most recent completed runs."""
        if not self.root.exists() or limit <= 0:
            return []
        records = []
        for path in sorted(self.root.glob("*/*/*.jsonl"), reverse=True):
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    row = json.loads(line)
                    if row.get("run_id") and row.get("symbol"):
                        records.append(row)
            except (OSError, json.JSONDecodeError):
                continue
        records.sort(key=lambda row: str(row.get("timestamp", "")), reverse=True)
        grouped: dict[str, set[str]] = {}
        for row in records:
            run_id = str(row["run_id"])
            if run_id not in grouped and len(grouped) >= limit:
                continue
            action = str(row.get("final_action") or "").upper()
            if action not in {"REJECT", "NO_TRADE"}:
                grouped.setdefault(run_id, set()).add(str(row["symbol"]).upper())
            else:
                grouped.setdefault(run_id, set())
        return list(grouped.values())[:limit]
