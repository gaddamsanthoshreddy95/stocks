# BIOCON validation investigation — 10 October 2026

Fresh real Yahoo downloads through the existing `YahooProvider` with
`auto_adjust=True` supplied 497 BIOCON rows ending 9 October 2026. Existing
session normalization retained 496 completed configured sessions. The retained
data has **zero failed OHLCV predicates**: finite observations, positive prices,
nonnegative volume, high at least open/low/close and low at most open/high/close.
No prices were edited, filled, clipped, dropped for OHLC repair, or synthesized.

The original Yahoo cache that produced the user's `HISTORY_INVALID` result was
not present in this workspace. Its exact offending predicate/date/value therefore
remains **unverified**; fresh data passing does not prove what failed previously.
The original generic error combined five predicates and did not retain their
individual results. New diagnostics name the exact rule and record sample dates
and observations. Invalid fresh-looking cache files previously bypassed content
validation; they are now preserved as `.rejected.parquet` / `.rejected.json` and
refetched. Invalid replacements remain rejected. No tolerance or gate was relaxed.

The sector path previously used Kite's NIFTY PHARMA history even in Yahoo-adjusted
weekly mode. A read-only authentication check reproduced TokenException without
exposing credentials/account data. Weekly Yahoo mode now uses the existing
`^CNXPHARMA` mapping. New Kite clients read current environment credentials rather
than import-time snapshots. This fixes stale in-process values but cannot create
valid broker authorization or renew an expired token. Futures/live data still
requires valid authorization. See [Kite authentication documentation](https://kite.trade/docs/connect/v3/user/).

Actual rerun with the captured BIOCON, Nifty and Pharma histories:

| Result | Value |
|---|---|
| Bearish discovery score | 90 / 100 |
| Classification | SHORTING_STOCKS |
| Confirmation | Three completed sessions |
| Recovery state | RECOVERY_INVALIDATED |
| Directional shortlist candidate | Yes: score exceeds configured 60 threshold |
| Full-universe bearish rank | 4th among 122 bearish candidates from 213 eligible stocks |
| Configured bearish research shortlist | Included: capacity 50 |
| Final membership/trade approval | Not published; requires remaining gates |
| Score without sector context | 80 / 100; SHORTING_STOCKS |
| Last completed price observation | 9 October 2026 |

The initial cached 9 October metadata was subsequently confirmed by fresh,
authenticated NFO/NSE masters covering 213 eligible stocks: BIOCON26OCTFUT,
token 12485378, expiry 27 October 2026, lot size 2500, underlying token 2911489.
A second read-only profile check authenticated with the current credentials.
Final ranking/admission depends
on the full universe, capacity and event/liquidity context. This result is weekly
research and does not approve a daily SHORT entry.

A subsequent read-only full-universe technical run evaluated all 213 stocks with
the same adapter, validators and adaptive runner. `universe_ranking.json` retains
the per-stock evaluations and ranking evidence. BIOCON remained 90/100 and ranked
fourth in the bearish group. No fundamental/news/liquidity admission job, production
membership publication, execution-ledger change or broker order was performed.

`provenance.json` records snapshot checksums. `evaluation.json` contains the full
observed indicators and validation report. Rerun the saved observations without
network calls or watchlist changes:

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_weekly_stock.py BIOCON --history reports\investigations\biocon_2026_10_10\BIOCON.parquet --benchmark reports\investigations\biocon_2026_10_10\NSEI.parquet --sector reports\investigations\biocon_2026_10_10\CNXPHARMA.parquet --as-of "2026-10-10 16:00+05:30"
```

To identify the original failure on the user's machine, run the same audit with
the original cached history using `--history`, or omit it to select BIOCON's
existing adjusted cache automatically. The tool reports individual OHLCV
violations even when another session/calendar check prevents classification.

Validation: 1035 tests and 16 subtests passed in the full regression run, including
ten new strict-diagnostics/cache/sector/authentication/real-BIOCON snapshot tests.
Compilation and whitespace checks passed. The existing unrelated `.env` token
change was retained; no credentials were stored in this investigation's artifacts.
