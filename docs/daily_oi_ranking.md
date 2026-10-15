Daily OI ranking applies to the shared scanner used by Futures LONG / SHORT
and Futures Trading daily evaluation. Weekly Rotation is unchanged.

The default ranking score is 90% unchanged price technical score plus 10% OI
score. Existing six technical weights, technical admission thresholds and all
execution/news/risk checks remain unchanged. Decision priority and short setup
priority remain ahead of ranking score in standalone reports. The workspace
uses the ranking score to order already-approved daily candidates.

OI uses the exact contract's fresh quote versus its previous completed session's
OI and close, validated by the existing execution-quality checks. No extra data
fetch or alternate-expiry baseline is used. Absolute OI alone earns no points.

With price moving in the candidate direction, increasing OI scores 75–100;
decreasing OI (covering/unwinding) scores 25–50. Movement against the candidate
scores zero. Price changes below 0.05% or OI changes below 0.1% score neutral 50.
OI strength saturates at 5% change. These are configurable ranking hypotheses,
not calibrated probabilities or evidence of strategy profitability.

Missing, stale, invalid or wrong-contract evidence is UNAVAILABLE, never neutral
or zero; the ranking retains the original technical score, while existing
mandatory OI execution safeguards still block unverified admission. Only a
verified completed-five-minute Futures technical score can receive the overlay.

Settings: FUTURES_SCAN_OI_RANKING_WEIGHT (default .10, zero disables),
FUTURES_SCAN_OI_MINIMUM_CHANGE_PERCENT (.10),
FUTURES_SCAN_OI_MINIMUM_PRICE_CHANGE_PERCENT (.05), and
FUTURES_SCAN_OI_FULL_STRENGTH_CHANGE_PERCENT (5).

Delivery percentage, stock P/E and sector P/E remain separate company-research
checks; this change does not add them to the technical ranking formula.
Scans remain manual and never place orders. Existing time cutoffs are retained.
