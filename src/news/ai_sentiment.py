"""Free, local financial sentiment using FinBERT and spaCy."""

from __future__ import annotations

import os
import importlib.util
from functools import lru_cache
import logging
from pathlib import Path
from statistics import mean
from threading import RLock
from time import perf_counter
from typing import Any


logger = logging.getLogger(__name__)


class _EntityFreeNLP:
    """Keep FinBERT sentiment available when optional spaCy NER is absent."""

    @staticmethod
    def _document():
        return type("EntityFreeDocument", (), {"ents": ()})()

    def __call__(self, _text):
        return self._document()

    def pipe(self, texts, **_kwargs):
        return (self._document() for _ in texts)


def _cached_safetensors_snapshots(model_name: str) -> list[Path]:
    """Return local snapshots with safe weights without contacting the Hub."""
    cache_root = Path(os.getenv("HF_HUB_CACHE", Path(os.getenv("HF_HOME", Path.home() / ".cache/huggingface")) / "hub"))
    repository = "models--" + model_name.replace("/", "--")
    snapshots = cache_root / repository / "snapshots"
    return sorted(
        {path.parent for path in snapshots.glob("*/model.safetensors")},
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    ) if snapshots.exists() else []


@lru_cache(maxsize=1)
def get_finbert_pipeline(model_name: str):
    """Build exactly one local-first FinBERT pipeline per Python process."""
    cache_root = Path(os.getenv("HF_HUB_CACHE", Path(os.getenv("HF_HOME", Path.home() / ".cache/huggingface")) / "hub"))
    repository_path = cache_root / ("models--" + model_name.replace("/", "--"))
    if repository_path.exists():
        # Set offline flags before importing transformers/huggingface_hub.
        # This is a second guard behind local_files_only=True and prevents
        # metadata, commit, discussion, or conversion-service requests.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    from transformers import (
        AutoConfig, AutoModelForSequenceClassification, AutoTokenizer, pipeline,
    )

    if not repository_path.exists():
        # Only a genuinely absent cache may use the network. Once the cache
        # exists every subsequent process takes the strict local path below.
        return pipeline("text-classification", model=model_name, tokenizer=model_name)

    tokenizer = AutoTokenizer.from_pretrained(model_name, local_files_only=True)
    config = AutoConfig.from_pretrained(model_name, local_files_only=True)
    try:
        model = AutoModelForSequenceClassification.from_pretrained(
            model_name, config=config, local_files_only=True
        )
    except (OSError, ValueError):
        # Some transformers versions reject legacy pytorch_model.bin files.
        # A safetensors conversion may already exist in another cached revision;
        # resolve it directly from disk instead of querying Hub commits/PRs.
        snapshots = _cached_safetensors_snapshots(model_name)
        if not snapshots:
            raise
        model = AutoModelForSequenceClassification.from_pretrained(
            snapshots[0], config=config, local_files_only=True
        )
    return pipeline("text-classification", model=model, tokenizer=tokenizer)


class AISentimentError(RuntimeError):
    """Raised when local NLP models cannot produce a trustworthy result."""


