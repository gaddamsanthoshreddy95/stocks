# Sector direction confirmation

Actionable LONG confirmation now requires both the stock and its mapped sector index to have strictly positive session returns. SHORT confirmation requires strictly negative returns for both. Returns are measured from the session's first completed five-minute bar open to the latest completed close, on exactly aligned stock, sector and Nifty windows. A zero return is flat and cannot pass. This does not use later bars or future prices.

This shared absolute direction check supplements the existing relative-strength checks: LONG must still outperform or equal sector and Nifty; SHORT must underperform or equal them. Existing VWAP, price/volume, OI, break/retest, quote freshness, news, costs and Entry Quality checks remain in force. Missing or incomplete sector data remains UNKNOWN; it is not treated as neutral or supporting data.

The existing enabled-by-default LONG/SHORT confirmation gates apply this check through the common FuturesOpportunityScanner used by Futures LONG / SHORT, selected-stock Futures Trading daily scans and thematic features including Imp Future. Weekly rankings and historical saved reports are not rewritten. Research LONG/SHORT rows may remain visible without approval; only current APPROVED and ENTER candidates qualify for entry consideration. Explicitly disabling the existing directional confirmation gates also disables their checks.

Saved evidence records sector_direction with its status, return values, basis and reasons. Opposing or flat sectors produce LONG_SECTOR_NOT_BULLISH / SHORT_SECTOR_NOT_BEARISH; stock-direction failures are likewise explicit. Fresh manual scans reassess the condition. No automatic scans or broker orders are introduced.
