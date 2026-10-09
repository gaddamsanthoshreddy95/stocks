# Futures cost audit — 9 October 2026 IST

The fee model has been checked against official NSE and Zerodha sources. **Empirical calibration remains UNKNOWN:** no matched executable quotes/fills or allocated contract notes were supplied. The active residual slippage assumption remains **2 basis points per leg, PROVISIONAL**. No broker calls, order actions, market-data downloads or historical probability preparation were performed for this audit.

## Verified standard tariff

| Component | Model |
|---|---|
| Brokerage | Lower of 0.03% or ₹20 per executed order; one entry order and one exit order assumed |
| Exchange plus IPFT | ₹183/crore on each side, counted once |
| Exchange/IPFT split before 1 March 2026 | ₹173 + ₹10 per crore |
| Exchange/IPFT split from 1 March 2026 | ₹182.99 + ₹0.01 per crore |
| STT through 31 March 2026 | 0.02% of sell turnover |
| STT from 1 April 2026 | 0.05% of sell turnover |
| SEBI | ₹10/crore, both sides |
| Stamp duty | 0.002% of buy turnover |
| GST | 18% of brokerage + exchange + SEBI + IPFT |

Supported historical tariff dates start at 1 October 2024. The existing numeric `exchange_rate=0.0000183` field now explicitly represents the **inclusive exchange/IPFT rate**. IPFT is allocated from that total rather than added again. Custom rates are labeled `CUSTOM_ASSUMPTIONS`.

The model retains unrounded per-trade tax allocations. STT is rounded half up to whole rupees after applicable client/day aggregation; rounding each hypothetical trade independently would misallocate the daily tax. Exact contract-note allocation and rounding remain uncalibrated. Standard resident individual tariff assumptions do not establish the user's account-specific pricing; negative-balance/collateral shortfall surcharges, dealer orders, physical delivery, penalties and other special tariffs need separate evidence.

## Spread, depth and slippage

A depth-weighted entry already includes entry-side spread and displayed-book impact. Live plans now charge half the observed full spread against **exit turnover only**, through `exit_spread_bps`. This is a provisional estimate of future exit spread. The existing residual 2-bps-per-leg allowance remains separate for latency/execution effects beyond displayed depth; no claim of statistical calibration is made. Supplying legacy extra slippage and explicit exit spread simultaneously raises an error to prevent double counting.

`trade_plan()` always uses the supplied Futures entry for target/stop percentages. Equity reference values are retained as research context, with no equity-based executable target or stop. A scan plan is explicitly a modeled execution reference, not a broker-confirmed fill. Minimum net reward/risk remains 1.0; target/stop remain 0.3%/0.2%.

## Saved-data arithmetic example — not an executable entry

The existing `reports/report_e_execution.json` contains ICICIBANK26OCTFUT's completed **8 October 2026 15:25 IST** research candle reference of ₹1,355 and lot size 700. The same record explicitly sets `live_entry_verified=false`. Its equity discovery reference was ₹1,344.90. No fresh price or executable spread is assumed below.

| Item | Corrected modeled value |
|---|---:|
| Futures target | 1359.06500000 |
| Futures stop | 1352.29000000 |
| Net target profit | 1880.42106858 |
| Net stop loss | 2858.65117572 |
| Net reward/risk | 0.65780011 |

This example excludes an unavailable spread charge and still fails the 1.0 minimum net reward/risk. These calculations do not approve a trade. Applying the old equity-offset arithmetic would give a target of ₹1,359.0347 and stop of ₹1,352.3102; the corrected Futures-based levels are independent of that equity reference.

## Local calibration command and validation

```bash
.venv/bin/python scripts/audit_futures_costs.py > reports/futures_cost_calibration.json
.venv/bin/python -m pytest tests/test_futures_cost_calibration.py -q
```

The command uses local data only and reports calibration `UNKNOWN`, observation count 0, and no invented observed slippage. Optional `--input PATH` accepts normalized local contract-note charge allocations and matched quote/fill observations as documented in the script. It reports discrepancies without automatically changing settings; fill observations require a preceding executable-side reference within two seconds.

**Focused validation: 17 tests passed.** They cover both directions, equity isolation, effective-date boundaries, IPFT allocation, brokerage caps, taxable sides, GST, exit-only spread, invalid inputs, unsupported historical dates, custom assumptions and unavailable calibration evidence. Full regression results are recorded separately by the parent correction workflow.

## Official evidence

- [NSE exchange/IPFT circular, effective 1 March 2026](https://nsearchives.nseindia.com/content/circulars/FA73061.pdf)
- [NSE STT circular, effective 1 April 2026](https://nsearchives.nseindia.com/content/circulars/FATAX73524.pdf)
- [NSE STT computation and reporting](https://www.nseindia.com/static/products-services/equity-derivatives-securities-transaction-tax)
- [Zerodha current tariff](https://zerodha.com/charges/)
- [Zerodha STT rounding](https://support.zerodha.com/category/account-opening/resident-individual/ri-charges/articles/how-is-the-securities-transaction-tax-stt-calculated)
- [Zerodha October 2024 fee revision](https://zerodha.com/z-connect/business-updates/revision-in-exchange-transaction-charges-and-securities-transaction-tax-from-october-1-2024)
- [Zerodha resident account tariff and exceptions](https://support.zerodha.com/category/account-opening/resident-individual/ri-charges/articles/what-is-the-brokerage-at-zerodha-for-equity)

Source retrieval dates, applicable periods and calculation scope are stored in `resources/futures_fee_schedule.json`.
