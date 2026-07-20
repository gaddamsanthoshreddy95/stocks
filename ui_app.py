"""Windows-friendly local Streamlit interface for the trading platform."""

from __future__ import annotations

import json
import os
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


def candidate_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for trade in [*report.get("trades", []), *report.get("watchlist", [])]:
        event = trade.get("event_risk", {})
        rows.append({
            "Symbol": trade.get("symbol"), "Status": trade.get("status"),
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


def selected_stock_details(report: dict[str, Any]) -> None:
    selected = [*report.get("trades", []), *report.get("watchlist", [])]
    for trade in selected:
        levels = trade.get("levels", {})
        option = trade.get("option_strategy", {})
        plan = option.get("trade") or {}
        label = f"{trade.get('symbol')} — {trade.get('final_action')} — {trade.get('status')}"
        with st.expander(label):
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
            legs = option_leg_rows(trade)
            if legs:
                st.markdown("**Exact option strikes**")
                st.dataframe(pd.DataFrame(legs), width="stretch", hide_index=True)
            else:
                rejection = option.get("rejection") or {}
                st.warning(rejection.get("reason", option.get("reason", "No executable option strike was approved.")))


def snapshot_rows(group: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for name, quote in (group or {}).items():
        if isinstance(quote, dict):
            rows.append({"Market": name.replace("_", " ").title(),
                         "Price": quote.get("price"), "Change": quote.get("change"),
                         "Change %": quote.get("change_percent"),
                         "Status": "AVAILABLE" if quote.get("price") is not None else "UNAVAILABLE"})
    return rows


def show_report(report: dict[str, Any]) -> None:
    summary_cards(report)
    tabs = st.tabs(["Candidates", "Market & context", "Rejected", "Complete report", "JSON"])
    with tabs[0]:
        rows = candidate_rows(report)
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True) if rows else st.info(
            "No executable or watchlist candidates were produced. Review rejected candidates for exact reasons."
        )
        if rows:
            st.subheader("Selected-stock levels and option strikes")
            selected_stock_details(report)
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
            for item in rejected:
                with st.expander(str(item.get("symbol", "UNKNOWN"))):
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
            market, sectors = ContextEnrichment(
                platform.settings.market_data_source == "kite"
            ).market_and_sectors(force_refresh=True)
            st.session_state["global_market_context"] = market
            st.session_state["sector_market_context"] = sectors
    market = st.session_state.get("global_market_context", {})
    global_rows = snapshot_rows(market.get("global", {}))
    if global_rows:
        st.dataframe(pd.DataFrame(global_rows), width="stretch", hide_index=True)
    else:
        st.info("Press Refresh global markets. Live mode and working internet access are required.")
    st.subheader("Sector strength")
    sectors = st.session_state.get("sector_market_context", {})
    sector_rows = [{"Sector": name, "Status": row.get("status", "UNAVAILABLE"),
                    "Market score": row.get("score"), "Rating": row.get("rating", "UNAVAILABLE"),
                    "Contribution proxy %": row.get("contribution_proxy_percent"),
                    "Price": row.get("price"), "Change %": row.get("change_percent"),
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
        show_report(report)


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
    selected = st.selectbox("Open saved report", list(labels))
    if selected:
        report = database.get_report(labels[selected])
        if report:
            show_report(report)


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
        page = st.radio("Page", ("Dashboard", "Daily report", "Bearish options", "Analyze", "History", "System"),
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
    elif page == "History":
        history_page(database)
    else:
        system_page(platform, database)


if __name__ == "__main__":
    main()
