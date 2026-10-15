AI-Stock is a manually scanned, user-curated list of 12 AI infrastructure-related
companies. The infrastructure descriptions are user-supplied theme labels, not
verified revenue exposure or an investment endorsement.

The page uses the existing FuturesOpportunityScanner and company-research,
technical, news, OI, execution-quality and safety gates. It evaluates LONG and
SHORT independently. Research rows are not approved orders. Active stock Futures
contracts are verified using the instrument master; theme membership never
waives Futures eligibility. Missing data remains unavailable with reasons.

The universe is fixed to CGPOWER, ABB, POWERINDIA, CUMMINSIND, BLUESTARCO, VOLTAS,
AMBER, POLYCAB, KEI, SIEMENS, TATACOMM, KAYNES. Sector/index data is still fetched
as context. General universe discovery is not called. Prepared-universe fallback
is intersected with the restricted universe to prevent expansion.

AI-Stock uses a separate ai_stock_prepared cache beside the configured Futures
preparation cache and ai_stock_scan_runs archives in the existing UI database.
The existing Futures archives and Weekly Rotation watchlists are unchanged.
The execution ledger remains shared, retaining the global two-entry limit.
Entry cutoff is no later than 15:00 IST and manual exit no later than 15:10 IST;
earlier configured limits are retained. It places no orders.

Select AI-Stock in the sidebar, select a mode, and click Scan AI-Stock.
AFTER_MARKET_RESEARCH analyses completed sessions. LIVE_SCAN requires prepared
history and current execution data. DAILY_PREP/FULL_RESEARCH prepare only the
specified universe. Navigation, status refresh and history viewing start no scan.
Each successful worker result is saved even if the browser navigates away.
