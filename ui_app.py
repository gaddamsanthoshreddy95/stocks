"""Windows-friendly local Streamlit interface for the trading platform."""

from __future__ import annotations

import json
import os
from datetime import date
from typing import Any

import pandas as pd
import streamlit as st

from src.application.errors import PlatformError
from src.application.platform import TradingPlatform
from src.news.ai_sentiment import AISentimentAnalyzer
from src.presenter.daily_report import DailyReportPresenter
from src.ui.database import ReportDatabase
from src.workflow.context_enrichment import ContextEnrichment


st.set_page_config(page_title="Stock Analyzer", page_icon="📈", layout="wide")


@st.cache_resource
def services() -> tuple[TradingPlatform, ReportDatabase]:
    return TradingPlatform(), ReportDatabase()


def value(value: Any, fallback: str = "N/A") -> Any:
    return fallback if value is None else value


def summary_cards(report: dict[str, Any]) -> None:
    summary = report.get("summary", {})
    columns = st.columns(5)
    metrics = (
        ("Stocks scanned", summary.get("stocks_scanned", 0)),
        ("Candidates reviewed", summary.get("context_reviewed", 0)),
        ("Trades", summary.get("trades_generated", 0)),
        ("Watchlist", summary.get("watchlisted", 0)),
        ("Market", report.get("market", {}).get("regime", "UNAVAILABLE")),
    )
    for column, (label, metric) in zip(columns, metrics):
        column.metric(label, metric)


def candidate_rows(report: dict[str, Any], execution_marks: dict[str, str] | None = None) -> list[dict[str, Any]]:
    execution_marks = execution_marks or {}
    rows = []
    for trade in [*report.get("trades", []), *report.get("watchlist", [])]:
        event = trade.get("event_risk", {})
        rows.append({
            "Symbol": trade.get("symbol"), "Status": trade.get("status"),
            "Executable trade": "YES" if trade.get("status") == "TRADE" else "NO",
            "Actually traded": execution_marks.get(str(trade.get("symbol")), "NOT_TRADED"),
            "Action": trade.get("final_action"), "Quality": trade.get("quality_grade"),
            "Quality score": trade.get("quality_score"),
            "Readiness": trade.get("execution_readiness_score"),
            "R:R": trade.get("levels", {}).get("risk_reward"),
            "Support": trade.get("levels", {}).get("support"),
            "Resistance": trade.get("levels", {}).get("resistance"),
            "Relative strength": trade.get("relative_strength", {}).get("score"),
            "RS status": trade.get("relative_strength", {}).get("status"),
            "Event risk": event.get("event_risk_score"),
            "Event data": event.get("event_data_availability_state"),
            "Option approval": trade.get("option_trade_approval", {}).get("status"),
        })
    return rows


def option_leg_rows(trade: dict[str, Any]) -> list[dict[str, Any]]:
    option = trade.get("option_strategy", {})
    plan = option.get("trade") or {}
    return [{
        "Side": leg.get("side"), "Quantity": leg.get("quantity"),
        "Strike": leg.get("strike"), "Type": leg.get("option_type"),
        "Premium": leg.get("premium"), "Bid": leg.get("bid"), "Ask": leg.get("ask"),
        "OI": leg.get("open_interest"), "Volume": leg.get("volume"),
    } for leg in plan.get("legs", [])]


def execution_mark_control(database: ReportDatabase | None, run_id: str, symbol: str,
                           current_mark: str = "NOT_TRADED", key_prefix: str = "candidate") -> None:
    if not database or not run_id or not symbol:
        return
    mark = st.radio(
        "Did you actually trade this suggestion?",
        ("NOT_TRADED", "TRADED"),
        index=1 if current_mark == "TRADED" else 0,
        horizontal=True,
        key=f"execution-mark-{key_prefix}-{run_id}-{symbol}",
    )
    if st.button("Save traded status", key=f"save-execution-{key_prefix}-{run_id}-{symbol}"):
        database.set_candidate_execution(run_id, symbol, mark == "TRADED")
        st.success(f"{symbol} saved as {mark}.")


