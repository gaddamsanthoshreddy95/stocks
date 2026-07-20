from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any


class EventRepository:
    SCHEMA_VERSION = 1

    def __init__(self, root: str | Path = "data/cache/events"):
        self.root = Path(root)
        self.events_path = self.root / "events.json"
        self.override_path = self.root / "manual_overrides.json"
        self.commodity_path = self.root / "commodity_snapshot.json"
        self.company_calendar_path = self.root / "company_calendar.json"
        self.economic_calendar_path = self.root / "economic_calendar.json"

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text())
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def read_events(self) -> list[dict[str, Any]]:
        return self._read(self.events_path).get("events", [])

    def read_overrides(self, as_of: datetime) -> list[dict[str, Any]]:
        rows = self._read(self.override_path).get("events", [])
        active = []
        for row in rows:
            if not row.get("enabled", True):
                continue
            expiry = row.get("expiry_time")
            try:
                if expiry and datetime.fromisoformat(expiry.replace("Z", "+00:00")) <= as_of:
                    continue
            except ValueError:
                continue
            active.append(row)
        return active

    def read_commodity_snapshot(self) -> dict[str, Any]:
        return self._read(self.commodity_path)

    def read_company_calendar(self) -> list[dict[str, Any]]:
        return self._read(self.company_calendar_path).get("events", [])

    def read_economic_calendar(self) -> list[dict[str, Any]]:
        return self._read(self.economic_calendar_path).get("events", [])

    def write_events(self, events: list[dict[str, Any]], generated_at: datetime) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": self.SCHEMA_VERSION,
                   "generated_at": generated_at.isoformat(), "events": events}
        descriptor, temporary = tempfile.mkstemp(prefix="events-", suffix=".json", dir=self.root)
        try:
            with os.fdopen(descriptor, "w") as handle:
                json.dump(payload, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.events_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
