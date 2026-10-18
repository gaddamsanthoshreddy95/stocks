# Entry Quality / Late Entry audit and proposed implementation

Audit date: 17 October 2026 IST. Current state: ENFORCE activated after the user requested continuation of the reviewed implementation. The findings below document the pre-activation review.

## Findings before production changes

1. `src/futures/scoring.py` scores both directions and has move/ATR/EMA/VWAP timing checks. The move ceiling defaults to 2%, VWAP extension to 1%, and EMA extension to 1.5 ATR. These are not calibrated to a 0.3% target / 0.2% stop. A 1% move is evidence, not sufficient reason to reject. Existing scoring must remain authoritative and unchanged.
2. `scanner._execution` validates actual-contract depth, spread, slippage, costs, target space, confirmations and research. Recent LONG/SHORT gates require completed five-minute retests. The separate fifteen-minute retracement request was not yet implemented. This proposal includes a common completed-15-minute trend/retest assessment, without activating it silently.
3. Final approval is centralized in `execution_safety.finalize`; preparation and selected-membership workflows may withdraw approval again. Entry quality must run at these final boundaries, never upgrade a failed gate, and record whether its own evidence predates a later withdrawal.
4. Research A/B rows, weekly memberships, Reports D/E scenarios and archived approvals can be mistaken for current entry permission. New advisory labels must distinguish proposed entry quality from actual approval. Historical viewing must never fetch prices or rewrite stored recommendations.
5. WAIT is presently a decision, not a monitored broker order. Reassessment must happen during the next explicitly requested scan, using new quotes/candles/news. Reusing a previous WAIT or old news snapshot cannot authorize entry. No background or full-universe recovery scan will be introduced.
6. Scores/ranks, weekly lists, event/news policy, ledger, two-entry cap and fixed exit policy must be unchanged in REPORT_ONLY. In ENFORCE, the new framework may only withdraw approval before shortlist selection; remaining eligible candidates naturally replace withheld ones without an additional scan.
7. Existing technical backtests explicitly lack reconstructed news/OI/depth. Local `ft_jobs` contains zero jobs. Seven saved Futures-related report files contain 2,180 candidate records and zero Entry Quality input snapshots. The prepared cache has historical candles and company/news records, but no archived decision-time bid/ask/depth quotes for the requested slots; later cached news is not substituted for earlier decisions. Saved October 8/9 reports lack complete decision-time evidence at all three requested times. Real paired 09:30/09:45/10:00 full-policy performance therefore cannot be claimed from those reports. Offline replay must record unavailable samples, costs, slippage, opportunity exclusions and MAE, rather than fill missing context with later data.
8. Broker actions are read-only. Software cannot guarantee a manually held position is flat at 15:10 or force an actual stop without automated orders. Preserve the entry gate, visible manual exit deadline and reconciliation; never claim automatic square-off.

9. Economic blocker found during regression verification: using the project's existing default cost model on 16 October 2026, a synthetic ₹101 entry / 100-unit contract gives net R:R about 0.36 despite gross 1.5. A 10,000-unit example is still about 0.66. The 0.3% target, 0.2% stop and required net R:R ≥1 can therefore reject otherwise strong setups before this framework. This is an existing policy/cost-model interaction, not a reason to suppress costs or loosen safeguards. Test positive-path fixtures explicitly use a synthetic no-cost model to isolate timing; a separate real-cost regression requires SKIP. No real-cost profitability claim is made.

## Workflow inventory and coverage