def selected_stock_details(report: dict[str, Any], database: ReportDatabase | None = None) -> None:
    selected = [*report.get("trades", []), *report.get("watchlist", [])]
    run_id = str(report.get("run_id", ""))
    execution_marks = database.get_candidate_executions(run_id) if database and run_id else {}
    for trade in selected:
        levels = trade.get("levels", {})
        option = trade.get("option_strategy", {})
        plan = option.get("trade") or {}
        label = f"{trade.get('symbol')} — {trade.get('final_action')} — {trade.get('status')}"
        with st.expander(label):
            symbol = str(trade.get("symbol", ""))
            execution_mark_control(database, run_id, symbol,
                                   execution_marks.get(symbol, "NOT_TRADED"), "selected")
            if trade.get("status") != "TRADE":
                st.warning("Watchlist/research candidate only — this is not an executable trade. "
                           "Position quantity remains zero until every final gate passes.")
            columns = st.columns(4)
            columns[0].metric("Current price", value(trade.get("current_price")))
            columns[1].metric("Support", value(levels.get("support")))
            columns[2].metric("Resistance", value(levels.get("resistance")))
            columns[3].metric("Risk / reward", value(levels.get("risk_reward")))
            st.dataframe(pd.DataFrame([{
                "Entry": levels.get("entry"), "Stop loss": levels.get("stop_loss"),
                "Target 1": levels.get("target_1"), "Target 2": levels.get("target_2"),
                "Target 3": levels.get("target_3"),
            }]), width="stretch", hide_index=True)
            st.write(f"**Option strategy:** {plan.get('strategy', option.get('strategy', 'UNAVAILABLE'))}")
            st.write(f"**Expiry:** {plan.get('expiry', option.get('expiry', 'UNAVAILABLE'))}")
            st.write(f"**Canonical structure:** {trade.get('option_structure', {}).get('status', 'UNAVAILABLE')}")
            st.write(f"**Canonical approval:** {trade.get('option_trade_approval', {}).get('status', 'UNAVAILABLE')}")
            approval = trade.get("option_trade_approval", {}).get("status")
            legs = option_leg_rows(trade) if approval == "APPROVED" and trade.get("status") == "TRADE" else []
            if legs:
                st.markdown("**Exact option strikes**")
                st.dataframe(pd.DataFrame(legs), width="stretch", hide_index=True)
            else:
                rejection = option.get("rejection") or {}
                st.warning(rejection.get("reason", option.get("reason",
                           "No executable option strike was approved for this candidate.")))


def snapshot_rows(group: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for name, quote in (group or {}).items():
        if isinstance(quote, dict):
            rows.append({"Market": name.replace("_", " ").title(),
                         "Price": quote.get("price"), "Change": quote.get("change"),
                         "Change %": quote.get("change_percent"),
                         "Status": "AVAILABLE" if quote.get("price") is not None else "UNAVAILABLE"})
        else:
            rows.append({"Market": name.replace("_", " ").title(), "Price": None,
                         "Change": None, "Change %": None, "Status": "UNAVAILABLE"})
    return rows


def show_report(report: dict[str, Any], database: ReportDatabase | None = None) -> None:
    summary_cards(report)
    tabs = st.tabs(["Candidates", "Market & context", "Rejected", "Complete report", "JSON"])
    with tabs[0]:
        marks = (database.get_candidate_executions(str(report.get("run_id")))
                 if database and report.get("run_id") else {})
        rows = candidate_rows(report, marks)
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True) if rows else st.info(
            "No executable or watchlist candidates were produced. Review rejected candidates for exact reasons."
        )
        if rows:
            st.subheader("Candidate levels and approved option strikes")
            st.caption("This section includes both executable trades and watchlist candidates. "
                       "Only rows marked TRADE / Executable trade YES are actionable paper-trade plans.")
            selected_stock_details(report, database)
    with tabs[1]:
        market = report.get("market", {})
        st.subheader("Global markets")
        global_rows = snapshot_rows(market.get("global", {}))
        if global_rows:
            st.dataframe(pd.DataFrame(global_rows), width="stretch", hide_index=True)
        else:
            st.info("Global market data is unavailable for this run. Live Kite mode still depends on Yahoo global-index connectivity.")
        context_columns = st.columns(2)
        with context_columns[0]:
            st.subheader("Commodities")
            commodity_rows = snapshot_rows(market.get("commodities", {}))
            st.dataframe(pd.DataFrame(commodity_rows), width="stretch",
                         hide_index=True) if commodity_rows else st.info("Commodity data unavailable.")
        with context_columns[1]:
            st.subheader("Forex")
            forex_rows = snapshot_rows(market.get("forex", {}))
            st.dataframe(pd.DataFrame(forex_rows), width="stretch",
                         hide_index=True) if forex_rows else st.info("Forex data unavailable.")
        st.subheader("Sector context")
        st.dataframe(pd.DataFrame(report.get("sector_ranking", [])), width="stretch",
                     hide_index=True)
        st.subheader("Context availability")
        st.json(report.get("context_statistics", {}), expanded=True)
        st.subheader("Dependency health")
        st.json(report.get("dependency_health", {}), expanded=True)
    with tabs[2]:
        rejected = report.get("rejected", [])
        if rejected:
            run_id = str(report.get("run_id", ""))
            execution_marks = (database.get_candidate_executions(run_id)
                               if database and run_id else {})
            for item in rejected:
                with st.expander(str(item.get("symbol", "UNKNOWN"))):
                    symbol = str(item.get("symbol", "UNKNOWN"))
                    st.error("Analytical status: REJECTED. Marking this as TRADED records your "
                             "actual action and does not convert it into an approved recommendation.")
                    execution_mark_control(database, run_id, symbol,
                                           execution_marks.get(symbol, "NOT_TRADED"), "rejected")
                    for reason in item.get("reasons", []):
                        st.write(f"• {reason}")
        else:
            st.success("No candidates were rejected.")
    with tabs[3]:
        st.code(DailyReportPresenter.render(report), language="text")
    with tabs[4]:
        st.download_button("Download report JSON", json.dumps(report, indent=2, default=str),
                           file_name=f"daily-report-{report.get('date', 'latest')}.json",
                           mime="application/json")
        st.json(report, expanded=False)


