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

            rows = extract_calendar_rows(page)

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
