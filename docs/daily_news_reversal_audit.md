# Daily Futures news-reversal audit — 12 October 2026

## Findings reported before changing production behavior

The existing scanner collects same-day article sentiment and applies
`TodayNewsService.direction_check` during execution. It rejects opposing sentiment
without requiring materiality verification or a defined post-news price reversal.
The company-research layer separately treats negative sentiment as a failed
`recent_news_quality` check, even for Shorts. FinBERT's HIGH label is based on
probability strength, not verification of economic significance. The source
collector deduplicates title/source pairs but does not certify each publisher
article or maintain independently verified company-impact assessments.

The generic bearish report is a research-candidate ranking and can contain
REJECT/UNKNOWN entries. It is not equivalent to approved Short recommendations.
The workspace daily page previously returned all approved selected stocks rather
than an explicit five per direction. Weekly membership and scoring are independent.

## Implemented opt-in gate

`FUTURES_SCAN_NEWS_CONFLICT_GATE_ENABLED=0` is the default. Production behavior
remains unchanged until explicitly enabled after this audit. No production scan,
membership publication, order or credential operation was performed by this audit.

When enabled, exclude SHORT only for a verified, fresh, HIGH-materiality,
company-specific positive event AND bullish intraday confirmation. Apply the
symmetric negative-event/bearish-confirmation rule to LONG. Scores and original
weights remain untouched. A headline alone cannot trigger this exclusion.

Verification requires a trusted HTTPS publisher domain, explicit source and
company-impact verification, correct company symbol, independent materiality
verification and a nonempty verification basis. The payload must carry publication,
first-seen/availability and assessment times. Future or timezone-free timestamps
cannot establish an event at a historical decision time. Materiality inferred only
from sentiment probability is insufficient. Existing native FinBERT results do
not acquire verification flags automatically. Verified provider annotations are
preserved through analysis; missing annotations remain UNAVAILABLE, not neutral.
Trusted domains include NSE/BSE, recognized financial publishers, HPCL issuer
domains and the independently checked Technip/GlobeNewswire issuer release.
They are checked by hostname boundary, not a substring or source display name.

Default confirmation requires two completed, consecutive five-minute bars,
both closes beyond the current session VWAP in the opposing direction, a close
beyond recent prior highs/lows, at least 0.3% directional movement from the prior
completed close and relative volume at least 1.2 versus preceding bars. Thresholds
are configurable hypotheses, not calibrated performance guarantees. The news
snapshot must be at most 900 seconds old, prices at most 600 seconds old, and
events at most 24 hours old. Invalid OHLCV, gaps, future/still-forming candles and
missing volume cannot confirm a reversal. Session VWAP uses only that day's
completed candles. Independent opening/closing rules still govern entry approval.

The enabled gate replaces the two legacy headline-only news restrictions, retains
their original company-research check for audit, and preserves every other failed
or unavailable fundamental, execution, ledger and risk gate. Unavailable verified
news/confirmation blocks approval rather than being called confirmed neutral.
Confirmed neutral and a successful collection with no relevant news are separate
states. Source outages or unchecked new articles do not become neutral evidence.

Confirmed exclusions are evaluated before execution-pool selection and refreshed
before final execution decisions. Excluded items remain in the raw reviewed list
with timestamped evidence and reasons; final reports skip them and fill from the
next eligible ranked item. The enabled daily workspace takes five approved stocks
per side; additional approved research candidates remain visible under
OUTSIDE_DAILY_TOP_5. Weekly Top 30 is not touched. More retained candidates may be
reviewed when enabled so late exclusions do not exhaust the original review budget.

## HINDPETRO, 12 October 2026

The [Technip issuer release](https://www.ten.com/en/media/press-releases/technip-energies-awarded-large-epc-contract-hpcl-mumbai-refinery-modernization)
confirms an EPC award for HPCL's Mumbai refinery modernization. The
[syndicated issuer publication](https://www.globenewswire.com/news-release/2026/10/12/3378555/0/en/technip-energies-awarded-large-epc-contract-by-hpcl-for-mumbai-refinery-modernization-project-in-india.html)
lists 01:00 US/Eastern, equivalent to 10:30 IST on 12 October. That source cannot
be used to explain an exclusion before its verified publication time. Benefits
described for a refinery project do not independently establish a bullish
earnings/trading impact for the company awarding the contract. The fixture
therefore deliberately marks economic direction/materiality UNVERIFIED.

The user also identified USD/INR strengthening. That is macro context, not a
company-specific announcement; importer/exporter exposures can differ. No
timestamped FX observations or independently assessed company-impact record
were supplied. The gate does not relabel a generic FX headline as company news.

The local HINDPETRO history caches end on 8 October and contain daily candles.
No actual 12-Oct five-minute replay or exact user decision time is available.
Regression tests cover the real release's availability boundary and insufficient
economic proof. Separate synthetic candles and explicitly labelled synthetic
event attestations test both exclusion directions and replacement. They do not
claim that actual HINDPETRO should have been excluded at a particular time.

## Activation and remaining verification

Keep the flag at 0 until reviewing verified-event feed coverage and historical
confirmation performance. Set it to 1 and restart only when deliberately adopting
this policy. Scans remain manual. The workspace preserves its 15:00 entry cutoff
and 15:10 exit deadline; no automatic orders are added.

The real HINDPETRO replay still needs the historical decision time, original
time-stamped news snapshot/assessment and real completed intraday bars. A missing
source or historical price record must remain an explicit limitation. In
particular, today's reconstructed sentiment must never be backdated to an earlier
decision.

## Validation and isolation

The existing shared news collector and weekly news aliases are unchanged, so
the feature cannot change Weekly Top 30 through shared alias matching.
A verified feed must identify HPCL reports with the actual HINDPETRO symbol.

A full-suite checkpoint passed 1,106 tests and 16 subtests. Subsequent focused
validation passed 160 tests, including enabled-gate scanner integration, daily
Top-5 replacement, preservation of weekly membership and session deadlines.
The final gate-specific run passed 26 tests. Synthetic scenario results establish
the implementation's decision rules; they do not establish real historical
HINDPETRO trade eligibility or predictive performance.
