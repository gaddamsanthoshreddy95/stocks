from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


LOGGER = logging.getLogger(__name__)

SENSIBULL_RESULTS_URL = (
    "https://web.sensibull.com/"
    "stock-market-calendar/stock-results-calendar/"
)

OUTPUT_DIRECTORY = Path("data/cache/events")
OUTPUT_FILE = OUTPUT_DIRECTORY / "sensibull_results_calendar.json"

DATE_PATTERN = re.compile(
    r"(?P<day>\d{1,2})(?:st|nd|rd|th)\s+"
    r"(?P<month>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)",
    re.IGNORECASE,
)
SYMBOL_PATTERN = re.compile(r"\(([A-Z][A-Z0-9&-]*)\)")


def clean_text(value: str | None) -> str:
    """Normalize whitespace in extracted browser text."""
    if not value:
        return ""

    return re.sub(r"\s+", " ", value).strip()


def normalize_symbol(company_name: str) -> str:
    """
    Produce a temporary normalized identifier.

    Replace this with your NSE instrument-master mapping because company
    display names and NSE trading symbols are not always identical.
    """
    normalized = re.sub(r"[^A-Za-z0-9]", "", company_name).upper()
    return normalized


def _calendar_date(day: int, month_text: str, reference_date: date) -> date:
    """Resolve Sensibull's year-less date to the date nearest the scrape date."""
    month = datetime.strptime(month_text[:3].title(), "%b").month
    candidates = [date(year, month, day) for year in (
        reference_date.year - 1, reference_date.year, reference_date.year + 1
    )]
    return min(candidates, key=lambda value: abs((value - reference_date).days))


def structure_calendar_rows(
    rows: list[dict[str, Any]], reference_date: date | None = None
) -> list[dict[str, Any]]:
    """Add the symbol, event type, and inherited calendar date to scraped rows."""
    reference_date = reference_date or date.today()
    active_date: date | None = None
    structured: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        raw_text = clean_text(str(item.get("raw_text", "")))
        date_match = DATE_PATTERN.search(raw_text)
        if date_match:
            active_date = _calendar_date(
                int(date_match.group("day")), date_match.group("month"), reference_date
            )
        elif item.get("event_date"):
            try:
                active_date = date.fromisoformat(str(item["event_date"]))
            except ValueError:
                pass
        symbol_match = SYMBOL_PATTERN.search(raw_text)
        item.update({
            "symbol": symbol_match.group(1) if symbol_match else None,
            "event_type": "STOCK_RESULTS" if re.search(
                r"\bStock Results\b", raw_text, re.IGNORECASE
            ) else "DIVIDEND" if re.search(
                r"\bDividend\b", raw_text, re.IGNORECASE
            ) else "OTHER",
            "event_date": active_date.isoformat() if active_date else None,
        })
        structured.append(item)
    return structured


def load_result_dates(path: Path = OUTPUT_FILE) -> dict[str, date]:
    """Load the most recent stock-result date for each exact NSE symbol."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    requested = date.fromisoformat(payload.get("requested_date", date.today().isoformat()))
    records = structure_calendar_rows(payload.get("records", []), requested)
    result: dict[str, date] = {}
    for record in records:
        if record.get("event_type") != "STOCK_RESULTS" or not record.get("event_date"):
            continue
        symbol = str(record.get("symbol") or "").upper()
        if symbol:
            result[symbol] = date.fromisoformat(record["event_date"])
    return result


def result_highlight(symbol: str, as_of: date, result_dates: dict[str, date]) -> dict[str, Any]:
    """Classify a result as declared, due within 15 days, or outside the alert window."""
    result_date = result_dates.get(str(symbol).upper())
    if result_date is None:
        return {"status": "NO RESULT DATE", "date": None, "days": None}
    days = (result_date - as_of).days
    if days < 0:
        status = "RESULT DECLARED"
    elif days <= 15:
        status = "RESULT DUE WITHIN 15 DAYS"
    else:
        status = "RESULT LATER"
    return {"status": status, "date": result_date.isoformat(), "days": days}


def extract_calendar_rows(page: Page) -> list[dict[str, Any]]:
    """
    Extract visible result-calendar entries.

    Sensibull may change its HTML structure, so several selector patterns
    are attempted before falling back to parsing visible text.
    """
    selector_candidates = [
        "[role='row']",
        "table tbody tr",
        "[class*='calendar'] [class*='card']",
        "[class*='result'] [class*='row']",
        "[class*='event']",
    ]

    extracted_rows: list[dict[str, Any]] = []
    seen_text: set[str] = set()

    for selector in selector_candidates:
        elements = page.locator(selector)
        element_count = elements.count()

        for index in range(element_count):
            element = elements.nth(index)
            text = clean_text(element.inner_text())

            if not text or len(text) < 3 or text in seen_text:
                continue

            # Avoid collecting large container elements containing the entire page.
            if len(text) > 500:
                continue

            seen_text.add(text)

            extracted_rows.append(
                {
                    "raw_text": text,
                    "source": "sensibull",
                }
            )

        if extracted_rows:
            LOGGER.info(
                "Extracted %d calendar rows using selector %s",
                len(extracted_rows),
                selector,
            )
            break

    return extracted_rows


def scrape_sensibull_results_calendar(
    headless: bool = True,
) -> dict[str, Any]:
    """Load the JavaScript calendar and return extracted results data."""
    scrape_time = datetime.now().astimezone()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)

        context = browser.new_context(
            viewport={"width": 1440, "height": 1200},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/150.0.0.0 Safari/537.36"
            ),
        )

        page = context.new_page()

        try:
            page.goto(
                SENSIBULL_RESULTS_URL,
                wait_until="domcontentloaded",
                timeout=60_000,
            )

            # Allow API-backed calendar content to load.
            page.wait_for_timeout(8_000)

            try:
                page.wait_for_load_state("networkidle", timeout=20_000)
            except PlaywrightTimeoutError:
                LOGGER.warning(
                    "Network did not become idle; processing visible content."
                )

            rows = structure_calendar_rows(extract_calendar_rows(page), date.today())

            payload = {
                "source": "Sensibull Results Calendar",
                "source_url": SENSIBULL_RESULTS_URL,
                "requested_date": date.today().isoformat(),
                "scraped_at": scrape_time.isoformat(),
                "record_count": len(rows),
                "records": rows,
            }

            return payload

        finally:
            context.close()
            browser.close()


def save_calendar_data(payload: dict[str, Any]) -> Path:
    """Write the extracted calendar data to a JSON cache."""
    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)

    temporary_file = OUTPUT_FILE.with_suffix(".tmp")

    temporary_file.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    temporary_file.replace(OUTPUT_FILE)
    return OUTPUT_FILE


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    try:
        payload = scrape_sensibull_results_calendar()
        saved_path = save_calendar_data(payload)

        LOGGER.info(
            "Saved %d calendar records to %s",
            payload["record_count"],
            saved_path,
        )

    except Exception:
        LOGGER.exception("Sensibull calendar extraction failed.")
        raise


if __name__ == "__main__":
    main()