def dashboard(platform: TradingPlatform, database: ReportDatabase) -> None:
    st.title("Stock Analyzer")
    st.caption("Local research and paper-trading dashboard. No live orders are submitted.")
    counts = database.counts()
    left, middle, right = st.columns(3)
    left.metric("Saved reports", counts["reports"])
    middle.metric("Generated paper-trade ideas", counts["generated_trades"])
    right.metric("Data source", platform.settings.market_data_source.upper())
    history = database.list_reports(10)
    st.subheader("Recent reports")
    if history:
        st.dataframe(pd.DataFrame(history), width="stretch", hide_index=True)
    else:
        st.info("Generate the first daily report from the Daily report page.")
    st.subheader("Live global market snapshot")
    if st.button("Refresh global markets"):
        with st.spinner("Loading global indices, commodities and forex…"):
            # This button explicitly requests internet market context. Global
            # and sector quotes come from Yahoo and do not require Kite mode.
            market, sectors = ContextEnrichment(
                True,
                sector_history_provider=(platform.provider
                                         if platform.settings.market_data_source == "kite" else None),
            ).market_and_sectors(force_refresh=True)
            st.session_state["global_market_context"] = market
            st.session_state["sector_market_context"] = sectors
            st.session_state["global_market_refresh_attempted"] = True
    market = st.session_state.get("global_market_context", {})
    global_rows = snapshot_rows(market.get("global", {}))
    global_available = sum(row["Status"] == "AVAILABLE" for row in global_rows)
    if global_rows:
        st.dataframe(pd.DataFrame(global_rows), width="stretch", hide_index=True)
    if not global_available:
        if st.session_state.get("global_market_refresh_attempted"):
            st.error(f"Market refresh returned no usable global quotes. Reason: "
                     f"{market.get('reason', 'Yahoo Finance returned no data')}. "
                     "Check internet/firewall access to query1.finance.yahoo.com and try again.")
        elif not global_rows:
            st.info("Press Refresh global markets. Working internet access is required.")
    elif global_available < len(global_rows):
        st.warning(f"Partial market refresh: {global_available}/{len(global_rows)} global quotes are available.")
    st.subheader("Sector strength")
    sectors = st.session_state.get("sector_market_context", {})
    sector_rows = [{"Sector": name, "Status": row.get("status", "UNAVAILABLE"),
                    "Source": row.get("source", "UNAVAILABLE"),
                    "Market score": row.get("score"), "Rating": row.get("rating", "UNAVAILABLE"),
                    "Contribution proxy %": row.get("contribution_proxy_percent"),
                    "Price": row.get("price"), "Change %": row.get("change_percent"),
                    "Model": row.get("score_model"), "Samples": row.get("sample_count"),
                    "Above 20 DMA %": row.get("breadth_above_20dma_percent"),
                    "Above 50 DMA %": row.get("breadth_above_50dma_percent"),
                    "Median 20D %": row.get("median_return_20d_percent"),
                    "Median 5D %": row.get("median_return_5d_percent"),
                    "Reason": row.get("reason")}
                   for name, row in sectors.items()]
    if sector_rows:
        st.caption("Market score is the sector's cross-sectional return percentile (0–100). "
                   "Contribution proxy is the signed share of absolute sector-index movement; "
                   "it is not official Nifty constituent attribution.")
        st.dataframe(pd.DataFrame(sector_rows), width="stretch", hide_index=True)
    else:
        st.info("Sector strength is unavailable. Refresh in live mode to request sector-index data.")


