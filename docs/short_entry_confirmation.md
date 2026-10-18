Audit and change: stricter daily SHORT confirmation

The existing scanner already checked Futures VWAP, relative volume, trigger
crossing, invalidation/stop coverage, exhaustion, news, liquidity and final quote
freshness. It did not require both stock/Futures VWAP slopes to be non-rising,
an intraday breakdown followed by a completed failed retest for every SHORT,
or same-session weakness versus both Nifty and sector. Its OI handling accepted
known regimes as available evidence, including covering/unwinding, rather than
requiring a price decline with OI growth for every SHORT.

The new configurable gate is enabled by default across all shared-scanner daily
features, including Futures LONG / SHORT, Futures Trading daily and thematic
pages. Weekly discovery, LONG scoring and all technical weights are unchanged.
No existing risk, company-research, news or execution gate is waived.
The one directional correction is the stock VWAP research rule: with the new
SHORT gate enabled, a verified stock VWAP check requires price at/below VWAP
instead of the legacy LONG-biased at/above rule. The original outcome is retained
as original_underlying_vwap_check. Missing/invalid VWAP still blocks verification;
all other company-research rules are unchanged. Disabling the gate restores the
previous directional policy.

To approve a SHORT, the new gate requires:
- Both completed stock and Futures prices below their own session VWAP; the
  fresh Futures quote must also remain below Futures VWAP. Both VWAP slopes
  between the latest two completed bars must be flat or falling.
- No three-bar higher-high/higher-low stock structure backed by at least 60%
  bullish-candle volume share.
- Stock session return no stronger than either Nifty or sector return, using
  identical completed five-minute windows from 09:15. Missing context blocks
  verification, rather than inventing neutral relative strength.
- At least 0.05% price decline and 0.1% OI growth against the existing verified
  previous-session exact-contract baseline. OI ranking weights remain unchanged.
- A completed Futures close below the preceding three bars' support, a later
  bearish failed retest that trades back to that level, and a crossed retest-low
  trigger. The retest must be within the last two completed bars; candidate
  breakdowns are searched over the last eight bars of this session.
- Fresh stock and matching-contract quotes and confirmation no older than 30 seconds
  when the report is returned. Quotes are refreshed only if necessary, without
  redownloading history. Existing spread, depth and stop-invalidation constraints
  still apply; a passed new gate alone cannot approve a trade.

Missing/invalid data yields UNKNOWN, an unconfirmed retest yields WAIT, and
bullish/OI conflicts yield BLOCKED (mapped to REJECT when other data is verified).
Reasons and underlying evidence are retained and visible in shared stock tables.
There is no fixed 09:45 ban; sufficient completed bars and confirmation are
required. No future or still-forming bar is used to confirm the historical setup.

Settings use FUTURES_SCAN_SHORT_CONFIRMATION_ followed by GATE_ENABLED,
SUPPORT_BARS, RETEST_MAX_AGE_BARS, BUYING_VOLUME_SHARE, MAX_AGE_SECONDS. Set the
gate flag to 0 to use the previous gate policy; other safeguards remain active.

Validation uses labelled synthetic sessions and integration tests, not an actual
BEL replay. BEL's saved 16-Oct-2026 scanner evidence and historical decision-time
candles are not available here. The change does not establish a success rate or
prove it would have prevented that trade. Existing historical backtests do not
replay these new stock/sector/OI confirmation inputs and must not be described as
performance validation of the new policy. Scans stay manual and place no orders.
