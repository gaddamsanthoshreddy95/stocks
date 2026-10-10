# Futures Trading: implementation and operating guide

## Verification and hardening on 10 October 2026

The requested workspace was already present in the clean repository at the start
of this request. It was inspected and retained, rather than recreated. This
attempt changes only workspace code, its configuration example, tests and this
guide. It adds no new database migration or API route; the additive `ft_*` schema
and routes documented below remain authoritative.

Changes in this attempt:

- `discovery.py` rejects an internal missing session and OHLC values outside the
  recorded high/low range. A covering, valid Nifty history supplies observed
  market sessions; without it, the existing `MARKET_HOLIDAYS_IST` calendar applies.
  Configure actual exchange holidays for the history period when the benchmark
  is unavailable. Missing sessions yield UNKNOWN_DATA rather than invented bars.
- `service.py` finishes eligibility audit persistence and provider cleanup before
  publishing a rotation. Failure in either preserves the preceding watchlist
  version, marks the job FAILED and releases its lease.
- `config.py`, `bias.py`, `adapter.py` and `ui.py` expose validated directional and
  strong Market Bias thresholds, defaulting to 20 and 60. Set
  `FUTURES_WORKSPACE_BIAS_DIRECTIONAL_THRESHOLD` and
  `FUTURES_WORKSPACE_BIAS_STRONG_THRESHOLD`, or save them in workspace Settings.
  They require `0 < directional < strong <= 100`. Adjustments remain REPORT_ONLY.
  Invalid settings display an error and do not replace saved settings.
- `.env.example` documents both settings. `tests/test_futures_workspace.py` adds
  ten offline cases for late rotation failure, internal gaps, observed holidays,
  malformed OHLC and threshold configuration.

No existing LONG/SHORT scoring, Reports A–E, target/stop settings, execution ledger,
broker order behavior or legacy session settings were changed. Workspace entry
and manual-close deadlines remain 15:00 and 15:10 IST respectively.

