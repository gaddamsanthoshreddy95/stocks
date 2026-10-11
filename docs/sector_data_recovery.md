# Weekly sector context and uncertainty

Scans remain manual. Opening or refreshing a page performs no market scan.
Click **Weekly Rotation → Run Full Universe Scan** to generate new evidence.
Existing saved results are not recomputed by deploying this change.

## Source and identity policy

The workspace resolves stock sectors from NSE quote industry fields, matching
both NSE symbol and the official equity-list ISIN. If NSE is unavailable, it
tries Moneycontrol search and company data with the same identity checks.
Access denials and rate limits are respected. No guessed company match is used.
Source access circuits apply to the current job; a later manually initiated job
can try again. The official source references are
[NSE industry classification](https://www.nseindia.com/static/products-services/industry-classification)
and the [Nifty sector index catalogue](https://www.niftyindices.com/indices/equity/sectoral-indices).
Verified identities are cached for 30 days with source and retrieval timestamps.
Unverified local CSV labels and older verified labels remain visible as context,
but cannot certify a sector comparison. Conflicting verified labels require review.
Unknown symbols no longer inherit a default Diversified classification in this workspace.

Sector benchmark candidates use explicit index names, including Power,
Hospitals, Healthcare, Insurance, Capital Goods, Cement, Chemicals and others.
Established Yahoo aliases, broker index histories and official NSE daily index
archives provide independent fallback sources. A catalogue entry does not imply
that every provider carries that index. Unavailable or newly launched indices
remain unknown until enough real completed sessions exist.

Every benchmark must pass OHLC validation, freshness and at least 21 aligned
completed sessions. A fresh one-row cache is insufficient. No missing prices are
filled or fabricated. When available, the validated Nifty calendar supplies the
session grid. Labelled broader-industry proxies are informational only and never
earn the exact-sector relative-strength points.

## Recovery and rankings

The existing weekly job screens stock history and estimates the highest possible
score before requesting external sector context. Existing members and candidates
whose missing context could affect eligibility receive priority. Only failed stock
evaluations are retried; healthy stock histories and shared valid index tables
are preserved. There is no additional full-universe scan. Retries remain bounded
to two recovery attempts. Token errors can reload supplied credentials once;
this cannot renew an expired broker token or bypass authorization.

Each scoring condition is PASS, FAIL or UNKNOWN. Original ten-point checks and
admission thresholds remain authoritative. Confirmed points, possible points,
coverage and the score over observed conditions are reported separately.
The observed score and its ranking comparison are explicitly report-only and
have no out-of-sample validation. Missing data never receives bonus points.

Equal-score shortlist boundary candidates receive context within the configurable
per-direction budget (default 100). Equal original scores use measured 20-session
directional strength before the final symbol tie breaker. Uncertain contenders and
budget overflow remain in a review queue. Existing members remain protected by
the existing membership policy. Daily execution checks and risk gates are unchanged.

Unresolved requested data failures produce INCOMPLETE rather than COMPLETE.
The weekly result records recovery actions, source errors, per-stock provenance,
sector coverage and review reasons. A usable partial result follows the existing
publication policy; it does not certify failed members as tradable.

## Viewing the result

Weekly Rotation shows **Candidates requiring review**, **Sector benchmark coverage**,
and an optional original-versus-observed ranking comparison. Stock tables expose
sector identity, benchmark source/date, confirmed/possible scores and coverage via
**Visible columns**. Previously saved column preferences are preserved, so select
the new columns explicitly if they are absent in an existing view.

These source integrations are covered by fixture tests, including identity
mismatch, stale and conflicting identities, short/gapped/invalid histories,
exact-index fallback, proxy isolation and failed-context recovery. Live source
availability depends on provider access and credentials; test success does not
establish that every live sector currently has a usable benchmark.
