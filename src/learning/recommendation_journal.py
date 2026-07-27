"""Append-only JSONL journal for every generated recommendation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
        return [
            snapshot["symbols"]
            for snapshot in self.recent_selected_snapshots(limit, minimum_gap_minutes=0)
        ]

    def recent_selected_snapshots(
        self,
        limit: int = 3,
        minimum_gap_minutes: int = 15,
        reference_time: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Return independent prior observations, ignoring immediate reruns.

        A run is eligible only after the minimum gap from ``reference_time``.
        Older runs are then sampled with the same minimum separation, so
        repeated clicks cannot manufacture persistence.
        """
        if not self.root.exists() or limit <= 0:
            return []
        reference = reference_time or datetime.now(timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        cutoff = reference - timedelta(minutes=max(0, minimum_gap_minutes))
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
        grouped: dict[str, dict[str, Any]] = {}
        for row in records:
            run_id = str(row["run_id"])
            try:
                timestamp = datetime.fromisoformat(str(row.get("timestamp", "")))
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
            if timestamp > cutoff:
                continue
            grouped.setdefault(run_id, {"timestamp": timestamp, "symbols": set()})
            grouped[run_id]["timestamp"] = max(grouped[run_id]["timestamp"], timestamp)
            action = str(row.get("final_action") or "").upper()
            if action not in {"REJECT", "NO_TRADE"}:
                grouped[run_id]["symbols"].add(str(row["symbol"]).upper())
        ordered = sorted(grouped.items(), key=lambda item: item[1]["timestamp"], reverse=True)
        selected = []
        previous_time = reference
        gap = timedelta(minutes=max(0, minimum_gap_minutes))
        for run_id, snapshot in ordered:
            if previous_time - snapshot["timestamp"] < gap:
                continue
            selected.append({
                "run_id": run_id,
                "timestamp": snapshot["timestamp"].isoformat(),
                "symbols": snapshot["symbols"],
            })
            previous_time = snapshot["timestamp"]
            if len(selected) >= limit:
                break
        return selected
