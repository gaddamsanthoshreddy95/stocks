"""Google Gemini client for grounded stock-analysis conversations."""

from __future__ import annotations

import json
import os
import re
import time
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
        self.model = model or os.getenv("GEMINI_ANALYST_MODEL", "gemini-3.5-flash")
        self.fallback_model = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.1-flash-lite")
        self.web_search = os.getenv("GEMINI_WEB_SEARCH", "always").strip().lower()
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

    @staticmethod
    def _needs_current_search(question: str) -> bool:
        words = set(re.findall(r"[a-z]+", str(question).lower()))
        return bool(words.intersection({
            "news", "today", "latest", "current", "recent", "headline", "headlines",
            "announcement", "announcements", "breaking", "happened", "update", "updates",
        }))

    @staticmethod
    def _grounding_sources(payload: dict[str, Any]) -> list[dict[str, str]]:
        candidates = payload.get("candidates") or []
        if not candidates:
            return []
        chunks = candidates[0].get("groundingMetadata", {}).get("groundingChunks", [])
        sources, seen = [], set()
        for chunk in chunks:
            web = chunk.get("web", {}) if isinstance(chunk, dict) else {}
            uri, title = str(web.get("uri") or ""), str(web.get("title") or "Source")
            if uri.startswith(("https://", "http://")) and uri not in seen:
                seen.add(uri)
                sources.append({"title": title, "url": uri})
            if len(sources) == 5:
                break
        return sources

    def answer(self, question: str, context: dict[str, Any],
               history: list[dict[str, str]] | None = None) -> dict[str, Any]:
        if not self.configured:
            raise GeminiConfigurationError(
                "GEMINI_API_KEY is not configured. Add it as an Azure secret or sign in for this session.")

        system_instruction = (
            "You are the Alphatrace assistant. Answer only from the supplied report, UI, and "
            "project evidence. The analytical engine is the source of truth. Explain decisions and "
            "probabilities in plain language, cite report IDs and project file paths when present, "
            "distinguish facts from inference, and say when evidence is unavailable. Never claim a "
            "guaranteed return, place an order, reveal credentials, or invent live data. For current-news "
            "questions only, Google Search results supplied by the search tool are valid additional evidence; "
            "clearly separate them from saved-report facts and cite their sources. Lead with the answer."
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
        request_body = {
            "system_instruction": {"parts": [{"text": system_instruction}]},
            "contents": contents,
            "generationConfig": {"maxOutputTokens": 2048},
        }
        search_enabled = self.web_search != "off"
        search_requested = self.web_search == "always" or self._needs_current_search(question)
        if search_enabled and search_requested:
            request_body["tools"] = [{"google_search": {}}]
        models = [self.model]
        if self.fallback_model and self.fallback_model != self.model:
            models.append(self.fallback_model)
        response = None
        used_model = self.model
        last_detail = "temporary capacity error"
        for model_index, candidate_model in enumerate(models):
            for attempt in range(2):
                try:
                    response = requests.post(
                        f"{self.endpoint_root}/{candidate_model}:generateContent",
                        headers={"x-goog-api-key": self.api_key,
                                 "Content-Type": "application/json"},
                        json=request_body,
                        timeout=self.timeout_seconds,
                    )
                except requests.RequestException as exc:
                    if attempt == 0:
                        time.sleep(1)
                        continue
                    if model_index < len(models) - 1:
                        break
                    raise RuntimeError(
                        "Gemini could not be reached. Check the server network connection.") from exc
                if response.ok:
                    used_model = candidate_model
                    break
                try:
                    error = response.json().get("error", {})
                    last_detail = error.get("message") or response.text[:300]
                    status = str(error.get("status", ""))
                except ValueError:
                    last_detail, status = response.text[:300], ""
                if (response.status_code in {401, 403} or "API_KEY" in status
                        or "API key" in last_detail):
                    raise GeminiAuthenticationError(
                        "Gemini authentication was rejected. Enter a valid Google AI Studio API key.")
                if response.status_code not in {429, 500, 502, 503, 504}:
                    raise RuntimeError(
                        f"Gemini request failed ({response.status_code}): "
                        f"{last_detail or 'unknown error'}")
                if attempt == 0:
                    retry_after = response.headers.get("Retry-After", "1")
                    try:
                        delay = min(max(float(retry_after), 0.5), 3.0)
                    except ValueError:
                        delay = 1.0
                    time.sleep(delay)
            if response is not None and response.ok:
                break

        if response is None or not response.ok:
            status_code = response.status_code if response is not None else 503
            raise RuntimeError(
                f"Gemini request failed ({status_code}) after retry and fallback: {last_detail}")

        payload = response.json()
        text = self._output_text(payload)
        if not text:
            block_reason = payload.get("promptFeedback", {}).get("blockReason")
            if block_reason:
                raise RuntimeError(f"Gemini blocked this request: {block_reason}")
            raise RuntimeError("Gemini returned no assistant message.")
        sources = self._grounding_sources(payload) if search_requested else []
        if sources:
            links = "\n".join(f"- [{source['title']}]({source['url']})" for source in sources)
            text = f"{text}\n\nSources:\n{links}"
        return {"text": text, "model": used_model, "response_id": None,
                "search_enabled": search_enabled,
                "search_grounded": bool(sources), "sources": sources}
