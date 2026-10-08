# Futures pipeline recovery — 8 October 2026

The 20:21 IST reference execution read existing provider caches and retained raw research facts. The 22:20 IST LIVE_SCAN execution read a separate prepared database containing only one universe record. No prepared candles or company records had ever been seeded. This was a cache handoff failure, not expiry or deleted fundamentals.

Google News and Kite DNS failed under the restricted execution environment. Read-only connectivity checks with permitted networking succeeded (Google News HTTP 200). No code bypasses network restrictions. Source failures are now recorded with their type/detail/category and credentials redacted.

## Evidence

| Execution | Universe | Rankable stock scores | Raw company facts | Cache/mode |
|---|---:|---:|---:|---|
| 20:21 IST reference | 214 | 213 in both directions | 214 | Native Kite Parquet/public-source caches |
| 22:20 IST LIVE_SCAN | 214 | 0 | 0 | Prepared SQLite had universe only |
| 2026-10-08T22:52:52.979016+05:30 | 214 | 213 in both directions | 214 | AFTER_MARKET_RESEARCH |

Initial recovery imported 445 existing candle datasets, 214 company snapshots and 214 cached news records, preserving original dates. It fetched 267 missing historical datasets through the paced Kite gateway. It did not run historical backtests.

Final execution: 182.38s; {'quote': 1, 'history': 213, 'margin': 10}. No existing data needed reimporting. The 213 historical requests updated missing same-session equity tails with one overlapping bar; full histories were reused. Report D made zero additional requests.

## Commands executed

```bash
.venv/bin/python main.py futures-scan --mode AFTER_MARKET_RESEARCH --limit 5 --json > reports/futures_after_market_2026-10-08.json 2> /tmp/futures_after_market_2026-10-08.log
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest tests/test_futures_data_recovery.py tests/test_fixed_target_stop_report.py tests/test_prepared_futures_modes.py -q
```

The market-data scan and one-symbol gap diagnostics ran with permitted read-only network access. Full suite: 703 passed, 16 subtests passed, 104.61s. Final recovery/Report D/preparation suite: 40 passed (includes forced-approval protection and cached-pending-news preparation regressions). `git diff --check` passed.

## Files changed in this repair

- `src/futures/data_recovery.py`
- `src/futures/cache.py`
- `src/futures/runtime.py`
- `src/futures/preparation.py`
- `src/futures/scanner.py`
- `src/futures/live_news.py`
- `src/data_provider/kite_data_provider.py`
- `src/quality/public_fundamentals.py`
- `src/news/analysis_service.py`
- `src/presenter/futures_opportunities.py`
- `main.py`
- `ui_app.py`
- `scripts/run_futures_mode.py`
- `tests/test_futures_data_recovery.py`
- `tests/test_prepared_futures_modes.py`
- `README.md`
- `.env.example`

## Remaining unavailable information

- NIFTYFPI has no NSE equity instrument-token mapping. It remains in the configured universe and audit; it is excluded from scored top lists.
- For the 213 equity histories, the latest five-minute candle is 15:10 IST. Missing bars: 15:15, 15:20 and 15:25 IST. A FEDERALBNK tail request through both 15:30 and current wall time returned only 15:10, confirming a feed availability issue. VWAP freshness is not waived.
- 80 symbols have analysed news, 42 have no relevant news, and 92 have collected headlines awaiting semantic analysis after the configured 60-second analysis budget. These stay UNKNOWN for mandatory news analysis and are persisted for later preparation.
- Existing unsupported/missing sector PE, promoter data, commentary and sector metrics remain UNKNOWN per candidate. Existing threshold failures and banks/NBFC policy flags remain intact.
- Live Futures entry, target, stop-loss, execution costs and reward/risk for displayed candidates are unverified. Completed-candle reference prices are explicitly labelled research references. No replacement plans or success probabilities were invented.
- Historical backtests remain available in the existing weekly/daily modes. Unavailable or incompatible prepared backtest results stay UNKNOWN; no old probability is imported under different assumptions.

## Output order

Report A → Report B → Report C → Report D, automatically. The complete detailed output is `reports/futures_after_market_2026-10-08.md`, and the full audit is the same basename with `.json`.

All after-market candidates are RESEARCH ONLY. Technical weights, required research thresholds, 0.3% target, 0.2% stop-loss, costs and live approval checks remain unchanged. Daily versus completed Futures five-minute scoring bases are displayed. After-market ranking uses unchanged technical scores; it does not turn TOO LATE or UNKNOWN records into actionable trades.

## Report D

{
  "TARGET_SL_ONLY": 0,
  "TARGET_SL_PLUS_OTHER_FAILURES": 0,
  "TARGET_SL_PLUS_UNKNOWN": 83
}

All 83 entries remain REJECTED / RESEARCH ONLY and include their original scores, exact recorded reasons, remaining PASS/FAIL/UNKNOWN checks, and missing details. These records are derived solely from the final scan, including candidates outside its displayed top five.