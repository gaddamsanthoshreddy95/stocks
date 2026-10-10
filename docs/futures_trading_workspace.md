# Futures Trading: implementation and operating guide

**Current operating mode: manual only.** Opening the workspace never starts a
scan. Use **Weekly Rotation → Run Full Universe Scan**, **Recheck Selected
Watchlists**, or **Daily Trading → SCAN SELECTED STOCKS** explicitly. The old
`schedule` CLI command now returns MANUAL_MODE without constructing market-data
services. This supersedes the original automatic-scheduling requirement at the
user's request. Existing host tasks invoking `schedule` therefore do not start
scans; disable any separately configured host task that invokes `rotate` directly.

**Recovery inside a requested Weekly Rotation is automatic.** That same job
retries only failed securities (up to two recovery attempts), revalidates their
history and evaluates them again before building the shortlist. Healthy securities
are not rescanned and no second full-universe job is launched. Failed/stale
per-security caches are bypassed; broker history recovery requests fresh data
instead of trusting the same cached frame. Prices and adjustment provenance are
still subject to the unchanged strict validators and scoring rules.

A TokenException can trigger one coordinated reload of credentials already
supplied in the environment, followed by the failed request's retry. This does
not issue a token, renew authorization or bypass broker permissions. Persistent
token errors, recovery errors, invalid/stale history and failed optional feed
attempts are recorded in recovery evidence and `unresolved_failures`.

Any unresolved fetch/validation failures keep the rotation/job **INCOMPLETE**,
even if a validated subset is published atomically. Unknown securities preserve
their prior membership for review; if no reliable classifications exist, no
watchlist version is published. Expected missing optional financial information
still permits explicitly labelled technical-only research under existing policy.
Validated recovered securities enter the same rankings and membership decisions
as the healthy securities. The UI exposes unresolved failures without rendering
full raw histories.
Recovery regression validation passed **1042 tests and 16 subtests** in 144.26
seconds. New cases cover invalid/stale per-stock history recovery, unchanged
healthy-stock fetch counts, one universe refresh/job, recovered shortlist
admission, unresolved invalid data, failed recovery hooks, recoverable and
persistent TokenException, and forced cache bypass. Scoring, thresholds, approved
targets/stops, ledger semantics and manual-only job initiation are unchanged.

The UI now shows small summaries on initial navigation. Full 213-stock JSON
trees are not rendered inside collapsed expanders. Stock evidence loads only
after its explicit control is selected, and full reports are downloadable on
request. Settings reads no scan payloads; latest results select one database row,
and Rotation History reads 50 lightweight version headers per page and only the
selected version's payload. Existing evidence and historical versions are retained.
Both watchlist tables also show the latest completed combined scan's original
daily score, score availability, final workspace decision and timestamp beside
the separate weekly discovery score. Nontrade/unknown/conflict results are included.
Rows absent from that scan, or transferred to the opposite directional category,
show Not scanned; a missing score is Unavailable, while a real zero stays zero.
These are dated snapshots and viewing a watchlist never initiates a scan.

All Futures Trading daily/watchlist tables and the existing Futures LONG / SHORT
reviewed-stock and Reports A–C tables now share selectable stock-context columns.
These include saved relevant headlines, news status/source/HTTP(S) article links,
publication/check timestamps, P/E, sector P/E, ROE/ROCE, available financial
metrics, Futures VWAP versus signal VWAP, RSI/ADX/MACD, ATR, volume, candle
structure, EMA/support/resistance and measured execution-check factors. Weekly
indicators have a Weekly prefix; live-session values are not inferred from them.
Optional one-stock news details expose up to 20 saved headlines and source links
without fetching network data or rendering the entire report.

