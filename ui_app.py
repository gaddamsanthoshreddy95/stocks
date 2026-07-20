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
            "Relative strength": trade.get("relative_strength", {}).get("score"),
            "RS status": trade.get("relative_strength", {}).get("status"),
            "Event risk": event.get("event_risk_score"),
            "Event data": event.get("event_data_availability_state"),
            "Option approval": trade.get("option_trade_approval", {}).get("status"),
        })
    return rows


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
        page = st.radio("Page", ("Dashboard", "Daily report", "Analyze", "History", "System"),
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
    elif page == "History":
        history_page(database)
    else:
        system_page(platform, database)


if __name__ == "__main__":
    main()
