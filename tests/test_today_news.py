from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
import pytest

from src.news.today import TodayNewsService, matches
from src.news.analysis_service import NewsAnalysisService
from src.news.ai_sentiment import AISentimentAnalyzer
from src.quality.futures_selection import block_trade
from src.workflow.final_decision import FinalConsistencyValidator


NOW = datetime(2026, 10, 8, 4, 30, tzinfo=timezone.utc)


def rss(items):
    return ('<rss><channel>' + ''.join(
        f'<item><title>{title}</title><source>Publisher</source><pubDate>{stamp}</pubDate><link>https://example.test/news</link></item>'
        for title, stamp in items) + '</channel></rss>').encode()


def news(labels, *, checked=NOW, published=NOW):
    return {'news_state': 'ANALYZED', 'checked_at': checked.isoformat(),
            'article_assessments': [
                {'title': label, 'sentiment': label, 'published': published.isoformat(),
                 'url': 'https://example.test/news'} for label in labels]}


def test_discovery_uses_india_day_and_excludes_yesterday_future_and_undated():
    content = rss([
        ('TCS shares profit rises', 'Wed, 07 Oct 2026 19:30:00 GMT'),
        ('INFY shares fall yesterday', 'Wed, 07 Oct 2026 18:00:00 GMT'),
        ('SBIN shares rise in future', 'Thu, 08 Oct 2026 05:30:00 GMT'),
        ('SBIN result unknown date', '')])
    result = TodayNewsService.discover(
        ['TCS', 'INFY', 'SBIN'], aliases={s: {s} for s in ['TCS', 'INFY', 'SBIN']},
        now=NOW, fetcher=lambda domain, url: content)
    assert result['run_date'] == '2026-10-08'
    assert result['symbols'] == ['TCS']
    assert len(result['articles']) == 1
    assert len(result['sources']) == 4


def test_discovery_is_fetched_again_each_execution_and_records_failures():
    calls = []
    def fetch(domain, url):
        calls.append(domain)
        if domain == 'moneycontrol.com':
            return rss([('TCS earnings', 'Thu, 08 Oct 2026 04:00:00 GMT')])
        raise requests.ConnectionError('Unavailable publisher')
    for _ in range(2):
        result = TodayNewsService.discover(['TCS'], aliases={'TCS': {'TCS'}}, now=NOW, fetcher=fetch)
        assert result['available']
        assert len([s for s in result['sources'] if s['state'] == 'FETCH_FAILED']) == 3
    assert len(calls) == 8


def test_exchange_mentions_are_not_bse_stock_mentions():
    assert matches('NSE and BSE market update', {'BSE': {'BSE'}}) == []
    assert matches('BSE shares rise after results', {'BSE': {'BSE'}}) == ['BSE']
    assert matches('PNB Housing results', {'PNB': {'PNB'}, 'PNBHOUSING': {'PNB Housing'}}) == ['PNBHOUSING']
    assert matches('SBI Life results', {'SBIN': {'SBI'}}) == []


def test_index_futures_are_not_discovered_as_company_news():
    content = rss([('Nifty falls while TCS rises', 'Thu, 08 Oct 2026 04:00:00 GMT')])
    result = TodayNewsService.discover(['NIFTY', 'TCS'], aliases={'NIFTY': {'Nifty'}, 'TCS': {'TCS'}},
                                       now=NOW, fetcher=lambda domain, url: content)
    assert result['symbols'] == ['TCS']


@pytest.mark.parametrize('labels,change,direction,code', [
    (['BEARISH'], 2, 'BULLISH', 'PRICE_RISING_WITH_NEGATIVE_NEWS'),
    (['BULLISH'], -2, 'BULLISH', 'PRICE_FALLING_WITH_POSITIVE_NEWS'),
    (['BULLISH'], 2, 'BEARISH', 'POSITIVE_NEWS_OPPOSES_SHORT_TRADE'),
    (['BEARISH'], -2, 'BULLISH', 'NEGATIVE_NEWS_OPPOSES_LONG_TRADE'),
    (['BEARISH', 'BULLISH'], 2, 'BULLISH', 'MIXED_NEWS_REQUIRES_REVIEW'),
])
def test_contradictions_block_even_with_opposing_aggregate_sentiment(labels, change, direction, code):
    payload = {**news(labels), 'sentiment': 'BULLISH'}
    result = TodayNewsService.direction_check(payload, change, direction, now=NOW)
    assert not result['approved']
    assert code in result['reason_codes']
    trade = {'final_action': 'BUY', 'action': 'BUY', 'recommendation': 'BUY',
             'risk': {'quantity': 100}, 'trade_eligibility': {'eligible': True}}
    block_trade(trade, ', '.join(result['reason_codes']), 'TODAY_NEWS_CONFLICT')
    FinalConsistencyValidator.validate(trade)
    assert trade['risk']['quantity'] == 0


