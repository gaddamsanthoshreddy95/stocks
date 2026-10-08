"""Fresh India-date news discovery and price/news contradiction checks."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from urllib.parse import quote_plus
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from src.news.analysis_service import NewsAnalysisService


PUBLISHERS = ("moneycontrol.com", "economictimes.indiatimes.com", "livemint.com", "cnbctv18.com")
INDEX_FUTURES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "SENSEX", "BANKEX"}
INDIA = ZoneInfo("Asia/Kolkata")


def stock_aliases(symbols, instrument_path="data/instruments.csv"):
    aliases = {symbol: {symbol} for symbol in symbols if symbol not in INDEX_FUTURES}
    path = Path(instrument_path)
    if path.exists():
        instruments = pd.read_csv(path, keep_default_na=False)
        for row in instruments.to_dict("records"):
            symbol = row.get("tradingsymbol")
            if row.get("exchange") != "NSE" or symbol not in aliases:
                continue
            name = re.sub(r"\b(?:LIMITED|LTD|LTD\.)\b", "", str(row.get("name", "")), flags=re.I).strip()
            if len(name) >= 5:
                aliases[symbol].add(name)
    common = {"SBIN": {"SBI", "State Bank of India"}, "LT": {"L&T", "Larsen and Toubro", "Larsen & Toubro"},
              "M&M": {"Mahindra & Mahindra", "Mahindra and Mahindra"},
              "HDFCBANK": {"HDFC Bank"}, "ICICIBANK": {"ICICI Bank"},
              "PNBHOUSING": {"PNB Housing"}, "LICHSGFIN": {"LIC Housing"},
              "MOTILALOFS": {"Motilal Oswal"}, "ANANDRATHI": {"Anand Rathi Wealth"},
              "HDFCLIFE": {"HDFC Life"}, "ICICIGI": {"ICICI Lombard"},
              "ENRIN": {"Siemens Energy"}, "INFY": {"Infosys"}}
    for symbol, names in common.items():
        if symbol in aliases:
            aliases[symbol].update(names)
    return aliases


def matches(text, aliases):
    result = []
    for symbol, names in aliases.items():
        for name in names:
            # Exchange references must not be confused with BSE Ltd stock news.
            if name in {"BSE", "NSE", "IDEA", "OIL"}:
                expression = r"(?<!\w)" + re.escape(name) + r"\s+(?:shares?|stocks?|profit|results?|earnings)\b"
            elif name == "SBI":
                expression = r"\bSBI\b(?!\s+Life\b)"
            elif name == "PNB":
                expression = r"\bPNB\b(?!\s+Housing\b)"
            else:
                expression = r"(?<!\w)" + re.escape(name) + r"(?!\w)"
            if re.search(expression, text, flags=re.I):
                result.append(symbol)
                break
    return sorted(result)


class TodayNewsService:
    @staticmethod
    def discover(symbols, *, now=None, aliases=None, fetcher=None):
        now = now or datetime.now(timezone.utc)
        day = now.astimezone(INDIA).date()
        symbols = [symbol for symbol in symbols if symbol not in INDEX_FUTURES]
        aliases = aliases or stock_aliases(symbols)
        aliases = {symbol: names for symbol, names in aliases.items() if symbol in symbols}
        # Yesterday in the query avoids losing early IST headlines dated yesterday in UTC.
        after = (day - timedelta(days=1)).isoformat()

        def fetch(domain):
            query = f'site:{domain} ("stocks to watch" OR "stocks in news" OR shares OR earnings) after:{after}'
            url = "https://news.google.com/rss/search?q=" + quote_plus(query) + "&hl=en-IN&gl=IN&ceid=IN:en"
            url += "&_=" + str(int(now.timestamp() * 1000))
            try:
                if fetcher:
                    content = fetcher(domain, url)
                else:
                    response = requests.get(url, timeout=8, headers={"User-Agent": "alphatrace/1.0"})
                    response.raise_for_status()
                    content = response.content
                root = ElementTree.fromstring(content)
                articles = []
                for node in root.findall("./channel/item"):
                    stamp = NewsAnalysisService._published_datetime(node.findtext("pubDate", ""))
                    if stamp is None or stamp > now or stamp.astimezone(INDIA).date() != day:
                        continue
                    title = NewsAnalysisService._text(node.findtext("title", ""))
                    description = NewsAnalysisService._text(node.findtext("description", ""))
                    matched = matches(title + " " + description, aliases)
                    if matched:
                        articles.append({"title": title, "description": description,
                            "source": NewsAnalysisService._text(node.findtext("source", domain)),
                            "published": stamp.isoformat(), "url": node.findtext("link"),
                            "matched_symbols": matched})
                return {"publisher": domain, "state": "FETCHED", "matching_articles": len(articles)}, articles
            except (requests.RequestException, ElementTree.ParseError, ValueError) as exc:
                return {"publisher": domain, "state": "FETCH_FAILED", "error": exc.__class__.__name__}, []

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(fetch, PUBLISHERS))
        articles, seen = [], set()
        for _, items in results:
            for article in items:
                identity = (article["title"].casefold(), article["source"].casefold())
                if identity not in seen:
                    articles.append(article)
                    seen.add(identity)
        articles.sort(key=lambda item: item["published"], reverse=True)
        return {"run_date": day.isoformat(), "timezone": "Asia/Kolkata",
                "checked_at": now.isoformat(), "available": any(status["state"] == "FETCHED" for status, _ in results),
                "sources": [status for status, _ in results], "articles": articles,
                "symbols": sorted({symbol for article in articles for symbol in article["matched_symbols"]})}

    @staticmethod
    def direction_check(news, price_change_percent, direction, *, now=None, discovered=False):
        now = now or datetime.now(timezone.utc)
        day = now.astimezone(INDIA).date()
        checked = news.get("checked_at") or news.get("generated_at")
        try:
            stamp = datetime.fromisoformat(checked)
            fresh = (stamp.tzinfo is not None and stamp.astimezone(INDIA).date() == day
                     and 0 <= (now - stamp).total_seconds() <= 900)
        except (ValueError, TypeError):
            fresh = False
        state = news.get("news_state")
        complete = fresh and state in {"ANALYZED", "NO_RELEVANT_NEWS"}
        today = []
        for article in news.get("article_assessments", []):
            try:
                stamp = datetime.fromisoformat(article.get("published", ""))
            except (ValueError, TypeError):
                continue
            if stamp.tzinfo is None:
                continue
            if stamp <= now and stamp.astimezone(INDIA).date() == day:
                today.append(article)
        if discovered and not today:
            complete = False  # Roundup mentions alone cannot establish stock-specific sentiment.
        if price_change_percent is None:
            complete = False
        positive = [article for article in today if article.get("sentiment") in {"BULLISH", "POSITIVE"}]
        negative = [article for article in today if article.get("sentiment") in {"BEARISH", "NEGATIVE"}]
        reasons = []
        if not complete:
            reasons.append("TODAY_NEWS_CHECK_INCOMPLETE")
        if price_change_percent is not None and price_change_percent > 0 and negative:
            reasons.append("PRICE_RISING_WITH_NEGATIVE_NEWS")
        if price_change_percent is not None and price_change_percent < 0 and positive:
            reasons.append("PRICE_FALLING_WITH_POSITIVE_NEWS")
        if direction == "BULLISH" and negative:
            reasons.append("NEGATIVE_NEWS_OPPOSES_LONG_TRADE")
        if direction == "BEARISH" and positive:
            reasons.append("POSITIVE_NEWS_OPPOSES_SHORT_TRADE")
        if positive and negative:
            reasons.append("MIXED_NEWS_REQUIRES_REVIEW")
        return {"approved": not reasons, "run_date": day.isoformat(), "checked_at": checked,
                "price_change_percent": price_change_percent, "direction": direction,
                "status": ("UNVERIFIED" if not complete else "CONFLICT" if reasons
                           else "NO_TODAY_NEWS" if not today
                           else "SUPPORTIVE" if (direction == "BULLISH" and positive
                                                  or direction == "BEARISH" and negative)
                           else "NEUTRAL"),
                "reason_codes": reasons, "positive_articles": positive, "negative_articles": negative}
