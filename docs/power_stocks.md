Power Stocks is a separate manual watchlist for NTPC, POWERGRID, TATAPOWER,
JSWENERGY, BHEL, CGPOWER, PFC and RECLTD. Role descriptions are theme labels;
the list includes power financing and equipment as well as generation.

It reuses AI-Stock's existing scanner, company research, price/volume technical
analysis, news and OI ranking, execution safeguards, history and selectable
columns. Both directions are evaluated. Research rows do not imply approval.
Futures availability is verified from the instrument master, not assumed.

Power Stocks has its own power_stocks_prepared cache, power_stock_scan_runs
database archive, background-job state and column preferences. CGPOWER belongs
to both lists but their saved scan results remain separate. The execution ledger
and global two-entry daily limit remain shared. Weekly Rotation and the broader
Futures scanner universe are unchanged. No orders are placed.

Select Power Stocks in the sidebar and click Scan Power Stocks. After close,
use AFTER_MARKET_RESEARCH; LIVE_SCAN uses prepared history and current market
data. Preparation modes process only these eight stocks plus benchmark context.
Navigation, mode selection and viewing history never start scans automatically.
