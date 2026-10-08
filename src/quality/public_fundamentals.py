"""Published company data from Screener, Moneycontrol, and NSE reports.

Price data remains with Kite. Each company field carries source/reporting dates.
There is no paid API dependency and no inference of unavailable numeric values.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, timedelta
from hashlib import sha256
from io import BytesIO
import json
import logging
from math import isfinite
from pathlib import Path
import re
from threading import RLock
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo
from uuid import uuid4

from bs4 import BeautifulSoup
import pandas as pd
from pypdf import PdfReader
from pypdf.errors import PyPdfError
import requests

from src.quality.models import FundamentalSnapshot

logger = logging.getLogger(__name__)


def number(value):
    if value is None:
        return None
    match = re.fullmatch(r"[-+]?\d+(?:\.\d+)?", str(value).replace(",", "").replace("%", "").strip())
    result = float(match.group()) if match else None
    return result if result is not None and isfinite(result) else None


def table(section):
    """Read the first table, preserving periods and missing cell positions."""
    if section is None or section.find("table") is None:
        return [], {}
    node = section.find("table")
    periods = [cell.get("data-date-key") or cell.get_text(" ", strip=True)
               for cell in node.select("thead th")[1:]]
    rows = {}
    for row in node.select("tbody tr"):
        cells = row.find_all("td", recursive=False)
        if cells:
            key = re.sub(r"\s+", " ", cells[0].get_text(" ", strip=True)).strip(" +")
            rows[key] = [number(cell.get_text(" ", strip=True)) for cell in cells[1:]]
    return periods, rows


def period_date(value):
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        try:
            stamp = pd.Timestamp(datetime.strptime(str(value).strip(), "%b %Y"))
            return (stamp + pd.offsets.MonthEnd(0)).date()
        except ValueError:
            return None


def yoy_growth(periods, values):
    dates = [period_date(period) for period in periods]
    result = []
    for index in range(max(0, len(periods) - 3), len(periods)):
        current = dates[index]
        prior = next((j for j, stamp in enumerate(dates) if stamp and current
                      and stamp.year == current.year - 1 and stamp.month == current.month), None)
        value = values[index] if index < len(values) else None
        base = values[prior] if prior is not None and prior < len(values) else None
        result.append(round((value / base - 1) * 100, 4)
                      if value is not None and base is not None and base > 0 else None)
    return tuple(result) if len(result) == 3 and all(v is not None for v in result) else None


class PublicFundamentalProvider:
    """Combine reachable public sources with bounded requests and dated caching."""

    def __init__(self, *, timeout=10, cache_directory=".cache/public_fundamentals",
                 today=None, fetcher=None, include_current_session=False):
        self.timeout = timeout
        self.root = Path(cache_directory)
        if not self.root.is_absolute():
            self.root = Path(__file__).resolve().parents[2] / self.root
        self.today = today or datetime.now(ZoneInfo("Asia/Kolkata")).date()
        self.fetcher = fetcher
        self.include_current_session = include_current_session
        self._lock = RLock()
        self._archive_lock = RLock()
        self._snapshots = {}
        self._archives = None
        self._failures = {}

    def _fetch(self, url, *, ttl=300):
        if self.fetcher:
            return self.fetcher(url)
        path = self.root / (sha256(url.encode()).hexdigest() + ".cache")
        if path.exists() and datetime.now().timestamp() - path.stat().st_mtime < ttl:
            return path.read_bytes()
        for attempt in range(2):
            try:
                response = requests.get(url, timeout=(5, self.timeout), headers={
                    "User-Agent": "Mozilla/5.0", "Accept": "*/*",
                    "Referer": f"https://{urlparse(url).netloc}/",
                })
                break
            except (requests.Timeout, requests.ConnectionError):
                if attempt:
                    raise
        response.raise_for_status()
        self.root.mkdir(parents=True, exist_ok=True)
        # Atomic replacement prevents concurrent readers seeing a partial response.
        temporary = path.with_suffix(f".{uuid4().hex}.tmp")
        temporary.write_bytes(response.content)
        temporary.replace(path)
        return response.content

    def _attempt(self, symbol, source, operation):
        try:
            return operation()
        except (requests.RequestException, ValueError, KeyError, TypeError, IndexError, OSError, PyPdfError) as exc:
            with self._lock:
                self._failures.setdefault(symbol, {})[source] = exc.__class__.__name__
            logger.info("Public company source %s unavailable for %s: %s", source, symbol,
                        exc.__class__.__name__)
            return None

    @staticmethod
    def parse_screener(symbol, html, *, today, source_url=None, reporting_basis="consolidated"):
        soup = BeautifulSoup(html, "html.parser")
        ratios = {}
        for item in soup.select("#top-ratios li"):
            name = item.select_one(".name")
            value = item.select_one(".number")
            if name and value:
                ratios[name.get_text(" ", strip=True)] = number(value.get_text(strip=True))
        if not ratios or not any(value is not None for value in ratios.values()):
            raise ValueError("Screener company ratios are absent")
        quarterly_periods, quarters = table(soup.find(id="quarters"))
        balance_periods, balance = table(soup.find(id="balance-sheet"))
        holding_periods, holdings = table(soup.find(id="shareholding"))
        # Screener omits the quarterly promoter row when it is zero. Only use
        # the second table if its last two dates match the quarterly periods.
        holding_section = soup.find(id="shareholding")
        if holding_section and "Promoters" not in holdings:
            tables = holding_section.find_all("table")
            if len(tables) > 1:
                extra_periods, extra_rows = table(BeautifulSoup(str(tables[1]), "html.parser"))
                if extra_periods[-2:] == holding_periods[-2:] and "Promoters" in extra_rows:
                    holdings["Promoters"] = [None] * (len(holding_periods) - 2) + extra_rows["Promoters"][-2:]
        cash_periods, cash = table(soup.find(id="cash-flow"))
        source = source_url or f"https://www.screener.in/company/{quote(symbol)}/consolidated/"
        evidence = {}
        data = {}

        def put(field, value, period, basis):
            data[field] = value
            if value is not None:
                evidence[field] = {"url": source, "period": str(period), "basis": basis,
                                   "retrieved_on": today.isoformat()}

        balance_date = period_date(balance_periods[-1]) if balance_periods else None
        annual_current = balance_date and 0 <= (today - balance_date).days <= 450
        put("roe", ratios.get("ROE") if annual_current else None, balance_date, f"Published {reporting_basis} ROE percent")
        put("roce", ratios.get("ROCE") if annual_current else None, balance_date, f"Published {reporting_basis} ROCE percent")
        put("pe_ratio", ratios.get("Stock P/E"), today, f"Published {reporting_basis} stock PE")
        put("market_cap", ratios.get("Market Cap"), today, "INR crore")
        if balance_periods:
            last = len(balance_periods) - 1
            stamp = period_date(balance_periods[last])
            if stamp and 0 <= (today - stamp).days <= 450:
                debt = (balance.get("Borrowings") or balance.get("Borrowing")
                        or [None] * (last + 1))[last]
                capital = (balance.get("Equity Capital") or [None] * (last + 1))[last]
                reserves = (balance.get("Reserves") or [None] * (last + 1))[last]
                equity = capital + reserves if capital is not None and reserves is not None else None
                put("total_debt", debt, stamp, "Reported borrowings, INR crore; includes reported lease liabilities")
                put("debt_to_equity", debt / equity if debt is not None and equity and equity > 0 else None,
                    stamp, "Borrowings / (equity capital + reserves), consolidated")
                if last >= 1:
                    previous_stamp = period_date(balance_periods[last-1])
                    prior_debt = (balance.get("Borrowings") or balance.get("Borrowing") or [None]*(last+1))[last-1]
                    prior_capital = (balance.get("Equity Capital") or [None]*(last+1))[last-1]
                    prior_reserves = (balance.get("Reserves") or [None]*(last+1))[last-1]
                    prior_equity = prior_capital+prior_reserves if prior_capital is not None and prior_reserves is not None else None
                    if previous_stamp and previous_stamp < stamp:
                        put("previous_total_debt", prior_debt, previous_stamp, "Prior reported borrowings, INR crore; same reporting basis")
                        put("previous_debt_to_equity", prior_debt/prior_equity if prior_debt is not None and prior_equity and prior_equity > 0 else None,
                            previous_stamp, "Prior borrowings / equity; same reporting basis")
        if quarterly_periods:
            latest = period_date(quarterly_periods[-1])
            if latest and 0 <= (today - latest).days <= 150:
                revenue_row = next((key for key in ("Sales", "Revenue", "Revenue from Operations", "Total Income")
                                    if key in quarters), "Sales")
                for field, row in (("quarterly_revenue_growth_pct", revenue_row),
                                   ("quarterly_profit_growth_pct", "Net Profit")):
                    values = quarters.get(row, [])
                    if len(values) == len(quarterly_periods):
                        put(field, yoy_growth(quarterly_periods, values), latest,
                            f"Latest three quarters, year-on-year growth, {reporting_basis}; oldest to newest")
                evidence["quarterly_periods"] = {"periods": quarterly_periods[-3:], "url": source}
        if holding_periods:
            latest = period_date(holding_periods[-1])
            if latest and 0 <= (today - latest).days <= 150:
                for key, prefix in (("FIIs", "fii"), ("DIIs", "dii"), ("Promoters", "promoter")):
                    values = holdings.get(key, [])
                    if len(values) == len(holding_periods) and values:
                        put(f"{prefix}_holding_percent", values[-1], latest, "Published shareholding percent")
                        change = values[-1] - values[-2] if len(values) > 1 and all(
                            v is not None for v in values[-2:]) else None
                        put(f"{prefix}_holding_change_pct_points", change, latest,
                            "Latest reported quarter minus previous quarter, percentage points")
        cash_date = period_date(cash_periods[-1]) if cash_periods else None
        if cash_date and 0 <= (today - cash_date).days <= 450 and cash.get("Cash from Operating Activity"):
            put("operating_cash_flow", cash["Cash from Operating Activity"][-1], cash_periods[-1], "INR crore")
        ratio_periods, ratio_rows = table(soup.find(id="ratios"))
        if len(ratio_periods) >= 2:
            last_stamp, previous_stamp = period_date(ratio_periods[-1]), period_date(ratio_periods[-2])
            if annual_current and last_stamp and previous_stamp and last_stamp == balance_date and previous_stamp < last_stamp:
                for field, labels in (("previous_roe", ("ROE %", "ROE")), ("previous_roce", ("ROCE %", "ROCE"))):
                    values = next((ratio_rows[label] for label in labels if label in ratio_rows), [])
                    if len(values) == len(ratio_periods):
                        put(field, values[-2], previous_stamp, f"Prior published {reporting_basis} ratio percent; same ratio table")
        return FundamentalSnapshot(symbol=symbol, source="Screener", as_of=today.isoformat(),
                                   evidence=evidence, **data), soup

    @staticmethod
    def moneycontrol_url(symbol, suggestions):
        for row in suggestions:
            description = BeautifulSoup(row.get("pdt_dis_nm", ""), "html.parser")
            span = description.find("span")
            tokens = [part.strip().upper() for part in span.get_text().split(",")] if span else []
            url = row.get("link_src", "")
            if symbol.upper() in tokens and urlparse(url).hostname == "www.moneycontrol.com":
                return url
        return None

    @staticmethod
    def parse_moneycontrol(html, *, today):
        soup = BeautifulSoup(html, "html.parser")
        pe = soup.select_one(".nsepe")
        sector = soup.select_one(".nsesc_ttm")
        baseline = soup.select_one(".nsed20ad")
        values = {"pe_ratio": number(pe.get_text(strip=True)) if pe else None,
                  "sector_pe": number(sector.get_text(strip=True)) if sector else None,
                  "monthly_delivery_percent": number(baseline.get_text(strip=True)) if baseline else None}
        block = soup.find(id="lockdl")
        deals = []
        if block:
            for node in block.select(".bd_bx"):
                stamp = node.select_one(".br_date")
                if stamp:
                    try:
                        deals.append(datetime.strptime(stamp.get_text(strip=True), "%d %b, %Y").date())
                    except ValueError:
                        pass
            text = block.get_text(" ", strip=True).lower()
            if deals or re.search(r"no\s+(?:data|block deals)", text):
                # Conservatively flag any recent deal for review; do not claim causal impact.
                values["block_deal_price_impact"] = any(0 <= (today - stamp).days <= 30 for stamp in deals)
        return values, [stamp.isoformat() for stamp in deals]

    def _load_archives(self):
        with self._archive_lock:
            if self._archives is not None:
                return self._archives
            candidates = [self.today - timedelta(days=i) for i in range(0 if self.include_current_session else 1, 46)
                          if (self.today - timedelta(days=i)).weekday() < 5]

            def load(stamp):
                url = f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{stamp:%d%m%Y}.csv"
                try:
                    content = self._fetch(url, ttl=86400 * 30)
                    frame = pd.read_csv(BytesIO(content), skipinitialspace=True)
                    frame.columns = frame.columns.str.strip()
                    required = {"SYMBOL", "SERIES", "DELIV_QTY", "TTL_TRD_QNTY", "DATE1"}
                    if not required.issubset(frame.columns):
                        return None
                    frame["SYMBOL"] = frame["SYMBOL"].astype(str).str.strip()
                    frame["SERIES"] = frame["SERIES"].astype(str).str.strip()
                    frame = frame[frame["SERIES"] == "EQ"].set_index("SYMBOL")
                    return stamp, frame, url
                except (requests.RequestException, ValueError, OSError):
                    return None
            # Shared all-stock reports are downloaded once, not once per company.
            with ThreadPoolExecutor(max_workers=4) as pool:
                loaded = list(pool.map(load, candidates))
            self._archives = sorted((item for item in loaded if item), key=lambda item: item[0], reverse=True)[:21]
            return self._archives

    def _delivery(self, symbol):
        records = []
        for stamp, frame, url in self._load_archives():
            if symbol not in frame.index or frame.index.has_duplicates:
                continue
            row = frame.loc[symbol]
            delivered, traded = number(row.get("DELIV_QTY")), number(row.get("TTL_TRD_QNTY"))
            if delivered is not None and traded and 0 <= delivered <= traded:
                records.append((stamp, delivered, traded, url))
        if not records or (self.today - records[0][0]).days > 5:
            return {}, {}
        stamp, delivered, traded, url = records[0]
        values = {"delivery_percent": delivered / traded * 100}
        evidence = {"delivery_percent": {"url": url, "period": stamp.isoformat(),
                    "basis": "Latest completed trading session; delivered / traded quantity"}}
        if len(records) >= 21:
            previous = records[1:21]
            values["monthly_delivery_percent"] = sum(item[1] for item in previous) / sum(item[2] for item in previous) * 100
            evidence["monthly_delivery_percent"] = {
                "url": "https://www.nseindia.com/all-reports", "report_urls": [item[3] for item in previous],
                "period": f"{previous[-1][0]} to {previous[0][0]}", "sessions": 20,
                "basis": "Volume-weighted delivery percentage over preceding 20 sessions; excludes latest session"}
        return values, evidence

    @staticmethod
    def commentary(text):
        normalized = re.sub(r"\s+", " ", text).lower()
        # Limit the assessment to prepared remarks, excluding analysts' questions.
        match = re.search(r"(?:we (?:will|can|shall) now (?:begin|open|take)|open (?:the )?floor).*?(?:question|q&a)", normalized)
        prepared = normalized[:match.start()] if match else normalized[:25000]
        themes = {
            "growth": r"strong growth|growth momentum|double.digit growth|accelerat(?:ing|e).*growth|robust growth",
            "demand": r"strong demand|robust demand|healthy demand|strong pipeline|robust pipeline",
            "orders": r"strong order|record (?:order|bookings)|deal wins|order book|large deal momentum",
            "margins": r"margin expansion|improv.{0,25}margin|margin.{0,25}improv|strong profitability",
            "outlook": r"confident|optimistic|raise.{0,20}guidance|reaffirm.{0,20}guidance|strong outlook",
        }
        matched = [name for name, pattern in themes.items() if re.search(pattern, prepared)]
        negatives = re.findall(r"weak demand|lower.{0,20}guidance|margin pressure|margin.{0,35}(?:down|declin)|deteriorat\w*", prepared)
        strength = ("VERY_STRONG" if len(matched) >= 4 and "growth" in matched
                    and "outlook" in matched and "margins" in matched and not negatives
                    else "STRONG" if len(matched) >= 3 and not negatives else "MIXED")
        return strength, {"method": "Conservative rule-based interpretation of prepared remarks; not a company-reported rating",
                          "positive_themes": matched, "negative_signal_count": len(negatives)}

    def _commentary(self, soup):
        documents = soup.find(id="documents")
        if documents is None:
            return None
        company_domains = set()
        for link in soup.select("#top a[href]"):
            if link.get_text(strip=True) == "Website":
                host = urlparse(link["href"]).hostname
                if host:
                    company_domains.add(host.removeprefix("www."))
        for link in documents.find_all("a", href=True):
            if link.get_text(strip=True) != "Transcript":
                continue
            url = link["href"]
            host = urlparse(url).hostname or ""
            if host not in {"www.bseindia.com", "nsearchives.nseindia.com", "archives.nseindia.com"} and not any(
                host == domain or host.endswith("." + domain) for domain in company_domains):
                continue
            row = link.find_parent("li") or link.parent
            label = row.get_text(" ", strip=True)
            stamp_match = re.search(r"\b([A-Z][a-z]{2} 20\d{2})\b", label)
            stamp = (datetime.strptime(stamp_match.group(1), "%b %Y").date()
                     if stamp_match else None)
            if not stamp or not 0 <= (self.today - stamp).days <= 150:
                continue
            try:
                content = self._fetch(url, ttl=86400 * 30)
            except requests.RequestException:
                continue
            if not content.startswith(b"%PDF") or len(content) > 15000000:
                continue
            reader = PdfReader(BytesIO(content))
            text = "\n".join(page.extract_text() or "" for page in reader.pages[:50])
            if len(text.strip()) < 1000:
                continue
            strength, details = self.commentary(text)
            return strength, {**details, "url": url, "period": stamp.isoformat()}
        return None

    def read_cached_snapshot(self, symbol, *, max_age_seconds=3600):
        """Read dated parsed facts without issuing a request or extending their age."""
        path = self.root / f'snapshot_{symbol}.json'
        try:
            data = json.loads(path.read_text())
            stamp = datetime.fromisoformat(data['saved_at'])
            age = (datetime.now(ZoneInfo('Asia/Kolkata')) - stamp).total_seconds()
            if not 0 <= age < max_age_seconds or data['payload']['symbol'] != symbol:
                return None
            return {**data, 'source': str(path.resolve())}
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def get_fundamentals(self, symbol):
        symbol = str(symbol).strip().upper().removesuffix(".NS")
        if not re.fullmatch(r"[A-Z0-9.&-]{1,30}", symbol):
            raise ValueError("Invalid NSE symbol")
        with self._lock:
            if symbol in self._snapshots:
                return self._snapshots[symbol]
        disk = self.read_cached_snapshot(symbol)
        if disk:
            snapshot = FundamentalSnapshot(**disk['payload'])
            with self._lock:
                self._snapshots[symbol] = snapshot
            return snapshot
        screener_url = f"https://www.screener.in/company/{quote(symbol)}/consolidated/"
        parsed = self._attempt(symbol, "Screener", lambda: self.parse_screener(
            symbol, self._fetch(screener_url, ttl=3600), today=self.today))
        if parsed is None:
            standalone_url = f"https://www.screener.in/company/{quote(symbol)}/"
            parsed = self._attempt(symbol, "Screener standalone", lambda: self.parse_screener(
                symbol, self._fetch(standalone_url, ttl=3600), today=self.today,
                source_url=standalone_url, reporting_basis="standalone"))
        snapshot, soup = parsed if parsed else (FundamentalSnapshot(symbol), None)
        values, evidence = {}, dict(snapshot.evidence)
        suggestions_url = "https://www.moneycontrol.com/mccode/common/autosuggestion_solr.php?" + f"query={quote(symbol)}&type=1&format=json"
        suggestions = self._attempt(symbol, "Moneycontrol lookup", lambda: json.loads(self._fetch(suggestions_url, ttl=86400)))
        mc_url = self.moneycontrol_url(symbol, suggestions) if isinstance(suggestions, list) else None
        if mc_url:
            mc = self._attempt(symbol, "Moneycontrol", lambda: self.parse_moneycontrol(self._fetch(mc_url), today=self.today))
            if mc:
                fields, deals = mc
                # Keep stock PE and sector PE from the same publisher/basis.
                if fields.get("pe_ratio") is not None and fields.get("sector_pe") is not None:
                    values.update({key: fields[key] for key in ("pe_ratio", "sector_pe")})
                elif fields.get("sector_pe") is not None and snapshot.pe_ratio is not None:
                    values["sector_pe"] = fields["sector_pe"]
                for key in ("monthly_delivery_percent", "block_deal_price_impact"):
                    if fields.get(key) is not None:
                        values[key] = fields[key]
                for key in values:
                    evidence[key] = {"url": mc_url, "period": self.today.isoformat(),
                        "basis": ("Moneycontrol sector PE compared with Screener stock PE; cross-publisher comparison"
                                  if key == "sector_pe" and fields.get("pe_ratio") is None else
                                  "Moneycontrol published TTM valuation") if key in {"pe_ratio", "sector_pe"}
                        else "Moneycontrol published 20-day delivery baseline" if key == "monthly_delivery_percent"
                        else "Any reported block deal in the past 30 days is conservatively flagged; price causality is not inferred",
                        "deal_dates": deals if key == "block_deal_price_impact" else []}
        delivery = self._attempt(symbol, "NSE delivery archives", lambda: self._delivery(symbol))
        if delivery:
            values.update(delivery[0])
            evidence.update(delivery[1])
        pledge_url = f"https://www.nseindia.com/api/corporate-pledgedata?index=equities&symbol={quote(symbol)}"
        pledge = self._attempt(symbol, "NSE promoter pledge", lambda: json.loads(self._fetch(pledge_url)))
        if isinstance(pledge, dict):
            rows = pledge.get("data", [])
            if rows:
                row = rows[0]
                stamp = None
                try:
                    stamp = datetime.strptime(row.get("shp", ""), "%d-%b-%Y").date()
                except ValueError:
                    pass
                amount = number(row.get("percPromoterShares"))
                if amount is None and number(row.get("totPromoterShares")) == 0 and number(row.get("totPromoterHolding")) == 0:
                    amount = 0
                if amount is not None and stamp and 0 <= (self.today - stamp).days <= 150:
                    values["promoter_pledge"] = amount
                    evidence["promoter_pledge"] = {"url": pledge_url, "period": stamp.isoformat(),
                        "basis": "Promoter encumbered shares / promoter holdings, percent; not all depository pledged shares"}
        if soup:
            commentary = self._attempt(symbol, "Company earnings transcript", lambda: self._commentary(soup))
            if commentary:
                values["commentary_strength"] = commentary[0]
                evidence["commentary_strength"] = commentary[1]
        with self._lock:
            if self._failures.get(symbol):
                evidence["source_errors"] = dict(self._failures[symbol])
        snapshot = replace(snapshot, **values, evidence=evidence,
                           source="Screener / Moneycontrol / NSE / company filings", as_of=self.today.isoformat())
        with self._lock:
            self._snapshots[symbol] = snapshot
        if not self.fetcher:
            try:
                from dataclasses import asdict
                self.root.mkdir(parents=True, exist_ok=True)
                path = self.root / f'snapshot_{symbol}.json'
                temporary = path.with_suffix(f'.{uuid4().hex}.tmp')
                temporary.write_text(json.dumps({'saved_at': datetime.now(ZoneInfo('Asia/Kolkata')).isoformat(),
                                                 'payload': asdict(snapshot)}, default=str))
                temporary.replace(path)
            except OSError:
                pass  # Persistence cannot erase successfully fetched facts.
        return snapshot

    def prefetch(self, symbols):
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(self.get_fundamentals, symbols))

    def invalidate_snapshot(self, symbol):
        with self._lock:
            self._snapshots.pop(symbol, None)
