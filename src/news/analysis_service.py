"""Reliable, dependency-light news collection and sentiment analysis.

News is intentionally evaluated only after technical screening has produced a
small shortlist.  A failed feed must never block a trading report.
"""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from copy import deepcopy
import re
import logging
from threading import Lock
from time import monotonic, perf_counter
from typing import Any
from urllib.parse import quote_plus
from xml.etree import ElementTree

import requests

from src.news.ai_sentiment import AISentimentAnalyzer, AISentimentError


logger = logging.getLogger(__name__)


class NewsAnalysisService:
    """Fetch news and delegate all semantic interpretation to an AI model."""

    _analyzer: AISentimentAnalyzer | None = None
    _analyzer_lock = Lock()
    _cache_lock = Lock()
    _cache: dict[str, tuple[float, dict[str, Any]]] = {}
    cache_ttl_seconds = 30 * 60

    @classmethod
    def _shared_analyzer(cls) -> AISentimentAnalyzer:
        if cls._analyzer is None:
            with cls._analyzer_lock:
                if cls._analyzer is None:
                    cls._analyzer = AISentimentAnalyzer()
        return cls._analyzer

    @classmethod
    def preload_model(cls) -> dict[str, Any]:
        """Warm the process-wide local models before the targeted-news stage."""
        started = perf_counter()
        try:
            model_load_seconds = cls._shared_analyzer().load_finbert()
            return {"available": True, "model_load_seconds": model_load_seconds,
                    "wall_seconds": perf_counter() - started}
        except AISentimentError as exc:
            logger.warning("FinBERT preload unavailable: %s", exc)
            return {"available": False, "model_load_seconds": 0,
                    "wall_seconds": perf_counter() - started, "error": str(exc)}

    @staticmethod
    def _text(value: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(value or ""))).strip()

    @classmethod
    def analyze(cls, symbol: str, timeout: float = 4.0, limit: int = 8,
                analyzer: AISentimentAnalyzer | None = None) -> dict[str, Any]:
        """Return a bounded sentiment/event assessment for a stock symbol."""
        cache_key = symbol.strip().upper().removesuffix(".NS")
        if analyzer is None:
            with cls._cache_lock:
                cached = cls._cache.get(cache_key)
                if cached and monotonic() - cached[0] < cls.cache_ttl_seconds:
                    return deepcopy(cached[1])
        url = "https://news.google.com/rss/search?q=" + quote_plus(f"{symbol} NSE stock") + "&hl=en-IN&gl=IN&ceid=IN:en"
        network_started = perf_counter()
        try:
            response = requests.get(url, timeout=timeout, headers={"User-Agent": "stock-analyzer/1.0"})
            response.raise_for_status()
            root = ElementTree.fromstring(response.content)
        except (requests.RequestException, ElementTree.ParseError) as exc:
            return {
                "available": False,
                "score": 0,
                "sentiment": "UNAVAILABLE",
                "confidence": 0,
                "article_count": 0,
                "events": [],
                "headlines": [],
                "reasons": [f"News feed unavailable: {exc.__class__.__name__}."],
                "analysis_method": "UNAVAILABLE",
                "score_impact": 0,
                "requested": True, "fetch_failed": True,
                "collection_state": "FETCH_FAILED", "analysis_state": "FETCH_FAILED",
                "news_state": "FETCH_FAILED",
                "timings": {"network_seconds": round(perf_counter() - network_started, 3),
                            "model_load_seconds": 0, "inference_seconds": 0},
            }
        network_seconds = perf_counter() - network_started
        logger.info("News network time for %s: %.3fs", cache_key, network_seconds)

        articles = []
        for item in root.findall("./channel/item")[:limit]:
            title = cls._text(item.findtext("title", ""))
            description = cls._text(item.findtext("description", ""))
            articles.append({
                "title": title,
                "description": description,
                "source": cls._text(item.findtext("source", "Google News")),
                "published": cls._published(item.findtext("pubDate", "")),
                "url": cls._text(item.findtext("link", "")) or None,
            })

        count = len(articles)
        if not articles:
            return {"available": False, "score": 0, "sentiment": "UNAVAILABLE", "confidence": 0,
                    "article_count": 0, "events": [], "headlines": [], "materiality": "NONE",
                    "trade_impact": "NONE", "analysis_method": "UNAVAILABLE", "score_impact": 0,
                    "requested": True, "collection_state": "FETCHED",
                    "analysis_state": "NO_RELEVANT_NEWS", "news_state": "NO_RELEVANT_NEWS",
                    "reasons": ["No relevant news articles were available for analysis."],
                    "timings": {"network_seconds": round(network_seconds, 3),
                                "model_load_seconds": 0, "inference_seconds": 0}}
        ai_analyzer = analyzer or cls._shared_analyzer()
        try:
            assessment = ai_analyzer.analyze(symbol, articles)
        except AISentimentError as exc:
            return {"available": False, "score": 0, "sentiment": "UNAVAILABLE", "confidence": 0,
                    "article_count": count, "events": [], "headlines": articles[:3],
                    "materiality": "NONE", "trade_impact": "NONE",
                    "score_impact": 0,
                    "requested": True, "fetch_failed": True, "collection_state": "FETCHED",
                    "analysis_state": "FETCH_FAILED", "news_state": "FETCH_FAILED",
                    "reasons": [str(exc)], "analysis_method": "AI_UNAVAILABLE",
                    "timings": {"network_seconds": round(network_seconds, 3),
                                "model_load_seconds": 0, "inference_seconds": 0}}
        ai_timings = assessment.get("timings", {})
        result = {
            "available": True,
            "requested": True, "collection_state": "FETCHED",
            "analysis_state": "ANALYZED", "news_state": "ANALYZED",
            "score": round(float(assessment["score"]), 2),
            "sentiment": assessment["sentiment"],
            "confidence": round(float(assessment["confidence"]), 2),
            "article_count": count,
            "events": assessment["events"],
            "headlines": articles[:3],
            "materiality": assessment["materiality"],
            "trade_impact": assessment["trade_impact"],
            "reasons": assessment["reasoning"],
            "article_assessments": assessment["article_assessments"],
            "entities": assessment.get("entities", []),
            "entity_model_available": bool(assessment.get("entity_model_available", False)),
            "analysis_method": assessment.get("analysis_provider", "LOCAL_AI_MODEL"),
            "model": ai_analyzer.model,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "timings": {
                "network_seconds": round(network_seconds, 3),
                "model_load_seconds": ai_timings.get("model_load_seconds", 0),
                "inference_seconds": ai_timings.get("inference_seconds", 0),
            },
        }
        if analyzer is None:
            with cls._cache_lock:
                cls._cache[cache_key] = (monotonic(), deepcopy(result))
        return result

    @staticmethod
    def _published(value: str) -> str | None:
        try:
            return parsedate_to_datetime(value).isoformat()
        except (TypeError, ValueError):
            return None
