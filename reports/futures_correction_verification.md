# Focused Futures correction verification

Implemented the approved correction phase. The scanner remains read-only for broker actions. No order placement, modification, cancellation, conversion or automatic square-off was added or activated. Verification used synthetic broker feeds and existing cached market evidence; no live orders or new historical probability preparation occurred.

## Final validation

**901 test cases and 16 subtests passed: 917 checks total, zero failures, errors or skips.** The final full suite took 136.70 seconds. The previous recorded Report E baseline was 766 passing checks.

| New focused coverage | Tests in final suite |
|---|---:|
| test_futures_sessions | 41 |
| test_futures_trade_ledger | 39 |
| test_futures_read_only_gateway | 13 |
| test_futures_cost_calibration | 22 |
| test_futures_correction_regressions | 36 |

Evidence: [full test log](futures_correction_tests.log), [JUnit results](futures_correction_tests.xml), [final safety integration log](futures_correction_integration.log).

## Preserved strategy and corrected behavior

- LONG barriers: Futures entry × 1.003 / entry × 0.998; SHORT: entry × 0.997 / entry × 1.002. Equity inputs are separately labeled research context. Fresh depth gives a quote estimate, while reconciled opening fills give the actual average entry. No candle close is labeled executable. Mathematical barriers are not submitted order prices.
- Global maximum two executed opening/increasing orders per IST trading day across LONG/SHORT, symbols and expiries. SQLite preserves fills across restarts; partial fills of one order count once, pure exits count zero, reversals count their opening portion. Read-only orders/trades/positions are reconciled before evaluation and before final approval. Ambiguous, stale or missing books remain UNKNOWN; pending orders and overnight exposure are explicit.
- Default entry cutoff 15:15 IST; manual exit warning from 15:10; retained backtest/manual exit deadline 15:20. Cutoff is explicit and configurable. No automatic square-off exists.
- Canonical IST session normalization precedes Futures scoring, indicators and simulations. Final checks require the exact latest completed five-minute bar and current-session continuity. Final quote freshness runs after account reconciliation. Each direction retains its own candle snapshot; a later direction cannot refresh an earlier signal.
- Net reward/risk minimum remains 1.0. Target/stop and all unrelated research thresholds/weights remain unchanged. Dated fees count exchange/IPFT once; a depth-weighted entry embeds entry spread, and future exit half-spread is estimated separately. Residual slippage remains provisional.
- A → B → C → D → E is retained across CLI, API, dashboard and scheduled output. Separate DAILY DISCOVERY and FUTURES EXECUTION provenance and final safety checks are additive. Opposing confirmed Futures signals cannot both be entry READY. D/E reuse the same retained records, with no additional market/account calls.

## Before/after evidence

The preserved synthetic baseline contains four stocks evaluated independently in both directions. All eight technical scores, daily directional scores, company thresholds and final decisions remained unchanged; approved count was zero before and after. Corrected modeled costs/RR and added safety reasons are recorded individually in [fixture comparison](futures_correction_fixture_comparison.json). Fixed Futures levels in that fixture were already on a Futures basis and remain unchanged.

The [cached-data comparison](futures_correction_comparison.md) isolates normalization with identical scoring formulas/weights. It covers 214 cached contracts, 404 valid directional replays and 12,630 excluded session rows. All 32 saved Futures scores matched raw replay; four rounded scores changed after normalization:

| Stock | Direction | Raw score | Corrected score |
|---|---|---:|---:|
| JSWENERGY | SHORT | 69.62 | 65.17 |
| PRESTIGE | LONG | 33.93 | 30.80 |
| PRESTIGE | SHORT | 42.35 | 45.47 |
| TATAPOWER | SHORT | 54.98 | 59.42 |

Timing classifications did not change in this offline replay. Saved actual approvals were zero; corrected actual approvals were **not reevaluated** because fresh depth, news, full mandatory checks and account reconciliation were not rerun. This is not a substitute for a current scan. Exact indicator differences, rejection reasons, normalization exclusions and invalid source fields are in the [comparison JSON](futures_correction_comparison.json).