def bearish_options_page(platform: TradingPlatform) -> None:
    st.title("Bearish option opportunities")
    st.caption("Shows up to two validated Long Put or Bear Put Spread structures; it never forces a trade.")
    with st.form("bearish-options-form"):
        left, right = st.columns(2)
        minimum_score = left.number_input("Minimum bearish score", 0, 100, 60)
        option_month = right.text_input("Option month (optional)", placeholder="YYYY-MM")
        submitted = st.form_submit_button("Scan bearish options", type="primary")
    if submitted:
        with st.spinner("Scanning the universe and validating option structures…"):
            try:
                st.session_state["bearish_options"] = platform.bearish_option_candidates(
                    limit=2, minimum_score=int(minimum_score), option_month=option_month.strip() or None
                )
            except (PlatformError, ValueError) as exc:
                st.error(str(exc))
    result = st.session_state.get("bearish_options")
    if not result:
        return
    st.info(result["message"])
    for candidate in result.get("candidates", []):
        option = candidate["option"]
        trade = option.get("trade", {})
        with st.expander(f"{candidate['symbol']} — {trade.get('strategy')} — bearish score {candidate['bearish_score']}", expanded=True):
            columns = st.columns(4)
            columns[0].metric("Spot", candidate["current_price"])
            columns[1].metric("RSI", candidate["rsi"])
            columns[2].metric("Relative volume", candidate["relative_volume"])
            columns[3].metric("Approval", candidate["option_trade_approval"]["status"])
            levels = candidate.get("levels", {})
            st.dataframe(pd.DataFrame([{
                "Support": levels.get("support"), "Resistance": levels.get("resistance"),
                "Entry": levels.get("entry"), "Stop loss": levels.get("stop_loss"),
                "Target 1": levels.get("target_1"), "Target 2": levels.get("target_2"),
                "Target 3": levels.get("target_3"),
            }]), width="stretch", hide_index=True)
            st.markdown("**Exact option strikes**")
            st.dataframe(pd.DataFrame(trade.get("legs", [])), width="stretch", hide_index=True)
            st.write(f"Maximum loss: ₹{value(trade.get('maximum_loss'))}")
            st.write(f"Maximum profit: {value(trade.get('maximum_profit'), 'UNLIMITED')}")
            st.write(f"Breakeven: ₹{value(trade.get('breakeven'))}")
    if not result.get("candidates"):
        st.json(result.get("rejection_counts", {}), expanded=True)


def daily_report_page(platform: TradingPlatform, database: ReportDatabase) -> None:
    st.title("Daily report")
    with st.form("daily-report-form"):
        left, middle, right = st.columns(3)
        limit = left.number_input("Maximum final trades", 1, 50, 5)
        minimum_score = middle.number_input("Minimum technical score", 0, 100, 40)
        option_month = right.text_input("Option month (optional)", placeholder="YYYY-MM")
        submitted = st.form_submit_button("Run report", type="primary")
    if submitted:
        with st.spinner("Running the end-to-end workflow…"):
            try:
                report = platform.daily_report(int(limit), int(minimum_score), option_month.strip() or None)
                report_id = database.save_report(report, platform.settings.market_data_source)
                st.session_state["current_report"] = report
                st.success(f"Report completed and saved locally as #{report_id}.")
            except (PlatformError, ValueError) as exc:
                st.error(str(exc))
    report = st.session_state.get("current_report")
    if report:
        show_report(report, database)


