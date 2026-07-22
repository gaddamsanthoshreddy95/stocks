"""Reliable, dependency-light news collection and sentiment analysis.

News is intentionally evaluated only after technical screening has produced a
small shortlist.  A failed feed must never block a trading report.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from copy import deepcopy
import re
import logging
import os
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
    cache_ttl_seconds = 5 * 60

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
        max_age_hours = max(1, int(os.getenv("NEWS_MAX_AGE_HOURS", "72")))
        if analyzer is None:
            with cls._cache_lock:
                cached = cls._cache.get(cache_key)
                if cached and monotonic() - cached[0] < cls.cache_ttl_seconds:
                    return deepcopy(cached[1])
        search = quote_plus(f"{symbol} NSE stock when:{max_age_hours}h")
        url = "https://news.google.com/rss/search?q=" + search + "&hl=en-IN&gl=IN&ceid=IN:en"
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

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=max_age_hours)
        articles = []
        stale_article_count = 0
        seen: set[tuple[str, str]] = set()
        for item in root.findall("./channel/item"):
            title = cls._text(item.findtext("title", ""))
            description = cls._text(item.findtext("description", ""))
            source = cls._text(item.findtext("source", "Google News"))
            published_at = cls._published_datetime(item.findtext("pubDate", ""))
            if published_at is None or published_at < cutoff or published_at > now + timedelta(minutes=5):
                stale_article_count += 1
                continue
            identity = (title.casefold(), source.casefold())
            if not title or identity in seen:
                continue
            seen.add(identity)
            articles.append({
                "title": title,
                "description": description,
                "source": source,
                "published": published_at.isoformat(),
                "url": cls._text(item.findtext("link", "")) or None,
            })
        articles.sort(key=lambda article: article["published"], reverse=True)
        articles = articles[:limit]

        count = len(articles)
        if not articles:
            return {"available": False, "score": 0, "sentiment": "UNAVAILABLE", "confidence": 0,
                    "article_count": 0, "events": [], "headlines": [], "materiality": "NONE",
                    "trade_impact": "NONE", "analysis_method": "UNAVAILABLE", "score_impact": 0,
                    "requested": True, "collection_state": "FETCHED",
                    "analysis_state": "NO_RELEVANT_NEWS", "news_state": "NO_RELEVANT_NEWS",
                    "reasons": [
                        f"No timestamped news newer than {max_age_hours} hours was available."
                    ],
                    "max_age_hours": max_age_hours,
                    "stale_article_count": stale_article_count,
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
            "max_age_hours": max_age_hours,
            "stale_article_count": stale_article_count,
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
        published = NewsAnalysisService._published_datetime(value)
        return published.isoformat() if published else None

    @staticmethod
    def _published_datetime(value: str) -> datetime | None:
        try:
            published = parsedate_to_datetime(value)
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            return published.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None
