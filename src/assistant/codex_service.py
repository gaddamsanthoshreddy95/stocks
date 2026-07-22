"""Guarded server-side bridge to Codex CLI for project development tasks."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any


class CodexUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class CodexRun:
    mode: str
    output: str
    events: list[dict[str, Any]]
    return_code: int


class CodexService:
    MODES = {"EXPLAIN": "read-only", "PROPOSE": "read-only", "IMPLEMENT": "workspace-write"}

    def __init__(self, repository_root: str | Path, executable: str | None = None):
        self.root = Path(repository_root).resolve()
        configured = executable or os.getenv("CODEX_EXECUTABLE", "codex")
        self.executable = shutil.which(configured)

    @property
    def configured(self) -> bool:
        return bool(self.executable)

    @staticmethod
    def _event_text(event: dict[str, Any]) -> str:
        """Extract user-facing text from both legacy and current Codex JSONL events."""
        for key in ("message", "text"):
            value = event.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "agent_message":
            return ""
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
        content = item.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    parts.append(part["text"])
            return "\n".join(parts).strip()
        return ""

    def run(self, request: str, mode: str = "EXPLAIN", timeout_seconds: int = 600,
            confirmed: bool = False) -> CodexRun:
        mode = str(mode).upper()
        if mode not in self.MODES:
            raise ValueError("Codex mode must be EXPLAIN, PROPOSE, or IMPLEMENT.")
        if mode == "IMPLEMENT" and not confirmed:
            raise PermissionError("IMPLEMENT mode requires explicit confirmation.")
        if not self.configured:
            raise CodexUnavailableError(
                "Codex CLI is not installed or authenticated. Install Codex, sign in, and restart the UI.")
        policy = (
            "Work only inside this repository. Never read or reveal .env, credentials, tokens, .git, "
            "cache data, or broker secrets. Never place trades, push commits, deploy, install dependencies, "
            "or use network access. "
        )
        if mode == "EXPLAIN":
            policy += "Inspect and explain only; do not edit files."
        elif mode == "PROPOSE":
            policy += "Inspect and propose an exact patch plan only; do not edit files."
        else:
            policy += ("Implement only the requested scoped change, preserve unrelated work, run relevant "
                       "existing tests, and finish with modified files plus test results. Do not commit.")
        command = [self.executable, "exec", "--json", "--sandbox", self.MODES[mode],
                   "-C", str(self.root), f"{policy}\n\nUser request:\n{request}"]
        try:
            completed = subprocess.run(command, capture_output=True, text=True,
                                       timeout=max(30, min(int(timeout_seconds), 1800)), check=False)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Codex exceeded the configured time limit and was stopped.") from exc
        events, messages = [], []
        for line in completed.stdout.splitlines():
            try:
                event = json.loads(line)
                events.append(event)
                message = self._event_text(event)
                if message:
                    messages.append(message)
            except json.JSONDecodeError:
                if line.strip():
                    messages.append(line.strip())
        output = "\n".join(messages).strip() or completed.stderr.strip()
        return CodexRun(mode, output or "Codex completed without textual output.",
                        events, completed.returncode)

    def answer(self, question: str, context: dict[str, Any],
               history: list[dict[str, str]] | None = None) -> dict[str, Any]:
        """Use an authenticated Codex CLI session as a read-only grounded analyst."""
        prompt = (
            "Answer as the Alphatrace assistant, not as a coding agent. Use only the supplied "
            "grounded UI/report context. Explain probabilities in plain language, distinguish facts "
            "from inference, say when evidence is unavailable, and never promise returns or place trades.\n\n"
            f"Recent conversation:\n{json.dumps((history or [])[-8:], default=str)}\n\n"
            f"Grounded context:\n{json.dumps(context, default=str)}\n\nQuestion:\n{question}"
        )
        result = self.run(prompt, "EXPLAIN", timeout_seconds=600)
        if result.return_code != 0:
            raise RuntimeError(result.output)
        return {"text": result.output, "model": "Codex via ChatGPT sign-in", "response_id": None}