def analyze_page(platform: TradingPlatform) -> None:
    st.title("Analyze one stock")
    with st.form("analyze-form"):
        symbol = st.text_input("NSE symbol", placeholder="RELIANCE").strip().upper()
        submitted = st.form_submit_button("Analyze", type="primary")
    if submitted and symbol:
        with st.spinner(f"Analyzing {symbol}…"):
            try:
                result = platform.analyze(symbol)
                st.session_state["analysis_result"] = result
            except (PlatformError, ValueError) as exc:
                st.error(str(exc))
    result = st.session_state.get("analysis_result")
    if result:
        st.subheader(str(result.get("symbol", symbol)))
        st.json(result, expanded=True)


def history_page(database: ReportDatabase) -> None:
    st.title("Report history")
    history = database.list_reports(100)
    if not history:
        st.info("No reports have been saved yet.")
        return
    st.dataframe(pd.DataFrame(history), width="stretch", hide_index=True)
    labels = {f"#{row['id']} — {row['generated_at']} — {row['market_regime']}": row["id"] for row in history}
    st.subheader("Delete report history")
    delete_scope = st.radio("Deletion scope", ("Selected reports", "All reports"), horizontal=True)
    selected_for_deletion = (
        st.multiselect("Reports to delete", list(labels)) if delete_scope == "Selected reports"
        else list(labels)
    )
    delete_count = len(selected_for_deletion)
    confirmed = st.checkbox(
        f"I confirm permanent deletion of {delete_count} report(s)",
        key="confirm-bulk-report-delete",
    )
    if st.button("Delete report history", type="secondary",
                 disabled=not confirmed or delete_count == 0):
        deleted = database.delete_reports([labels[label] for label in selected_for_deletion])
        st.success(f"Deleted {deleted} report(s). This cannot be undone.")
        st.rerun()

    st.subheader("Open a saved report")
    selected = st.selectbox("Open saved report", list(labels))
    if selected:
        report_id = labels[selected]
        report = database.get_report(report_id)
        if report:
            show_report(report, database)