| Workflow / module | Role | Integration |
|---|---|---|
| `ui_app.py` Futures LONG/SHORT, `src/ui/futures_history.py` | manual scan, saved history | shared scanner + additive saved fields; historical snapshots unchanged |
| `src/futures_workspace/service.py::scan_daily`, isolated daily CLI | selected weekly members | same scanner; refresh advisory after membership/conflict withdrawals |
| AI-Stock, Power Stocks, Reality, Defence India (`src/ui/*stock*.py`, `defence_india.py`) | fixed-universe scans | same scanner and common table columns |
| `TradingPlatform.scan_futures_opportunities`, main CLI, `scripts/run_futures_mode.py` | programmatic/manual modes | same scanner |
| REST `/futures-opportunities`, `/futures-trading/scan-selected` | API scan entry points | same scanner; additive JSON assessment |
| `preparation.PreparedScanner`: FULL_RESEARCH, DAILY_PREP, LIVE_SCAN, AFTER_MARKET_RESEARCH | cached/incremental modes | shared finalizer; research-only never ENTER |
| Weekly Rotation, selected rechecks, universe refresh, Recovering/Shorting memberships | discovery, ranking, admission | unchanged; membership is not intraday approval; daily context shows saved advisory only |
| Reports A/B/C | ranked research / approved shortlist | preserve scores/order in report-only; C can be filtered only in explicit enforcement mode |
| Reports D/E, `rejected_analysis`, `report_e`, presenters | rejected/conditional evidence and scenarios | additive advisory; existing calculations and hypothetical scenarios unchanged |
| `entry_validity`, shared tables, Markdown presenter | display saved data | display recorded proposal/mode/time; no current-price recomputation |
| `FuturesIntradayBacktester`, workspace performance/research replay, offline comparison scripts | hypothetical historical research | legacy results unchanged; separate Entry Quality comparator labels scope and missing evidence |
| Scheduler / Task Scheduler compatibility CLI | automatic jobs disabled | remains manual; externally invoked daily CLI uses same framework |
| `/suggestions`, `suggest_futures`, daily-report/dashboard/opportunities | legacy daily/equity/option selection | mark Futures research as not evaluated by intraday entry framework; no inferred ENTER or new market reads |
| `analyze`, AI analyst, trade-plan/decision/evaluation workflows | equity/option research; advisory displays | not exact-contract Futures entry approval; preserve existing engines; scope documented, no substitution of spot prices |
| `/papertrade`, paper broker, positions/outcomes, options/short-put workflows | simulation / different instruments / accounting | outside fixed intraday Futures execution approval; unchanged; no broker mutation introduced |

Search covered `src`, `ui_app.py`, `main.py`, scripts, API, presenters, workflow and trading-engine modules; notebook checkpoints and backups are not executable integration targets.

## Proposed common policy and expected impact

A pure evaluator consumes existing decision-time evidence, never fetches data. It records Futures moves from previous close/open, opening range, stock/exact-contract VWAP, relative volume, benchmark/sector returns, intraday true range and session range, recent support/resistance, retest/trigger distance, depth-weighted entry, slippage and the existing costed plan. Missing inputs stay missing. Exact-contract economics are never calculated from stock prices.

ENTER requires renewed directional confirmation, verified clear news, current quotes, adequate target room after cost buffer, valid fixed stop/structural invalidation, sufficient volume/relative strength and existing final approval. WAIT FOR PULLBACK indicates a valid directional idea whose current extension, trigger/structure or remaining room needs improvement. SKIP means unverified data, hard conflicts, invalid economics, closed entry window or failed mandatory safeguards. Suggested zones use observed Futures VWAP/retest levels and are conditional research zones, not limit orders or guaranteed fills.

Completed 15-minute candles are derived from exactly three completed five-minute candles, anchored at 09:15 IST. Both stock and exact-contract Futures require a prior-range break and subsequent completed directional retest, plus VWAP/trend alignment. Default two prior bars + break + retest means earliest confirmation at 10:15 IST. This is intentionally strict and will miss early opportunities; its effect must be measured before activation. No 09:30/09:45/10:00 entry can pass that proposed requirement with only current-session bars.

A move of 1% by itself never triggers SKIP. Continuation can pass when near a fresh valid trigger with supporting structure/flow and cost-adjusted space. Extensions are measured relative to the fixed stop and intraday range. Thresholds are configurable hypotheses, not measured probabilities.

Activation: `FUTURES_ENTRY_QUALITY_MODE=ENFORCE` is now the default and local setting. `REPORT_ONLY` remains an explicit diagnostic override. Report-only appends evidence, classifications and a comparison summary, preserving every original decision/rank/list. Enforcement can only downgrade; it never overrules existing news/ledger/quality gates. Waiting candidates are re-evaluated on the next manual scan with freshly captured inputs; it creates no persistent auto-run queue.


## Concrete implementation and validation artifacts