Use **Choose visible columns → Visible columns → Save column selection** in
each table. Preferences persist independently in the additive `ft_view_columns`
table in the existing UI database. Missing fields are ignored when applying old
preferences, and an empty selection can be changed in the same control. Existing
reports/scoring/risk checks and manual-only scan behavior remain intact.
Weekly fetched headlines are retained when only AI sentiment analysis failed;
this does not make unavailable sentiment a verified directional signal. Missing
news distinguishes fetch failure, not checked, and no relevant headlines found.
Numeric gaps remain unavailable rather than being replaced with zeros.
Validation: 142 focused tests passed, including the existing Reports A–E UI
compatibility checks. The final full suite passed **1025 tests and 16 subtests**
in 148.57 seconds; compilation and whitespace checks passed. Column persistence,
daily/weekly context precedence, failed-versus-empty news, numeric zero values,
unsafe URL filtering and legacy daily-score fallback are covered offline.
Focused manual-mode/UI regression checks passed 79 tests, including navigation
with a large 213-stock archived report, one-stock evidence loading, Settings with
report reads forbidden, and disabled automatic maintenance for empty, completed,
failed and incomplete prior states. Full-suite results for this change appear below.
The final full regression run passed **1006 tests and 16 subtests** in 141.90
seconds. Compilation and whitespace checks passed. Existing failed/incomplete
records remain in history and are labelled saved results; they do not launch
new jobs. Native browser performance on the user's Windows machine has not been
measured here.

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
| `ft_managed_workers` | Jobs protected by the crash-safe native worker lock |
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
Weekly liquidity uses completed Futures volume or a closing-session volume
snapshot; live quote/depth/spread checks are reserved for Daily Trading. Historical
quality is validated per security. Metadata eligibility count is distinct from the count
with sufficient research data.

Default adjusted equity history comes from the existing YahooProvider with
`auto_adjust=True`, two-year daily history and a completed-session local cache. Raw Kite
candles are never appended to that adjusted series. Benchmark/sector index data
comes from the existing index provider. Completed-session normalization removes
live/incomplete days and holidays configured by `MARKET_HOLIDAYS_IST`. Missing
sessions, duplicate timestamps, invalid OHLCV and stale history fail closed.
Changing `weekly_history_source` to `kite` requires adjustment provenance or an
explicit technical-only unadjusted-history configuration.

Indicators: 5/20/21/63/126-session returns, 252-session high drawdown, six-month
low distance, 52-week low and distance from that low, SMA20/50/200, RSI, MACD/signal, ATR, trend slope, higher/lower
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
initialization requires clicking Run Full Universe Scan. Once lists contain ACTIVE
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

Saved weekly schedule values are retained for compatibility, but no UI scheduling
or first-visit catch-up is enabled. `run_futures_workspace_schedule.bat` now returns
MANUAL_MODE through the disabled `schedule` command. No machine-level task was
installed or edited on the user's laptop from this remote workspace. Manual jobs
still prevent overlapping scans and apply validated watchlist changes atomically.

Managed worker locks are automatically released by the OS after a crash; their
saved leases are recovered on the next status read or job start. For a legacy
abandoned job from older code, first stop that worker/process, inspect `status`,
then run:

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
For maintenance while the UI is closed, explicitly run `rotate`, `recheck`, or
`refresh-universe` from the CLI. `schedule` performs no work in manual mode.

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

If a persisted lock exists without a worker in this UI process, the status panel
now displays its job ID and explains that saved RUNNING status is not evidence
of a live worker. It suppresses scheduled retries and offers **Recover stopped
job**. Stop other Streamlit/scheduler workers before using it. After recovery,
manual scan controls are available and automatic maintenance waits five minutes.
CLI `status` and `recover-job` now read the database directly without constructing
market-data services or requiring broker credentials. The saved-lock recovery UI
and existing workspace/background-job tests pass (72 tests).

### Crash-safe job locks and Windows environment repair

Jobs now hold a kernel file lock beside the SQLite database for their complete
lifetime: `msvcrt` byte locking on Windows and `flock` on Linux. The OS releases
it when a process dies. An additive `ft_managed_workers` table identifies jobs
using this mechanism. When no process holds the kernel lock, their abandoned
database leases are automatically marked FAILED and released. A live worker
blocks duplicate scans and cannot be recovered even with `--worker-stopped`.
Age alone never determines liveness, so a long full-universe scan keeps its lock.

Locks from older code lack this proof of ownership and require one explicit
recovery after stopping the previous Streamlit/scheduler workers. From PowerShell
in the project directory, after updating the code:

```powershell
.\.venv\Scripts\python.exe scripts\run_futures_workspace.py recover-job --worker-stopped
.\run_ui.bat
```

Alternatively, after stopping the previous workers, use the single launcher
command `.\run_ui.bat --recover-stopped`. It repairs an unusable Windows
environment if necessary, recovers the stopped job, and launches Streamlit.
An active managed worker causes recovery to fail and the launcher to stop.

