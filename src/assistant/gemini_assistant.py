"""Google Gemini client for grounded stock-analysis conversations."""

from __future__ import annotations

import json
import os
from typing import Any

import requests


class GeminiConfigurationError(RuntimeError):
    pass


class GeminiAuthenticationError(RuntimeError):
    pass


class GeminiAnalyst:
    endpoint_root = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 timeout_seconds: float = 90):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self.model = model or os.getenv("GEMINI_ANALYST_MODEL", "gemini-2.5-flash-lite")
        self.timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @staticmethod
    def _output_text(payload: dict[str, Any]) -> str:
        candidates = payload.get("candidates") or []
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        return "\n".join(str(part["text"]) for part in parts if part.get("text")).strip()

    def answer(self, question: str, context: dict[str, Any],
               history: list[dict[str, str]] | None = None) -> dict[str, Any]:
        if not self.configured:
            raise GeminiConfigurationError(
                "GEMINI_API_KEY is not configured. Add it as an Azure secret or sign in for this session.")

        system_instruction = (
            "You are the Stock Analyzer assistant. Answer only from the supplied report, UI, and "
            "project evidence. The analytical engine is the source of truth. Explain decisions and "
            "probabilities in plain language, cite report IDs and project file paths when present, "
            "distinguish facts from inference, and say when evidence is unavailable. Never claim a "
            "guaranteed return, place an order, reveal credentials, or invent live data. Lead with the answer."
        )
        contents = []
        for message in (history or [])[-8:]:
            role = "model" if message.get("role") == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": str(message.get("content", ""))}]})
        contents.append({
            "role": "user",
            "parts": [{"text": (
                f"Current grounded context:\n{json.dumps(context, default=str)}\n\n"
                f"User question: {question}"
            )}],
        })
        try:
            response = requests.post(
                f"{self.endpoint_root}/{self.model}:generateContent",
                headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                json={
                    "system_instruction": {"parts": [{"text": system_instruction}]},
                    "contents": contents,
                    "generationConfig": {"temperature": 0.2, "maxOutputTokens": 2048},
                },
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise RuntimeError("Gemini could not be reached. Check the server network connection.") from exc

        if not response.ok:
            try:
                error = response.json().get("error", {})
                detail = error.get("message") or response.text[:300]
                status = str(error.get("status", ""))
            except ValueError:
                detail, status = response.text[:300], ""
            if response.status_code in {401, 403} or "API_KEY" in status or "API key" in detail:
                raise GeminiAuthenticationError(
                    "Gemini authentication was rejected. Enter a valid Google AI Studio API key.")
            raise RuntimeError(
                f"Gemini request failed ({response.status_code}): {detail or 'unknown error'}")

        payload = response.json()
        text = self._output_text(payload)
        if not text:
            block_reason = payload.get("promptFeedback", {}).get("blockReason")
            if block_reason:
                raise RuntimeError(f"Gemini blocked this request: {block_reason}")
            raise RuntimeError("Gemini returned no assistant message.")
        return {"text": text, "model": self.model, "response_id": None}