- `src/futures/entry_quality.py`: one versioned snapshot builder, symmetric evaluator, report-only annotation and explicit opt-in withdraw-only hook. `execution_safety.finalize` is the shared integration point; preparation runs it again after its own withdrawals, and workspace membership checks refresh only that new report's assessment.
- `src/futures/retracement_15m.py`: common completed-candle trend/retest context; no broker or network reads.
- `src/futures/entry_quality_backtest.py`: paired timing comparison from recorded evidence; separate counters for unavailable inputs, missed target opportunities and avoided stops; net P&L, MAE, transaction costs and slippage through the existing backtester/cost model.
- `scripts/audit_entry_quality.py`: offline saved-report coverage audit; optional `--bundle` accepts `samples` plus exact-contract `histories` rows with ISO `timestamp`, `tradingsymbol`, and OHLCV columns. No market data is downloaded. Output is a new artifact, never an edit to a source report.
- `reports/entry_quality_validation.json`: actual local archive coverage result and original source hashes. Status is insufficient point-in-time evidence, not a claimed profitable backtest.
- Tables/Markdown/API responses display additive proposal, mode, reasons, conditional zone, assessed time and fifteen-minute state. Scores and original report fields remain authoritative in REPORT_ONLY.

Run offline coverage using `.venv/bin/python scripts/audit_entry_quality.py` (Windows: `.\.venv\Scripts\python.exe scripts\audit_entry_quality.py`). Run a recorded replay using the same command with `--bundle <export.json> --output <new-result.json>`.

The new simulation option uses the recorded depth-weighted decision reference, never changes OHLC, and explicitly labels this an estimated entry rather than a broker fill. Original technical backtests retain their default next-bar-open behavior. Outcome calculations include dated model costs and recorded exit spread; absent spread or unverified expiry prevents a paired result. Independent timing counterfactuals are not a portfolio authorization to exceed the two-entry live ledger limit.

The paired comparator also reports a separate portfolio view for each alternative entry time, choosing by the original recorded ranking and limiting each day to two entries. Conflicting directions in one symbol are excluded; unrecorded ranks do not become portfolio trades. This does not combine the three timing alternatives into six daily entries.

Validation: the broad regression run passed 489 tests; the final focused run passed 51 tests, and the additional Entry Quality/news-reversal run passed 78 tests. These cover both directions, 15-minute aggregation, stale/missing/future evidence, cost/room/slippage/structure checks, renewed WAIT evaluation, report-only invariants, opt-in exclusion/replacement, archive immutability, replay leakage, portfolio caps, manual UI, CLI/API/Reports A–E, selected-only scope, prepared modes, themes and read-only broker controls. This is software validation, not a measured trading success rate.

Activation does not establish a trading success rate. The default completed 15-minute policy still prevents entries before 10:15 IST. Actual historical paired validation remains unavailable for the archived inputs. No scan is launched by activation.


## Enforcement activation follow-up

The reviewed gate is enabled by default and in local configuration. Old report-only archives retain their recorded mode and assessments. New table/presenter labels identify enforcement. Long/short research ordering uses the original pre-gate decision priority and unchanged scores, while only surviving approved candidates populate Report C. Repeated finalization retains the pre-gate evidence and withdrawal count, never promotes a withheld decision, and respects later ledger/safety failures. Withheld entry-state labels are cleared alongside approval flags.

The common gate rechecks news conflicts using the already collected news and completed stock candles even when the legacy news-gate toggle is off. It preserves existing news rejections, requires verified collection/materiality and market confirmation, and does not fetch additional data or treat unavailable news as neutral. Both report-only comparison and enforcement use this same evaluation.

Validation separates explicit REPORT_ONLY legacy compatibility from default-ENFORCE activation tests. Synthetic fixtures isolate policy logic; actual costs and missing historical evidence remain visible safeguards. Restart an already running UI to load the new setting, then initiate scans manually. No live market scan was launched during activation.

Post-activation verification: 101 focused enforcement/entry-quality/news/table checks passed, and 179 affected pipeline integration checks passed. The broader 530-test compatibility run used an explicit REPORT_ONLY override for legacy expectations; activation tests independently removed that override to verify the new ENFORCE default. Historical source hashes were verified unchanged.