The recovery command now finds the saved job automatically, so no job ID needs
to be copied. It preserves existing watchlists, versions and execution history.
If no lock remains, it returns NO_LOCK. Never delete the `.workspace.lock` file
while any worker is active; all workers sharing this database must use the new
code. Use a local database/file system so native process-lock semantics apply.

`run_ui.bat` checks that the Windows environment actually runs. If it is missing
or unusable, it calls `setup_windows.bat`. Setup preserves a copied Linux/broken
environment in a uniquely named `.venv_backup_*` folder, creates the existing
Python 3.12 Windows environment and installs requirements. It preserves `.env`
and application data. A local Python 3.12 installation and working dependency
downloads remain necessary. Native Windows execution is unavailable in this
Linux workspace; Windows locking is covered by mocked branch tests, and actual
process crashes/duplicate exclusion are tested on Linux.

The targeted regression run passed 79 workspace/background-job tests, including
actual process death, live-worker protection, finalization failure, legacy
recovery without broker initialization, and Windows lock acquire/release.
The final full regression run passed **979 tests and 16 subtests** in 138.37
seconds. Compilation and `git diff --check` passed. Native Windows launcher
execution and live broker/data-provider operation have not been verified here.

### Post-market weekly discovery and technical-first research

Weekly Rotation now runs a full-universe completed-session technical screen
before optional detailed research. It enriches up to twice the configured list
capacity per direction, plus every existing membership (including stocks losing
their classification). All stocks still receive technical evaluations and audit
evidence. Qualified stocks outside the enrichment shortlist remain discovery
research; they are not automatically admitted without that context attempt.
The rotation summary records the technical count, context count, shortlist,
execution-liquidity unknowns, policy and elapsed seconds.

Weekly liquidity evidence uses the nearest valid exact Futures contract's last
five completed daily volumes, with at least three usable sessions, freshness
checks and the existing minimum-volume setting. The dated volume average is
cached separately per contract, token and completed session; cached observations
are re-evaluated against the current threshold. If historical volume is
unavailable, a recent near-close volume snapshot can provide context. Partial
intraday, stale and future snapshots cannot pass. Neither bid/ask prices nor
spread are weekly admission gates.

Unknown weekly volume does not erase reliable technical classification; the
candidate is labelled execution-liquidity unverified and requires Daily Trading's
live checks. Observed low completed volume marks the candidate UNKNOWN_DATA and
preserves an existing membership for review; wholly unreliable rotations retain
the preceding version. Verified major-event blocks still require review.

Adjusted equity history is reused across jobs/weekends while its latest completed
session matches the required session; a new completed session triggers refresh.
Weekly news permits the existing short-lived cache instead of always forcing
refresh. Public fundamentals retain their existing dated cache. No synthetic
bars, prices or fundamentals are generated.

The watchlist UI includes the 52-week low, distance above it, high drawdown,
weekly volume-evidence status and a reminder that daily execution liquidity needs
fresh validation. A 52-week low alone does not create a SHORT signal. Daily scan
scores, quotes, spread/depth, ledger reconciliation, session/risk checks and
Reports A–E remain unchanged. No new configuration or migration is required.

Focused validation passed 134 tests covering this behavior and daily execution
regressions. The final full suite passed **991 tests and 16 subtests** in 146.67
seconds; compilation and whitespace checks passed. These changes reduce redundant context requests, but no production
runtime estimate has been validated. Network outages and slow history providers
can still extend full-universe scan times. Adaptive parallel fetching is described
below; cross-process resumable scans have not been added.

### Automatic retry loop corrected

The UI previously scheduled its next maintenance check five minutes after job
submission, and the scheduler skipped only COMPLETED weekly attempts. A long
rotation ending INCOMPLETE/FAILED could therefore trigger a new full scan as soon
as it finished. The check timer now starts at completion. A terminal weekly
attempt (COMPLETED, INCOMPLETE or FAILED) consumes that weekly cycle; failed or
incomplete attempts return AUTO_RETRY_PAUSED with their saved reason. Manual full
rotations in the current cycle also prevent an unsolicited scheduled duplicate.
Failed daily metadata refreshes are not retried repeatedly that same day.

Manual **Run Full Universe Scan** remains available for retries, and a new weekly
cycle still runs automatically. The Weekly Rotation/status UI keeps the incomplete
or failed reason visible. Focused scheduler, workspace, post-market discovery and
background-job validation passed 90 tests, including restart persistence,
next-week scheduling, manual retries and a long-operation completion timer.

