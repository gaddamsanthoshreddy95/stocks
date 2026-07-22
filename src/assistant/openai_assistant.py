"""OpenAI Responses API client for grounded stock-analysis explanations."""

from __future__ import annotations

import json
import os
from typing import Any

import requests


class OpenAIConfigurationError(RuntimeError):
    pass


class OpenAIAuthenticationError(RuntimeError):
    pass


class OpenAIAnalyst:
    endpoint = "https://api.openai.com/v1/responses"

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 timeout_seconds: float = 90):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model or os.getenv("OPENAI_ANALYST_MODEL", "gpt-5.6-terra")
        self.timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @staticmethod
    def _output_text(payload: dict[str, Any]) -> str:
        if payload.get("output_text"):
            return str(payload["output_text"])
        pieces = []
        for item in payload.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"} and content.get("text"):
                    pieces.append(str(content["text"]))
        return "\n".join(pieces).strip()

    def answer(self, question: str, context: dict[str, Any],
               history: list[dict[str, str]] | None = None) -> dict[str, Any]:
        if not self.configured:
            raise OpenAIConfigurationError(
                "OPENAI_API_KEY is not configured. Add it to .env and restart the UI.")
        instructions = (
            "You are the Stock Analyzer assistant. Answer from the supplied report, UI, and project "
            "evidence only. The analytical engine is the source of truth. Explain decisions and "
            "probabilities in plain language, cite report IDs and project file paths when present, "
            "distinguish facts from inference, and say when evidence is unavailable. Never claim a "
            "guaranteed return, place an order, reveal credentials, or invent live data. Lead with the answer."
        )
        recent = (history or [])[-8:]
        user_input = (f"Conversation history:\n{json.dumps(recent, default=str)}\n\n"
                      f"Current grounded context:\n{json.dumps(context, default=str)}\n\n"
                      f"User question: {question}")
        response = requests.post(
            self.endpoint,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={"model": self.model, "instructions": instructions, "input": user_input,
                  "text": {"verbosity": "medium"}},
            timeout=self.timeout_seconds,
        )
        if not response.ok:
            try:
                detail = response.json().get("error", {}).get("message")
            except ValueError:
                detail = response.text[:300]
            if response.status_code in {401, 403}:
                raise OpenAIAuthenticationError(
                    "OpenAI authentication was rejected. Sign in again with a valid API key.")
            raise RuntimeError(f"OpenAI request failed ({response.status_code}): {detail or 'unknown error'}")
        payload = response.json()
        text = self._output_text(payload)
        if not text:
            raise RuntimeError("OpenAI returned no assistant message.")
        return {"text": text, "response_id": payload.get("id"), "model": self.model}
