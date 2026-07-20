"""Regression coverage for the explainable news-analysis integration."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.news.analysis_service import NewsAnalysisService
from src.news.ai_sentiment import AISentimentAnalyzer, AISentimentError, get_finbert_pipeline


RSS = b"""<rss><channel>
<item><title>SBIN profit growth beats estimates</title><description>Strong expansion</description><source>Example</source><pubDate>Mon, 20 Jul 2026 10:00:00 GMT</pubDate></item>
<item><title>SBIN wins large contract</title><description>Record order</description><source>Example</source><pubDate>Mon, 20 Jul 2026 09:00:00 GMT</pubDate></item>
</channel></rss>"""


class NewsAnalysisServiceTests(unittest.TestCase):
    def test_finbert_pipeline_has_single_entry_process_cache(self):
        self.assertEqual(get_finbert_pipeline.cache_parameters()["maxsize"], 1)

    def tearDown(self):
        NewsAnalysisService._cache.clear()

    @patch("src.news.analysis_service.requests.get")
    def test_ai_bullish_assessment_is_used_without_keyword_scoring(self, get):
        response = Mock(content=RSS)
        response.raise_for_status.return_value = None
        get.return_value = response

        analyzer = Mock(model="test-model")
        analyzer.analyze.return_value = {
            "sentiment": "BULLISH", "score": 72, "confidence": 91,
            "materiality": "HIGH", "events": ["EARNINGS_BEAT"],
            "reasoning": ["Results exceeded stated expectations."], "trade_impact": "SUPPORTIVE",
            "article_assessments": [], "analysis_provider": "LOCAL_FINBERT_SPACY",
        }
        result = NewsAnalysisService.analyze("SBIN", analyzer=analyzer)

        self.assertTrue(result["available"])
        self.assertEqual(result["sentiment"], "BULLISH")
        self.assertGreater(result["score"], 0)
        self.assertEqual(result["article_count"], 2)
        self.assertEqual(len(result["headlines"]), 2)
        self.assertEqual(result["analysis_method"], "LOCAL_FINBERT_SPACY")
        self.assertIn("network_seconds", result["timings"])
        self.assertIn("inference_seconds", result["timings"])
        analyzer.analyze.assert_called_once()

    @patch("src.news.analysis_service.requests.get")
    def test_ai_failure_is_unavailable_not_keyword_fallback(self, get):
        response = Mock(content=RSS)
        response.raise_for_status.return_value = None
        get.return_value = response
        analyzer = Mock(model="test-model")
        analyzer.analyze.side_effect = AISentimentError("provider unavailable")

        result = NewsAnalysisService.analyze("SBIN", analyzer=analyzer)

        self.assertFalse(result["available"])
        self.assertEqual(result["sentiment"], "UNAVAILABLE")
        self.assertEqual(result["news_state"], "FETCH_FAILED")
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["analysis_method"], "AI_UNAVAILABLE")

    def test_local_finbert_and_spacy_analyze_without_paid_api(self):
        sentiment_pipeline = Mock(return_value=[
            {"label": "positive", "score": 0.82},
            {"label": "negative", "score": 0.05},
            {"label": "neutral", "score": 0.13},
        ])
        nlp = Mock(return_value=SimpleNamespace(ents=[
            SimpleNamespace(text="State Bank of India", label_="ORG"),
        ]))
        analyzer = AISentimentAnalyzer(
            model="test-finbert",
            spacy_model="test-spacy",
            sentiment_pipeline=sentiment_pipeline,
            nlp=nlp,
        )

        result = analyzer.analyze("SBIN", [{
            "title": "State Bank of India reports results",
            "description": "Quarterly performance exceeded expectations.",
        }])

        self.assertEqual(result["sentiment"], "BULLISH")
        self.assertEqual(result["analysis_provider"], "LOCAL_FINBERT_SPACY")
        self.assertEqual(result["entities"][0]["text"], "State Bank of India")
        sentiment_pipeline.assert_called_once_with(
            ["State Bank of India reports results Quarterly performance exceeded expectations."],
            top_k=None,
            truncation=True,
            batch_size=1,
        )
        nlp.assert_called_once()

    def test_finbert_batches_multiple_articles(self):
        sentiment_pipeline = Mock(return_value=[
            [{"label": "positive", "score": .8}, {"label": "negative", "score": .1},
             {"label": "neutral", "score": .1}],
            [{"label": "positive", "score": .2}, {"label": "negative", "score": .2},
             {"label": "neutral", "score": .6}],
        ])
        documents = [SimpleNamespace(ents=[]), SimpleNamespace(ents=[])]
        nlp = Mock()
        nlp.pipe.return_value = documents
        analyzer = AISentimentAnalyzer(sentiment_pipeline=sentiment_pipeline, nlp=nlp)

        result = analyzer.analyze("SBIN", [
            {"title": "First", "description": "article"},
            {"title": "Second", "description": "article"},
        ])

        self.assertEqual(len(result["article_assessments"]), 2)
        sentiment_pipeline.assert_called_once_with(
            ["First article", "Second article"], top_k=None, truncation=True, batch_size=2
        )
        nlp.pipe.assert_called_once_with(["First article", "Second article"], batch_size=2)

    @patch("spacy.load", side_effect=OSError("model missing"))
    def test_missing_spacy_does_not_disable_finbert_sentiment(self, _load):
        sentiment_pipeline = Mock(return_value=[
            {"label": "positive", "score": .8},
            {"label": "negative", "score": .1},
            {"label": "neutral", "score": .1},
        ])
        analyzer = AISentimentAnalyzer(sentiment_pipeline=sentiment_pipeline, nlp=None)
        result = analyzer.analyze("SBIN", [{"title": "Results", "description": "Update"}])
        self.assertEqual(result["sentiment"], "BULLISH")
        self.assertEqual(result["analysis_provider"], "LOCAL_FINBERT")
        self.assertEqual(result["entities"], [])

    @patch("src.news.analysis_service.requests.get")
    def test_default_news_analysis_is_cached_across_requests(self, get):
        response = Mock(content=RSS)
        response.raise_for_status.return_value = None
        get.return_value = response
        analyzer = Mock(model="shared-model")
        analyzer.analyze.return_value = {
            "sentiment": "NEUTRAL", "score": 0, "confidence": 80,
            "materiality": "LOW", "events": [], "reasoning": ["Neutral."],
            "trade_impact": "NONE", "article_assessments": [],
            "analysis_provider": "LOCAL_FINBERT_SPACY",
        }
        NewsAnalysisService._cache.clear()

        with patch.object(NewsAnalysisService, "_shared_analyzer", return_value=analyzer):
            first = NewsAnalysisService.analyze("CACHECO")
            second = NewsAnalysisService.analyze("CACHECO")

        self.assertEqual(first, second)
        get.assert_called_once()
        analyzer.analyze.assert_called_once()

    def test_shared_analyzer_is_constructed_once(self):
        previous = NewsAnalysisService._analyzer
        NewsAnalysisService._analyzer = None
        try:
            with patch("src.news.analysis_service.AISentimentAnalyzer") as analyzer_class:
                first = NewsAnalysisService._shared_analyzer()
                second = NewsAnalysisService._shared_analyzer()
            self.assertIs(first, second)
            analyzer_class.assert_called_once()
        finally:
            NewsAnalysisService._analyzer = previous
