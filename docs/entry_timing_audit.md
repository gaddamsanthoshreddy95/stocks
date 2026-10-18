# Entry timing protections

The shared daily scanner now applies completed-session breakout/retest confirmation
in both directions. LONG requires rising or flat stock and exact-contract Futures
VWAPs, price above both, relative strength against Nifty and the mapped sector,
price rise with rising OI, and a completed breakout followed by a successful
retest and trigger crossing. SHORT retains the opposite checks. Missing context
cannot pass. Three-bar opposing price/volume structure blocks entry.

LONG executable ask-weighted entry must cross the trigger without exceeding the
existing trigger-distance limit, and its fixed stop must cover the retest low.
Existing SHORT trigger and setup-invalidation checks remain. Target/stop fractions,
scoring weights, account reconciliation, news gates and other execution checks
remain mandatory. An unaccommodated structural stop rejects the setup; it never
widens the stop automatically.

Both directions use the existing confirmation window settings (the historical
`short_confirmation_*` names are retained for compatibility). Separate LONG and
SHORT enabled toggles default to 1. The default confirmation/quote window is 30
seconds. Each approved snapshot stores `entry_valid_until`; the saved table always
shows entry status, labels expired approvals as requiring a manual rescan, and
never upgrades research rows. This is a snapshot validity bound, not a guarantee
of unchanged market conditions or a broker-side entry check. No automatic scan,
order or stop is placed. Manually recheck execution price, quantity and stop.

The common configuration caps entry at 15:00 IST and exit at 15:10 IST regardless
of news protection and older environment overrides. Earlier configured cutoffs
remain earlier. Weekly rankings are unchanged.

## Historical evidence

`src.futures.confirmation_replay.replay` accepts recorded decision-time market
snapshots, each with `decision_at`, `available_at`, direction, raw data and a
validated OI check. It rejects snapshots not available at decision time and the
gates exclude unfinished/future candles. Subsequent exact-contract candles are
used only to measure outcomes. It reports directional target-first/stop-first,
maximum adverse/favorable excursion and sample sufficiency using the existing
cost model. Fill assumptions and five-minute stop-first ambiguity are explicit.
It skips overlapping hypothetical entries per direction.

This utility validates confirmation timing, not the full scanner's research,
news, account, liquidity or approval policy. Existing legacy backtests explicitly
state that the new confirmation gates were not replayed. Synthetic regression
fixtures are not actual BEL trade reconstructions and do not establish a success
rate. A real performance claim requires recorded snapshots and chronological
out-of-sample evaluation; no missing historical quote or OI is manufactured.