def test_yesterday_news_is_not_used_for_today_alignment():
    yesterday = datetime(2026, 10, 7, 4, 30, tzinfo=timezone.utc)
    result = TodayNewsService.direction_check(news(['BEARISH'], published=yesterday), 2, 'BULLISH', now=NOW)
    assert result['approved']
    assert result['status'] == 'NO_TODAY_NEWS'
    stale = TodayNewsService.direction_check(news(['BULLISH'], checked=yesterday), 2, 'BULLISH', now=NOW)
    assert not stale['approved']
    earlier_today = TodayNewsService.direction_check(
        news(['BULLISH'], checked=NOW-timedelta(minutes=30)), 2, 'BULLISH', now=NOW)
    assert not earlier_today['approved']


def test_discovered_roundup_without_individual_analysis_cannot_approve():
    result = TodayNewsService.direction_check(
        {'news_state': 'NO_RELEVANT_NEWS', 'checked_at': NOW.isoformat()}, 2, 'BULLISH',
        now=NOW, discovered=True)
    assert not result['approved']


def test_neutral_news_is_not_claimed_to_support_a_trade():
    neutral = TodayNewsService.direction_check(news(['NEUTRAL']), 2, 'BULLISH', now=NOW)
    assert neutral['status'] == 'NEUTRAL'
    assert neutral['approved']
    supportive = TodayNewsService.direction_check(news(['BULLISH']), 2, 'BULLISH', now=NOW)
    assert supportive['status'] == 'SUPPORTIVE'


def test_force_refresh_bypasses_news_cache():
    response = Mock(content=rss([('TCS earnings', datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT'))]))
    response.raise_for_status.return_value = None
    analyzer = Mock(model='test-model')
    analyzer.analyze.return_value = {'score': 50, 'confidence': 80, 'sentiment': 'BULLISH',
        'events': [], 'materiality': 'MEDIUM', 'trade_impact': 'SUPPORTIVE',
        'reasoning': [], 'article_assessments': []}
    NewsAnalysisService._cache.clear()
    with patch('src.news.analysis_service.requests.get', return_value=response) as get, patch.object(
        NewsAnalysisService, '_shared_analyzer', return_value=analyzer):
        NewsAnalysisService.analyze('TCS')
        NewsAnalysisService.analyze('TCS')
        assert get.call_count == 1
        NewsAnalysisService.analyze('TCS', force_refresh=True)
        assert get.call_count == 2
    NewsAnalysisService._cache.clear()


def test_ai_retains_source_and_time_for_article_level_direction_check():
    model = Mock(return_value=[{'label': 'positive', 'score': .8},
                              {'label': 'negative', 'score': .1}, {'label': 'neutral', 'score': .1}])
    nlp = Mock(return_value=SimpleNamespace(ents=[]))
    analyzer = AISentimentAnalyzer(sentiment_pipeline=model, nlp=nlp)
    result = analyzer.analyze('TCS', [{'title': 'TCS earnings', 'source': 'Publisher',
                                     'published': NOW.isoformat(), 'url': 'https://example.test/news'}])
    article = result['article_assessments'][0]
    assert article['published'] == NOW.isoformat()
    assert article['url'] == 'https://example.test/news'


@pytest.mark.parametrize('news_score,included', [(70, True), (30, False)])
def test_news_discovery_gets_a_shortlist_place_without_waiving_score_floor(news_score, included):
    from tests.test_lightweight_screen import ShortlistBoundaryTests
    fixtures = ShortlistBoundaryTests()
    platform = fixtures.platform()
    def screen(symbol, history, live, settings):
        score = {'A': news_score, 'B': 90, 'C': 80}[symbol]
        return SimpleNamespace(candidate=fixtures.candidate(symbol, score), timings={
            'lightweight_indicator_seconds': 0, 'lightweight_support_seconds': 0,
            'setup_decision_seconds': 0, 'liquidity_trust_seconds': 0})
    today = {'symbols': ['A'], 'articles': [{'title': 'A results', 'matched_symbols': ['A']}]}
    with patch('src.application.platform.run_lightweight_screen', side_effect=screen):
        result = platform._suggest_stocks(limit=2, minimum_score=40, enrich=False, today_news=today)
    selected = {item['symbol']: item for item in result['suggestions']}
    assert ('A' in selected) == included
    assert len(selected) == 2
    if included:
        assert selected['A']['primary_discovery_bucket'] == 'TODAY_NEWS'
        assert selected['A']['today_news_mentions']