For the same saved 8 October 15:10 IST candle pair (ICICIBANK Futures ₹1,350.90, equity ₹1,344.90), LONG arithmetic changes from target ₹1,354.9347 / stop ₹1,348.2102 to target ₹1,354.9527 / stop ₹1,348.1982. These are **research arithmetic examples**, not executable prices or actual fills. [Cost audit and official sources](futures_cost_audit.md) document fee assumptions and calibration limits.

## Commands used

```bash
.venv/bin/python -m pytest -q tests/test_futures_sessions.py tests/test_futures_intraday_backtest.py tests/test_futures_execution.py
.venv/bin/python -m pytest -q tests/test_futures_trade_ledger.py tests/test_futures_read_only_gateway.py
.venv/bin/python -m pytest -q tests/test_futures_correction_regressions.py --disable-warnings --junitxml=reports/futures_correction_integration.xml
.venv/bin/python -m pytest -q --disable-warnings --junitxml=reports/futures_correction_tests.xml > reports/futures_correction_tests.log 2>&1
.venv/bin/python scripts/compare_futures_corrections.py
.venv/bin/python scripts/audit_futures_costs.py > reports/futures_cost_calibration.json
```

The comparison and calibration commands use local retained inputs. No FULL_RESEARCH, DAILY_PREP or live order command was run for this correction verification. The synthetic fixture comparison runs `fixture().scan(now=NOW, include_backtest=False)` with mocked broker methods; it does not contact Kite.

## Files in this correction phase

Canonical sessions and indicator inputs:

- `src/futures/sessions.py`
- `src/futures/scoring.py`
- `src/quality/futures_execution.py`
- `src/futures/data_recovery.py`

Futures price basis and operational policy:

- `src/futures/config.py`
- `src/futures/runtime.py`
- `src/futures/costs.py`
- `src/futures/backtest.py`

Read-only account reconciliation and final safety:

- `src/futures/trade_ledger.py`
- `src/futures/gateway.py`
- `src/futures/execution_safety.py`
- `src/futures/scanner.py`
- `src/futures/preparation.py`

Report provenance and evidence versions:

- `src/futures/rejected_analysis.py`
- `src/futures/report_e.py`
- `src/futures/report_e_history.py`
- `src/presenter/futures_opportunities.py`
- `ui_app.py`
- `main.py`
- `scripts/run_futures_mode.py`

Cost sources and offline comparison:

- `resources/futures_fee_schedule.json`
- `scripts/audit_futures_costs.py`
- `scripts/compare_futures_corrections.py`

Documentation:

- `README.md`
- `.env.example`

New regression tests:

- `tests/test_futures_sessions.py`
- `tests/test_futures_trade_ledger.py`
- `tests/test_futures_read_only_gateway.py`
- `tests/test_futures_cost_calibration.py`
- `tests/test_futures_correction_regressions.py`

Updated synthetic fixtures and approved-behavior expectations:

- `tests/futures_mode_fixture.py`
- `tests/test_bidirectional_futures.py`
- `tests/test_futures_research_and_timing.py`
- `tests/test_prepared_futures_modes.py`
- `tests/test_report_e.py`
- `tests/test_fixed_target_stop_report.py`

Preserved before-change evidence:

- `tests/fixtures/futures_correction_before.json`

## Remaining data limits

- Observed slippage and contract-note calibration remain UNKNOWN without observations; residual slippage remains 2 bps per leg, PROVISIONAL.
- Cached-data score replay is not a fresh all-gates scan; actual corrected live approvals were not reevaluated.
- Twelve cached contracts have invalid daily OHLC records; exact dates and fields are retained in comparison evidence. Values were not repaired or invented.
- Read-only reconciliation can block scanner approvals after two executions, but cannot prevent an external/manual third trade or close positions.
- No real-data historical backtest/preparation or win-probability publication was performed. Existing incompatible caches require corrected-version regeneration after validation.
- Historical technical simulations remain independent by direction, not a calibrated portfolio simulation of the two-entry manual strategy and missing point-in-time research/depth gates.

All existing saved execution reports and baseline fixtures remain available. Unrelated workspace changes, including current account configuration and concurrently generated reports, were not reverted.
