"""Local Ollama client for grounded, no-cloud-cost analyst conversations."""

from __future__ import annotations

import json
import os
from typing import Any

import requests


class OllamaConfigurationError(RuntimeError):
    pass


class OllamaAnalyst:
    def __init__(self, model: str | None = None, base_url: str | None = None,
                 timeout_seconds: float = 180):
        self.model = model or os.getenv("OLLAMA_ANALYST_MODEL", "gpt-oss:20b")
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")).rstrip("/")
        self.timeout_seconds = timeout_seconds

    def answer(self, question: str, context: dict[str, Any],
               history: list[dict[str, str]] | None = None) -> dict[str, Any]:
        system = (
            "You are the Alphatrace assistant. Use only the supplied report, UI, and project "
            "evidence. Explain decisions and probabilities plainly, distinguish facts from inference, "
            "and say when evidence is unavailable. Never promise returns or place trades."
        )
        messages = [{"role": "system", "content": system}]
        messages.extend((history or [])[-8:])
        messages.append({"role": "user", "content":
                         f"Grounded context:\n{json.dumps(context, default=str)}\n\nQuestion: {question}"})
        try:
            response = requests.post(f"{self.base_url}/api/chat", json={
                "model": self.model, "messages": messages, "stream": False,
            }, timeout=self.timeout_seconds)
        except requests.RequestException as exc:
            raise OllamaConfigurationError(
                "Ollama is not reachable. Start Ollama and download the configured model.") from exc
        if not response.ok:
            raise RuntimeError(f"Ollama request failed ({response.status_code}): {response.text[:300]}")
        text = str(response.json().get("message", {}).get("content", "")).strip()
        if not text:
            raise RuntimeError("Ollama returned no assistant message.")
        return {"text": text, "model": self.model, "response_id": None}