Kite metadata and quote assumptions were checked against the official
[instrument and quote API documentation](https://kite.trade/docs/connect/v3/market-quotes/).
This was a documentation check, not a live broker verification.

Current verification results are recorded at the end of this guide. Full live
integration activation, a machine-level scheduled task, linked manual-fill
performance and point-in-time fundamental/news ablation remain external or
unvalidated dependencies, as detailed below.

## Architecture findings

The existing UI is Streamlit (`ui_app.py`), its report store is SQLite
(`src/ui/database.py`), and the optional API is FastAPI (`src/api.py`). There is
no general scheduler or migration framework. The established daily Futures
scanner already provides independent directional scores, company research,
news/event checks, Futures liquidity/OI/VWAP/depth validation, session controls,
read-only account reconciliation, and Reports A–E. The execution ledger lives
separately at `data/futures_executions.sqlite3`. The repository was clean at the
start of this implementation.

The new `src/futures_workspace` package orchestrates that scanner and adds
weekly research, persistent directional membership, immutable versions, job
leases, scheduling and eight UI sections. Existing daily weights and approved
0.30% target / 0.20% stop remain authoritative. Existing scanner deadlines
remain 15:15 entry / 15:20 exit; this workspace uses 15:00 / 15:10. Neither
workflow can guarantee that manual positions close.

## Files and integration points

Added:

- `src/futures_workspace/config.py`: independent settings and validation.
- `src/futures_workspace/store.py`: additive schema, atomic versions, audit,
  settings, job status/leases and recovery.
- `src/futures_workspace/discovery.py`: metadata eligibility, completed-session
  indicators, bearish/recovery classification, fundamental interpretation and
  experimental discovery components.
- `src/futures_workspace/adapter.py`: existing Kite/Yahoo/fundamentals/news/events
  integration; shared paced contract histories and batched execution quotes.
- `src/futures_workspace/bias.py`: experimental report-only market classification.
- `src/futures_workspace/service.py`: weekly rotation, recheck, selected daily
  scan, manual management, performance summaries and replay persistence.
- `src/futures_workspace/scheduler.py`: separate universe and weekly schedule keys.
- `src/futures_workspace/performance.py`: offline point-in-time research replay.
- `src/futures_workspace/ui.py`: all eight requested sections and background jobs.
- `scripts/run_futures_workspace.py`, `run_futures_workspace_schedule.bat`:
  scheduler and operational entry points.
- `tests/test_futures_workspace.py`: deterministic integration and UI checks.

Modified: `src/futures/scanner.py` (optional explicit selected-symbol input),
`src/api.py` (additive routes), `ui_app.py` (feature-gated navigation),
`.env.example`, and `README.md`. Existing scanner calls without the new argument
still resolve their original universe. Prepared full-universe modes reject an
explicit selected-symbol input, preventing accidental discovery from daily scans.

## Database migration and audit

`WorkspaceStore` uses the existing UI database by default. First use creates
only new `ft_*` tables and indexes with `CREATE TABLE IF NOT EXISTS`; existing
report/watchlist tables and the Futures execution ledger are untouched.

| Table | Stored information |
|---|---|
| `ft_securities` | Canonical `NSE:<symbol>` identity and instrument evidence |
| `ft_memberships` | Category, enabled/pinned/status, timestamps, weekly evidence |
| `ft_versions` | Immutable membership snapshots, previous/new version, changes |
| `ft_jobs` | Kind, schedule key, running/completed/incomplete/failed, result/error |
| `ft_lease` | One cross-process workspace writer/scan lease |
| `ft_evidence` | Universe, discovery, fundamental, news/event, bias and replay snapshots |
| `ft_settings` | Workspace-specific validated overrides |

A primary key on membership symbol prevents contradictory active categories.
Both categories are independent persisted memberships, not visual filters of
one directional list. A single SQLite transaction publishes all changes and the
new version. Failed transactions retain the prior membership. Evidence rows
remain available even when a rotation is incomplete. Restore publishes a new
version and marks restored members REVIEW_REQUIRED until rechecked. It never
rewrites broker trades or execution counts. JSON exports include complete
retained evidence and previous/current membership snapshots.

## Full-universe discovery and weekly rotation

Production refresh retrieves the complete Kite NFO and NSE instrument masters.
Stock Futures names must map to NSE-segment EQ underlying securities; index
instruments are excluded. Contracts must have the correct exchange/segment/type,
positive token/lot size, a symbol and an unexpired expiry. The nearest valid
contract supplies mapping, expiry and lot size. No permanent stock list is used.
Kite quotes/depth validate availability, volume and spread; historical quality is
validated per security. Metadata eligibility count is distinct from the count
with sufficient research data.

Default adjusted equity history comes from the existing YahooProvider with
`auto_adjust=True`, two-year daily history and a bounded local cache. Raw Kite
candles are never appended to that adjusted series. Benchmark/sector index data
comes from the existing index provider. Completed-session normalization removes
live/incomplete days and holidays configured by `MARKET_HOLIDAYS_IST`. Missing
sessions, duplicate timestamps, invalid OHLCV and stale history fail closed.
Changing `weekly_history_source` to `kite` requires adjustment provenance or an
explicit technical-only unadjusted-history configuration.

Indicators: 5/20/21/63/126-session returns, 252-session high drawdown, six-month
low distance, SMA20/50/200, RSI, MACD/signal, ATR, trend slope, higher/lower
high-low structure, support/resistance, breakout/breakdown, volume trend,
relative volume, volatility, and aligned Nifty/sector excess returns. Unknown
relative inputs stay unknown; they never become observed neutral values.

Bearish discovery combines recent negative returns, weak structure, moving
averages, momentum, breakdown and relative weakness. A negative six-month return
is not required. Recovery requires a configurable prior decline/drawdown and
multi-session price/MACD confirmation, higher highs/lows and improving momentum.
States include RECOVERY_WATCH, RECOVERY_CONFIRMED and RECOVERY_INVALIDATED;
one bullish candle cannot admit a confirmed recovery.

The experimental 70/20/10 discovery components show availability and coverage.
Missing components are omitted from the observed-weight denominator, rather
than treated as measured zero. Technical score controls ranking by default.
Fundamental and news effects are context until independent validation; weekly
scores remain separate from daily strategy scores.

Each rotation evaluates every eligible name, independently ranks the two lists,
then automatically adds, removes or transfers supported classifications. It
respects pinned/disabled members, multi-session confirmation, capacity and
hysteresis. Pinned contradictory members require review. Unknown/contradictory
information preserves membership with REVIEW_REQUIRED when a partial valid
rotation is published. If every evaluation is unavailable, no membership version
is published and the attempt is INCOMPLETE. Selected-only recheck cannot admit
new universe securities. Restored/manually added members require recheck.

## Fundamentals, news and Market Bias

The existing public fundamentals provider supplies source/reporting/evidence
snapshots. Weekly interpretation uses available revenue/profit growth as
business-direction context. Financial sectors use profitability context without
industrial debt/cash-flow screening; unavailable asset quality/capital/funding
metrics stay UNKNOWN. Full provider snapshots retain other available debt,
ownership, pledge, valuation, cash-flow and commentary fields. A snapshot without
source, dated reporting evidence or fresh observations cannot supply a component
score. Publication dates missing from the source remain explicitly UNKNOWN.

Existing news analysis and EventRiskService are reused and stored independently.
Model headline sentiment is not promoted to a verified directional corporate
disclosure. Confirmed major event hard blocks mark discovery for review. Missing
news or financial statements permit explicitly labeled technical-only research;
all mandatory existing daily company/news gates still apply before execution
approval. No unsupported directional news score is fabricated.

Market Bias supports the requested six classifications and -100..100 bands.
The production adapter obtains completed Nifty/Bank/Midcap trends, Nifty VWAP
only if actual volume exists, and participation across available sector indices.
Breadth/opening range/relative-volume interfaces remain unknown unless sourced.
Nifty trend plus sufficient independent participation/coverage are required;
otherwise the classification is UNKNOWN. Sector observations are retained
independently. Direction adjustments and counter-trend flags are report-only:
original daily scores and mandatory risk decisions remain unchanged.

## Daily Trading

SCAN SELECTED STOCKS loads only enabled ACTIVE membership, deduplicates symbols,
and calls the existing scanner once with explicit symbols. It evaluates both
directions for conflict safety and presents SHORT only for bearish membership
and LONG only for recovering membership. Opposite approved directions cause a
CONFLICT outcome. Membership alone cannot approve an entry. Nontrade/rejected,
unknown, missing results and expired weekly membership are displayed explicitly.

Daily data is scoped to the job. Contract daily/intraday histories are retrieved
once and shared across directions; execution quotes are batched after history
fetches and refreshed if they age. Annual/index reads are shared and session
bars have a short reuse TTL. KiteGateway enforces bounded retries, timeouts,
quote/history pacing and batching. Work is serialized (one worker), keeping
concurrency bounded. Quotes/candles may still expire during a slow scan; the
original final checks withdraw approval rather than relaxing freshness.

Existing liquidity, OI, research, exact expiry, ledger reconciliation, duplicate
entries, two-entry limit, exposure, completed candles and session rules remain
in force. Reports A–E are retained inside the combined result. No broker BUY,
SELL, modify, cancel or square-off call was introduced. Results are immutable
historical snapshots, not continuously renewed approvals.

## Windows launch and operation

From your project folder, after installing dependencies and adding valid Kite
credentials to `.env`:

```env
MARKET_DATA_SOURCE=kite
FUTURES_WORKSPACE_ENABLED=true
```

```cmd
run_ui.bat
```

Open http://localhost:8501, choose Futures Trading, then Weekly Rotation. First
visit also schedules initialization automatically. Once lists contain ACTIVE
members, Daily Trading's single SCAN SELECTED STOCKS button generates the combined
result. Independent watchlist pages provide add/remove, enable/disable and
pin/unpin; Rotation History restores versions and exports audit JSON.

CLI commands from the project folder:

```cmd
.venv\Scripts\python.exe scripts\run_futures_workspace.py rotate
.venv\Scripts\python.exe scripts\run_futures_workspace.py recheck
.venv\Scripts\python.exe scripts\run_futures_workspace.py refresh-universe
.venv\Scripts\python.exe scripts\run_futures_workspace.py daily
.venv\Scripts\python.exe scripts\run_futures_workspace.py status
.venv\Scripts\python.exe scripts\run_futures_workspace.py schedule
```

Default weekly schedule: Saturday 09:00 Asia/Kolkata. The UI checks scheduled
maintenance every five minutes while this workspace is open. Use Windows Task
Scheduler to run the absolute path to `run_futures_workspace_schedule.bat` daily
at 09:00 IST when the UI is closed. The wrapper sets the project working directory.
The schedule command runs a distinct daily instrument refresh, then the latest
due weekly rotation. Successful weekly keys are idempotent; incomplete attempts
can retry. It does not schedule daily recommendations. No machine-level task was
installed on the user's laptop from this remote workspace.

Job leases persist through crashes, conservatively preventing another writer.
If a worker crashes, first stop that worker/process, inspect `status`, then run:

```cmd
.venv\Scripts\python.exe scripts\run_futures_workspace.py recover-job --job-id JOB_ID --worker-stopped
```

This recovers only the abandoned job lease and preserves committed versions.
Do not use it while the original worker is running.

## Configuration

Every `WorkspaceConfig` field accepts `FUTURES_WORKSPACE_<UPPERCASE_FIELD>`.
`.env.example` lists the defaults. UI Settings saves separate SQLite overrides
for discovery thresholds, schedule and weekly history source. Feature enablement
uses the environment flag; daily strategy weights are never edited here.

Optional `FUTURES_WORKSPACE_ADJUSTMENT_MANIFEST` supports per-symbol verified
Kite history provenance, e.g. an entry containing `source`, `basis` (`ADJUSTED`
or `CORPORATE_ACTION_VALIDATED`), `validated_at`, `from` and `through`. It must be
fresh (seven days), cover the complete supplied history and have no future
validation timestamp. It asserts evidence supplied by the operator; it does not
perform adjustment itself. Yahoo adjusted history needs no manual stock list or
manifest. Credentials remain in the existing environment configuration.

## New API routes

| Method | Route | Operation |
|---|---|---|
| GET | `/futures-trading` | Memberships, persisted jobs, performance |
| POST | `/futures-trading/universe-refresh` | Refresh full stock-Futures metadata |
| POST | `/futures-trading/rotate` | Full-universe automatic rotation |
| POST | `/futures-trading/recheck` | Existing memberships only |
| POST | `/futures-trading/scan-selected` | One combined selected-stock scan |
| POST | `/futures-trading/membership` | `{symbol, action, category}` |
| GET | `/futures-trading/versions` | Immutable versions |
| POST | `/futures-trading/versions/{version}/restore` | New restore version |
| POST | `/futures-trading/research-replay` | Offline archived research replay |

Disabled workspaces reject operations before market reads. Concurrent jobs return
conflict errors. Existing API launch remains `python -m uvicorn src.api:app --reload`.

## Performance and backtesting

Analysis & Performance shows persisted signal counts, direction/sector counts,
rotation turnover and audit history. Win rate, profit factor, expectancy and
drawdown remain UNKNOWN without outcome evidence; they are not inferred from
recommendations. The page accepts archived research JSON, and the API exposes the
same replay service. Input fields are:

- `universe_snapshots`: `{as_of, recorded_at, source, complete_universe,
  instruments, sectors}`; archive must have been recorded no later than as_of.
- `equity_histories`: keyed by canonical stock symbol; each value contains
  `{price_adjustment, rows}`, with dated OHLCV rows.
- `futures_histories`: keyed by exact Futures trading symbol, with dated OHLCV/OI
  rows in the same format.

Replay uses completed equity prefixes and archived eligibility, then existing
Futures technical signals, exact-contract prices, costs/slippage, next-bar entry
and 15:10 forced-exit assumptions. Only out-of-sample simulation trades contribute
to outcome summaries; insufficient samples do not supply a claimed win rate or
future probability. It reports average win/loss, profit factor, net expectancy,
drawdown, sector grouping and failed-recovery/false-bearish selection diagnostics
where supplied data permits. Costs remain PROVISIONAL. Five-session underlying
selection returns are explicitly separate from Futures P&L.

Limitations: this replay is technical research, not validation of every live
mandatory gate, portfolio two-entry constraints, complete weekly hysteresis
policy, or historical news/fundamental/bias effects. Archived full universes and
publication-time corporate-action adjustment provenance must be independently
verified to avoid survivorship/look-ahead bias. Contribution/ablation claims stay
UNKNOWN pending such evidence. No live calibration or claimed profitability was
performed.

## Verification and external dependencies

Baseline: 127 tests passed covering existing UI database, Futures execution,
ledger and correction regressions. The full repository regression run passed 958 tests and 16 subtests.
The final workspace test run passed 58 tests, including the expiry-day closing
boundary check added after that regression run. Compilation, CLI help and
whitespace checks also passed.

Offline checks cover actual technical classification and recovery invalidation,
full metadata validation, independent initialization, additions/removals/
transfers, pins/hysteresis, incomplete data, atomic failure, version restore,
manual membership, job idempotency/concurrency/recovery, selected-only scanner
calls, shared history and quote batching, nontrade/unknown/conflict outcomes,
Market Bias bands, sector divergence, deadlines/ledger preservation, schema
compatibility, disabled behavior, adjusted-provider reuse and all eight Streamlit
sections. Tests do not require live broker credentials.

External dependencies not activated or verified live here: current Kite
credentials/instrument/quote/history/account services, Yahoo availability,
public company data and news feeds. Existing providers may return incomplete
financial fields, sector mappings or event coverage. The interfaces record those
limitations rather than invent observations. Scheduling while the UI is closed
requires the local task described above. Full-universe research can be slow
because public-source access and Kite requests are bounded and paced.

### Current attempt results — 10 October 2026

The final fresh-process `.venv/bin/python -m pytest -q` run passed **969 tests
and 16 subtests** in 137.90 seconds, including 68 workspace cases and the eight
Streamlit sections. Compilation, CLI help and `git diff --check` also passed.
The initial full-suite attempt passed 958 tests but failed one UI test because
its process had imported the pre-edit config class before loading the edited UI;
the fresh final run above resolved that mixed-version failure. No live broker
credentials were required by the deterministic workspace tests.

Test-generated tracked cache/report changes and the new recommendation artifact
were removed after verification. The final changes contain only the workspace
hardening, configuration example, offline tests and this operating guide.

Linux launch from the repository root:

```bash
.venv/bin/python -m streamlit run ui_app.py
```

Use the existing credential configuration (`KITE_API_KEY`, `KITE_ACCESS_TOKEN`),
`MARKET_DATA_SOURCE=kite`, and `FUTURES_WORKSPACE_ENABLED=true`. Refresh expired
broker authorization through the established login flow. Open Futures Trading,
let first-run initialization finish or run Weekly Rotation, then use Daily
Trading's **SCAN SELECTED STOCKS** for the combined selected-only result.
For maintenance while the UI is closed, schedule
`.venv/bin/python scripts/run_futures_workspace.py schedule` through your existing
host scheduler, using Asia/Kolkata. The configured weekly default remains
Saturday 09:00; the command never schedules daily recommendations or broker orders.

### Workspace appears stuck or scan buttons stay disabled

Scheduled maintenance now reports instrument refresh, market history, and each
stock's history, fundamentals and news stages. The UI displays elapsed time and
warns if no stage update arrives for two minutes. This warning does not declare
the worker dead or release its lease. A completed or failed background job triggers
a full-page refresh so controls outside the status fragment become available;
failure details remain visible. Previously, scheduled jobs supplied no progress
callback and fragment refreshes could leave the scan controls stale.

After updating these files, restart Streamlit to replace its cached controller.
If a stage remains stuck, inspect the terminal and persisted CLI `status` before
retrying. For an abandoned persisted lease, stop the original worker first and
use the explicit `recover-job` procedure above. Never recover a live worker's lease.
The follow-up progress/UI fix passed 71 workspace and background-job tests,
including stalled-status messaging, failure display, button re-enablement,
scheduled stage updates, and the existing eight-section UI regression.