### Adaptive parallel weekly processing

Technical screening and shortlisted research now use parallel worker batches,
each starting at 2. Skipped candidates do not promote worker capacity. After
three full healthy batches, capacity doubles: 2, 4, 8, 16,
32, and onward to the configured resource ceiling. Set
`FUTURES_WORKSPACE_MAXIMUM_WORKERS` (default 32, supported 2–128) and
`FUTURES_WORKSPACE_HEALTHY_PARALLEL_BATCHES` (default 3), or save these in Settings.
Eight is not a fixed ceiling. Actual submitted work is bounded by pending stocks.

Timeout, network and rate-limit failures trigger a short cooldown and step back
to the actual previous capacity. Failed stocks have at most two additional
attempts. Completed stocks are never rerun by this worker controller. Successful
context stages are cached per stock, so a news retry does not repeat already
completed fundamentals or liquidity reads. Permanent data failures do not retry
or promote worker capacity; exhausted transient failures produce UNKNOWN_DATA
and preserve the previous membership for review. Some upstream providers return
UNKNOWN without exposing their transport error; this cannot establish healthy
technical data and does not justify capacity growth.

Shared history reads are guarded per symbol, the existing Yahoo request-start
pacing is synchronized, and event/provider initialization and mutable event
assessment are protected. Legacy provider/index reads remain serialized where
thread safety is not established. News network fetching and independent adjusted
stock histories may overlap. The unchanged Kite gateway retains its shared
per-API pacing, timeout and retry controls; more stock workers never raise its
permitted request rate. Shared benchmark and sector histories are reused.

Workers return evidence to one coordinator. SQLite evidence writes, membership
rotation and atomic version publication remain coordinated under the existing
crash-safe workspace job lock. The UI reports worker capacity in completion
updates; rotation exports retain growth/fallback/error events and final capacity.
Daily Trading, its execution validation, scores and Reports A–E are unchanged.

Validation covers growth through 64 workers,
timeouts with fallback from 8 to 4, a non-power-of-two resource ceiling,
retry exhaustion, permanent invalid data, completed-stage reuse and parallel
shared-history deduplication. Production speed gains are unmeasured and depend
on data-source limits and availability.
The final regression suite passed **1005 tests and 16 subtests** in 146.80
seconds, including the safeguard that resets each phase to two workers and
prevents skipped items from promoting capacity. Compilation and whitespace
checks passed.

### Incomplete-data diagnostics and optional-feed isolation

`NO_RELIABLE_CLASSIFICATIONS_LAST_VERSION_PRESERVED` means the attempted scan
could not publish reliable directional research; it does not distinguish failed
fetches from rejected historical data. New rotation reports/UI summaries show
fetched/usable history counts, technical/final classification counts, available
fundamentals, optional-context outages, benchmark coverage and counted rejection
reasons. Individual gap failures include sample missing dates and row counts.
Insufficient-history failures include fetched and completed row counts. These
diagnostics remain lightweight and do not render the full universe's raw evidence.

For the default adjusted-equity source, Nifty history now uses the same explicit
two-year adjusted source (`^NSEI`). If that source fails, the adapter requests an
explicit two-year broker history, rather than accepting a potentially shorter
annual cache. This prevents shorter benchmark coverage from misidentifying
weekday exchange holidays as stock-history gaps. Verified missing sessions,
stale data, missing adjustment provenance and invalid OHLC still fail closed.
Session-gap checks require full coverage of the latest 253-session indicator
window, rather than identical start dates across unused older cache history.

Sector-history outages are optional context and no longer discard valid stock
history. After bounded optional-context retries exhaust, the coordinator reuses
completed stages and keeps reliable technical classifications with missing
fundamentals/news labelled UNKNOWN. Confirmed low Futures volume and verified
major events still require review. Daily Trading's mandatory live gates and
manual-only operation remain unchanged.

Final focused validation passed 100 tests covering aligned benchmark fetching/fallback,
optional sector/news failures, counted missing-history reasons, gap diagnostics,
manual UI and existing discovery/parallel regressions. The actual data failure
on the user's machine remains unverified until a new manual run supplies these
source/reason counts; no unavailable production data was fabricated.
The full regression run before the final required-window boundary refinement
passed 1012 tests and 16 subtests in 148.50 seconds; the final focused run covers
that refinement, including genuine missing-session rejection. Compilation and
whitespace checks passed.