class AISentimentAnalyzer:
    """Analyze financial text locally; no paid API or sentiment lexicon."""

    def __init__(self, model: str | None = None, spacy_model: str | None = None,
                 sentiment_pipeline=None, nlp=None):
        self.model = model or os.getenv("NEWS_FINBERT_MODEL", "ProsusAI/finbert")
        self.spacy_model = spacy_model or os.getenv("NEWS_SPACY_MODEL", "en_core_web_sm")
        self._sentiment_pipeline = sentiment_pipeline
        self._nlp = nlp
        self._entity_model_available = nlp is not None
        self._model_lock = RLock()

    def load_finbert(self) -> float:
        """Load the process-cached FinBERT pipeline without initializing spaCy."""
        started = perf_counter()
        loaded = False
        with self._model_lock:
            if self._sentiment_pipeline is None:
                try:
                    self._sentiment_pipeline = get_finbert_pipeline(self.model)
                    loaded = True
                except (ImportError, OSError, RuntimeError) as exc:
                    raise AISentimentError(
                        "FinBERT is not fully cached locally. Download NEWS_FINBERT_MODEL once before running the report."
                    ) from exc
        elapsed = perf_counter() - started if loaded else 0.0
        if loaded:
            logger.info("FinBERT model load time: %.3fs (local cache)", elapsed)
        return elapsed

    def dependency_health(self) -> dict[str, Any]:
        """Report entity-model readiness without triggering model downloads."""
        spacy_installed = importlib.util.find_spec("spacy") is not None
        entity_model_installed = importlib.util.find_spec(self.spacy_model) is not None
        return {
            "spacy": "AVAILABLE" if spacy_installed else "MISSING",
            "entity_model": self.spacy_model,
            "entity_model_status": "AVAILABLE" if entity_model_installed else "MISSING",
            "entity_dependent_classification": "ENABLED" if entity_model_installed else "DISABLED_SAFE",
        }

    def _load(self) -> float:
        model_load_seconds = self.load_finbert()
        with self._model_lock:
            if self._nlp is None:
                try:
                    import spacy
                    self._nlp = spacy.load(self.spacy_model)
                    self._entity_model_available = True
                except (ImportError, OSError) as exc:
                    logger.warning(
                        "spaCy model %s unavailable; continuing with FinBERT sentiment and no entities: %s",
                        self.spacy_model, exc,
                    )
                    self._nlp = _EntityFreeNLP()
                    self._entity_model_available = False
        return model_load_seconds

    @staticmethod
    def _probabilities(raw_result) -> dict[str, float]:
        rows = raw_result
        while isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], list):
            rows = rows[0]
        if not isinstance(rows, list):
            raise AISentimentError("FinBERT returned an invalid classification payload")
        probabilities = {str(row["label"]).lower(): float(row["score"]) for row in rows}
        if not {"positive", "negative", "neutral"}.issubset(probabilities):
            raise AISentimentError("FinBERT response is missing sentiment probabilities")
        return probabilities

    @staticmethod
    def _label(score: float) -> str:
        return "BULLISH" if score >= 15 else "BEARISH" if score <= -15 else "NEUTRAL"

    def analyze(self, symbol: str, articles: list[dict[str, Any]]) -> dict[str, Any]:
        model_load_seconds = self._load()
        assessments, signed_scores, confidences, all_entities = [], [], [], []
        usable = []
        for article in articles:
            article_text = " ".join(filter(None, (article.get("title"), article.get("description"))))
            if article_text.strip():
                usable.append((article, article_text))
        # The shared transformers/spaCy objects are reused across requests and
        # guarded because concurrent model inference is not reliably thread-safe.
        with self._model_lock:
            inference_started = perf_counter()
            texts = [text for _, text in usable]
            if not texts:
                raw_results, documents = [], []
            else:
                raw_results = self._sentiment_pipeline(
                    texts, top_k=None, truncation=True, batch_size=min(16, len(texts))
                )
                # A few lightweight test/custom pipelines only support scalar
                # calls; retain that compatibility without penalizing the real
                # transformers batch path.
                if len(texts) == 1 and raw_results and isinstance(raw_results[0], dict):
                    raw_results = [raw_results]
                pipe = getattr(self._nlp, "pipe", None)
                if callable(pipe):
                    try:
                        documents = list(pipe(texts, batch_size=min(32, len(texts))))
                    except (TypeError, AttributeError):
                        documents = [self._nlp(text) for text in texts]
                else:
                    documents = [self._nlp(text) for text in texts]
                if len(documents) != len(texts):
                    documents = [self._nlp(text) for text in texts]
            for (article, text), raw_result, doc in zip(usable, raw_results, documents):
                probabilities = self._probabilities(raw_result)
                signed_score = (probabilities["positive"] - probabilities["negative"]) * 100
                label = self._label(signed_score)
                confidence = max(probabilities.values()) * 100
                entities = [
                    {"text": entity.text, "label": entity.label_}
                    for entity in doc.ents if entity.text.strip()
                ]
                all_entities.extend(entities)
                materiality = "HIGH" if abs(signed_score) >= 60 and confidence >= 70 else "MEDIUM" if abs(signed_score) >= 30 else "LOW"
                assessments.append({
                    "title": str(article.get("title", "")), "sentiment": label,
                    "materiality": materiality,
                    "summary": f"FinBERT {label.lower()} probability assessment; {len(entities)} entities extracted.",
                    "probabilities": {key: round(value * 100, 2) for key, value in probabilities.items()},
                    "entities": entities,
                })
                signed_scores.append(signed_score)
                confidences.append(confidence)
            inference_seconds = perf_counter() - inference_started
        logger.info("FinBERT inference time: %.3fs for %d articles", inference_seconds, len(usable))
        if not assessments:
            raise AISentimentError("No usable article text was available for local AI analysis")

        score = mean(signed_scores)
        confidence = mean(confidences)
        sentiment = self._label(score)
        materiality = "HIGH" if abs(score) >= 60 and confidence >= 70 else "MEDIUM" if abs(score) >= 30 else "LOW"
        trade_impact = (
            "BLOCK" if sentiment == "BEARISH" and materiality == "HIGH"
            else "CAUTION" if sentiment == "BEARISH"
            else "SUPPORTIVE" if sentiment == "BULLISH"
            else "NONE"
        )
        return {
            "sentiment": sentiment, "score": round(score, 2),
            "confidence": round(confidence, 2), "materiality": materiality,
            "events": [],
            "reasoning": [
                f"FinBERT analyzed {len(assessments)} financial news items locally.",
                f"Average signed sentiment score is {score:.2f}; article confidence averages {confidence:.2f}%.",
                f"spaCy extracted {len(all_entities)} named entities for report context.",
            ],
            "trade_impact": trade_impact, "article_assessments": assessments,
            "entities": all_entities,
            "entity_model_available": self._entity_model_available,
            "analysis_provider": ("LOCAL_FINBERT_SPACY" if self._entity_model_available
                                  else "LOCAL_FINBERT"),
            "timings": {
                "model_load_seconds": round(model_load_seconds, 3),
                "inference_seconds": round(inference_seconds, 6),
            },
        }