def trade_tracker_page(database: ReportDatabase) -> None:
    st.title("Actual trade tracker")
    st.caption("Record trades you actually placed. This journal never sends broker orders.")
    st.subheader("All suggestions marked TRADED")
    traded_suggestions = database.list_traded_suggestions()
    if traded_suggestions:
        st.dataframe(pd.DataFrame(traded_suggestions), width="stretch", hide_index=True)
        st.caption("Original status is preserved from the report snapshot: TRADE, WATCHLIST, or REJECTED.")
    else:
        st.info("No report suggestions have been marked TRADED yet.")
    summary = database.actual_trade_summary()
    columns = st.columns(4)
    columns[0].metric("Recorded", summary["total"])
    columns[1].metric("Open", summary["open"])
    columns[2].metric("Closed", summary["closed"])
    columns[3].metric("Realized P&L", f"₹{summary['realized_pnl']:,.2f}")

    with st.expander("Add an actual trade", expanded=not summary["total"]):
        with st.form("actual-trade-entry", clear_on_submit=True):
            first = st.columns(4)
            symbol = first[0].text_input("Symbol", placeholder="SBIN").strip().upper()
            instrument = first[1].selectbox("Instrument", ("EQUITY", "OPTION"))
            side = first[2].selectbox("Side", ("BUY", "SELL"))
            quantity = first[3].number_input("Quantity", min_value=1, value=1, step=1)
            second = st.columns(4)
            entry_date = second[0].date_input("Entry date", value=date.today())
            entry_price = second[1].number_input("Entry price", min_value=0.01, value=1.0, step=0.05)
            stop_loss = second[2].number_input("Stop loss (optional)", min_value=0.0, value=0.0)
            target = second[3].number_input("Target (optional)", min_value=0.0, value=0.0)
            third = st.columns(4)
            strategy = third[0].text_input("Strategy", placeholder="Swing / Long Call")
            option_type = third[1].selectbox("Option type", ("CE", "PE"),
                                             help="Used only when Instrument is OPTION")
            strike = third[2].number_input("Strike (options)", min_value=0.0, value=0.0)
            expiry = third[3].date_input("Expiry (options)", value=date.today())
            fees = st.number_input("Entry fees/taxes", min_value=0.0, value=0.0)
            notes = st.text_area("Notes")
            add = st.form_submit_button("Save actual trade", type="primary")
        if add:
            try:
                trade_id = database.add_actual_trade({
                    "symbol": symbol, "instrument_type": instrument, "side": side,
                    "quantity": int(quantity), "entry_date": entry_date.isoformat(),
                    "entry_price": entry_price, "stop_loss": stop_loss or None,
                    "target_price": target or None, "strategy": strategy,
                    "option_type": option_type if instrument == "OPTION" else None,
                    "strike": strike if instrument == "OPTION" else None,
                    "expiry": expiry.isoformat() if instrument == "OPTION" else None,
                    "fees": fees, "notes": notes,
                })
                st.success(f"Actual trade #{trade_id} saved.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    trades = database.list_actual_trades()
    if not trades:
        st.info("No actual trades recorded yet.")
        return
    display_columns = ["id", "symbol", "instrument_type", "side", "strategy", "option_type",
                       "strike", "expiry", "quantity", "entry_date", "entry_price", "stop_loss",
                       "target_price", "status", "exit_date", "exit_price", "fees", "realized_pnl", "notes"]
    st.dataframe(pd.DataFrame(trades)[display_columns], width="stretch", hide_index=True)

    open_trades = [trade for trade in trades if trade["status"] == "OPEN"]
    if open_trades:
        st.subheader("Close an open trade")
        open_labels = {f"#{trade['id']} — {trade['symbol']} — {trade['side']} {trade['quantity']}": trade["id"]
                       for trade in open_trades}
        with st.form("close-actual-trade"):
            close_label = st.selectbox("Open trade", list(open_labels))
            close_columns = st.columns(3)
            exit_date = close_columns[0].date_input("Exit date", value=date.today())
            exit_price = close_columns[1].number_input("Exit price", min_value=0.01, value=1.0, step=0.05)
            exit_fees = close_columns[2].number_input("Additional exit fees", min_value=0.0, value=0.0)
            close = st.form_submit_button("Close trade", type="primary")
        if close:
            try:
                pnl = database.close_actual_trade(open_labels[close_label], exit_date.isoformat(),
                                                  exit_price, exit_fees)
                st.success(f"Trade closed. Realized P&L: ₹{pnl:,.2f}")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    st.subheader("Delete an incorrectly entered trade")
    trade_labels = {f"#{trade['id']} — {trade['symbol']} — {trade['status']}": trade["id"] for trade in trades}
    delete_label = st.selectbox("Trade record", list(trade_labels), key="delete-trade-record")
    delete_confirmed = st.checkbox("I confirm this trade record should be permanently deleted")
    if st.button("Delete selected trade record", disabled=not delete_confirmed):
        trade_id = trade_labels[delete_label]
        if database.delete_actual_trade(trade_id):
            st.success(f"Deleted actual trade record #{trade_id}. This cannot be undone.")
            st.rerun()


def system_page(platform: TradingPlatform, database: ReportDatabase) -> None:
    st.title("System status")
    st.warning("Credentials remain in your local .env file and are never stored in SQLite.")
    st.json({
        "mode": "paper",
        "market_data_source": platform.settings.market_data_source,
        "database": str(database.path.resolve()),
        "dependency_health": AISentimentAnalyzer(
            model=platform.settings.news_ai_model,
            spacy_model=platform.settings.news_spacy_model,
        ).dependency_health(),
    })
    st.code(
        "KITE_API_KEY=your_api_key\n"
        "KITE_ACCESS_TOKEN=your_daily_access_token\n"
        "MARKET_DATA_SOURCE=kite",
        language="text",
    )


def main() -> None:
    platform, database = services()
    with st.sidebar:
        st.header("Navigation")
        page = st.radio("Page", ("Dashboard", "Daily report", "Bearish options", "Analyze",
                                 "Trade tracker", "History", "System"),
                        label_visibility="collapsed")
        st.divider()
        st.caption(f"Source: {platform.settings.market_data_source.upper()}")
        st.caption("Paper trading only")
    if page == "Dashboard":
        dashboard(platform, database)
    elif page == "Daily report":
        daily_report_page(platform, database)
    elif page == "Analyze":
        analyze_page(platform)
    elif page == "Bearish options":
        bearish_options_page(platform)
    elif page == "Trade tracker":
        trade_tracker_page(database)
    elif page == "History":
        history_page(database)
    else:
        system_page(platform, database)


if __name__ == "__main__":
    main()
