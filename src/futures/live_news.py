"""Fresh collection with cached semantic analysis; new unchecked stories block approval."""
from datetime import timedelta, datetime, timezone
from urllib.parse import quote_plus
from xml.etree import ElementTree

import requests

from src.futures.cache import fingerprint
from src.news.analysis_service import NewsAnalysisService
from src.news.today import matches


def article_fingerprint(articles):
    values = []
    for row in articles:
        value = {key: row.get(key) for key in ('title', 'description', 'source', 'published', 'url')}
        if value['published']:
            try:
                stamp = datetime.fromisoformat(value['published'])
                if stamp.tzinfo is not None:
                    value['published'] = stamp.astimezone(timezone.utc).isoformat()
            except (ValueError, TypeError):
                pass
        values.append(value)
    return fingerprint(sorted(values, key=lambda row: (str(row['published']), str(row['title']), str(row['url']))))


def collect(symbol, aliases, now, timeout):
    url = 'https://news.google.com/rss/search?q='+quote_plus(f'{symbol} NSE stock when:72h')+'&hl=en-IN&gl=IN&ceid=IN:en'
    response = requests.get(url, timeout=timeout, headers={'User-Agent': 'alphatrace/1.0'})
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    articles, seen = [], set()
    for item in root.findall('./channel/item'):
        title = NewsAnalysisService._text(item.findtext('title', ''))
        mentioned = matches(title, aliases)
        if symbol not in mentioned or len(mentioned) != 1:
            continue
        stamp = NewsAnalysisService._published_datetime(item.findtext('pubDate', ''))
        source = NewsAnalysisService._text(item.findtext('source', ''))
        if stamp is None or not now-timedelta(hours=72) <= stamp <= now or (title.casefold(),source.casefold()) in seen:
            continue
        seen.add((title.casefold(), source.casefold()))
        articles.append({'title': title, 'description': NewsAnalysisService._text(item.findtext('description', '')),
            'source': source, 'published': stamp.isoformat(), 'url': NewsAnalysisService._text(item.findtext('link', '')) or None})
    return sorted(articles, key=lambda row: row['published'], reverse=True)[:16]


def fresh_result(articles, cached, now):
    identity = article_fingerprint(articles)
    if not articles:
        return {'news_state': 'NO_RELEVANT_NEWS', 'collection_state': 'FETCHED', 'analysis_state': 'NO_RELEVANT_NEWS',
                'checked_at': now.isoformat(), 'headlines': [], 'article_assessments': [], 'events': [],
                'sentiment': 'UNAVAILABLE', 'articles_fingerprint': identity}
    cached_identity = cached.get('articles_fingerprint') if cached else None
    if cached and not cached_identity:
        headlines = cached.get('headlines', [])
        complete = cached.get('article_count',len(headlines)) <= len(headlines) and len(cached.get('article_assessments',[])) <= len(headlines)
        if complete:
            cached_identity = article_fingerprint(headlines)
    if cached and cached_identity == identity and cached.get('news_state') == 'ANALYZED':
        return {**cached, 'checked_at': now.isoformat(), 'analysis_reused': True, 'articles_fingerprint':identity,
                'analysis_original_checked_at':cached.get('analysis_original_checked_at',cached.get('checked_at'))}
    return {'news_state': 'UNANALYSED_NEW_HEADLINES', 'collection_state': 'FETCHED', 'analysis_state': 'UNKNOWN',
            'checked_at': now.isoformat(), 'headlines': articles, 'events': [], 'sentiment': 'UNAVAILABLE',
            'articles_fingerprint': identity, 'last_successful_analysis':cached,
            'reason': 'New or changed stories require prepared semantic/event analysis; live scan never waives this check.'}
