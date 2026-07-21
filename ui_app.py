"""Windows-friendly local Streamlit interface for the trading platform."""

from __future__ import annotations

import json
import os
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, timedelta
from threading import Lock
from typing import Any
from uuid import uuid4

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


class DailyReportJobs:
    """Run long tasks outside Streamlit's page lifecycle so navigation is safe."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ui-background")
        self._jobs: dict[str, Future] = {}
        self._lock = Lock()

    def submit(self, platform: TradingPlatform, database: ReportDatabase, limit: int,
               minimum_score: int, option_month: str | None) -> str:
        job_id = uuid4().hex

        def generate_and_save() -> dict[str, Any]:
            report = platform.daily_report(limit, minimum_score, option_month)
            report_id = database.save_report(report, platform.settings.market_data_source)
            return {"report": report, "report_id": report_id}

        future = self._executor.submit(generate_and_save)
        with self._lock:
            self._jobs[job_id] = future
        return job_id

    def submit_task(self, task) -> str:
        """Submit any UI operation that must survive navigation."""
        job_id = uuid4().hex
        future = self._executor.submit(task)
        with self._lock:
            self._jobs[job_id] = future
        return job_id

    def future(self, job_id: str | None) -> Future | None:
        with self._lock:
            return self._jobs.get(str(job_id)) if job_id else None


@st.cache_resource
def daily_report_jobs() -> DailyReportJobs:
    return DailyReportJobs()


def sync_daily_report_job() -> tuple[Future | None, dict[str, Any] | None]:
    """Copy a completed background result into this browser session once."""
    job_id = st.session_state.get("daily_report_job_id")
    future = daily_report_jobs().future(job_id)
    if future is None or not future.done():
        return future, None
    if st.session_state.get("daily_report_synced_job_id") == job_id:
        return future, None
    st.session_state["daily_report_synced_job_id"] = job_id
    try:
        result = future.result()
    except Exception as exc:  # The worker preserves the original exception for display here.
        st.session_state["daily_report_job_error"] = str(exc)
        return future, None
    st.session_state["current_report"] = result["report"]
    st.session_state["daily_report_job_error"] = None
    return future, result


FEATURE_JOBS = {
    "market": ("Global market refresh", "global_market_result"),
    "bearish": ("Bearish options scan", "bearish_options"),
    "analysis": ("Stock analysis", "analysis_result"),
}


def sync_feature_jobs() -> None:
    """Move completed generic job results into the current browser session."""
    for feature, (_, result_key) in FEATURE_JOBS.items():
        job_id = st.session_state.get(f"{feature}_job_id")
        future = daily_report_jobs().future(job_id)
        if future is None or not future.done():
            continue
        if st.session_state.get(f"{feature}_synced_job_id") == job_id:
            continue
        st.session_state[f"{feature}_synced_job_id"] = job_id
        try:
            result = future.result()
            if feature == "market":
                st.session_state["global_market_context"] = result["market"]
                st.session_state["sector_market_context"] = result["sectors"]
                st.session_state["global_market_refresh_attempted"] = True
            else:
                st.session_state[result_key] = result
            st.session_state[f"{feature}_job_error"] = None
        except Exception as exc:
            st.session_state[f"{feature}_job_error"] = str(exc)


def feature_is_running(feature: str) -> bool:
    future = daily_report_jobs().future(st.session_state.get(f"{feature}_job_id"))
    return future is not None and not future.done()


def start_feature_job(feature: str, task) -> None:
    job_id = daily_report_jobs().submit_task(task)
    st.session_state[f"{feature}_job_id"] = job_id
    st.session_state[f"{feature}_synced_job_id"] = None
    st.session_state[f"{feature}_job_error"] = None


@st.fragment(run_every=2)
def daily_report_status() -> None:
    """Render cross-page status; called in the sidebar on every Streamlit rerun."""
    future, completed = sync_daily_report_job()
    generic_futures = [
        daily_report_jobs().future(st.session_state.get(f"{feature}_job_id"))
        for feature in FEATURE_JOBS
    ]
    if future is None and not any(generic_futures):
        return
    st.divider()
    st.caption("Background jobs")
    if future is not None:
        if not future.done():
            st.info("Daily report: running in background.")
        elif st.session_state.get("daily_report_job_error"):
            st.error("Daily report failed: " + st.session_state["daily_report_job_error"])
        else:
            report_id = completed["report_id"] if completed else None
            message = f"Daily report: completed and saved as #{report_id}." if report_id else "Daily report: completed and saved."
            st.success(message)
    for feature, (label, _) in FEATURE_JOBS.items():
        feature_future = daily_report_jobs().future(st.session_state.get(f"{feature}_job_id"))
        if feature_future is None:
            continue
        if not feature_future.done():
            st.info(f"{label}: running in background.")
        elif st.session_state.get(f"{feature}_job_error"):
            st.error(f"{label} failed: {st.session_state[f'{feature}_job_error']}")
        else:
            st.success(f"{label}: completed.")
    if ((future is not None and not future.done())
            or any(item is not None and not item.done() for item in generic_futures)):
        st.caption("Status refreshes automatically every 2 seconds.")
    elif completed is not None:
        # A fragment rerun only redraws the sidebar.  Trigger one full rerun so
        # the newly synchronized report immediately replaces the page body.
        st.rerun()


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


def start_recommended_trade_control(database: ReportDatabase, run_id: str,
                                    trade: dict[str, Any]) -> None:
    """Turn a recommendation into a durable open position from the report UI."""
    symbol = str(trade.get("symbol", "")).upper().removesuffix(".NS")
    tracked = database.get_candidate_trade(run_id, symbol)
    if tracked and tracked["status"] == "OPEN":
        st.success("ACTIVE TRADE — shown in Daily Status until you mark it completed.")
        st.caption(f"Entry ₹{tracked['entry_price']:,.2f} · Quantity {tracked['quantity']} · "
                   f"Hold until {tracked.get('hold_until') or 'target / stop-loss'}")
        return
    if tracked and tracked["status"] == "CLOSED":
        outcome = "PROFIT" if float(tracked.get("realized_pnl") or 0) >= 0 else "LOSS"
        st.success(f"COMPLETED · {outcome} · Final P&L ₹{float(tracked.get('realized_pnl') or 0):,.2f}")
        st.caption(f"Exited on {tracked.get('exit_date')} at ₹{float(tracked.get('exit_price') or 0):,.2f}")
        return
    levels = trade.get("levels") or {}
    suggested_entry = float(levels.get("entry") or trade.get("current_price") or 1)
    suggested_hold = date.today() + timedelta(days=10)
    with st.form(f"start-trade-{run_id}-{symbol}"):
        st.markdown("**I traded this stock**")
        st.caption("Saving here creates the active trade automatically; no separate Trade Tracker entry is needed.")
        columns = st.columns(4)
        quantity = columns[0].number_input("Quantity", min_value=1, value=1, step=1,
                                           key=f"qty-{run_id}-{symbol}")
        entry_price = columns[1].number_input("Actual entry price", min_value=0.01,
                                              value=suggested_entry, step=0.05,
                                              key=f"entry-{run_id}-{symbol}")
        entry_date = columns[2].date_input("Trade date", value=date.today(),
                                           key=f"entry-date-{run_id}-{symbol}")
        hold_until = columns[3].date_input("Hold until (review date)", value=suggested_hold,
                                           min_value=entry_date,
                                           key=f"hold-{run_id}-{symbol}")
        submitted = st.form_submit_button("Mark TRADED & start tracking", type="primary")
    if submitted:
        database.set_candidate_execution(run_id, symbol, True)
        action = str(trade.get("final_action") or trade.get("action") or "BUY").upper()
        database.add_actual_trade({
            "symbol": symbol, "instrument_type": "EQUITY",
            "side": "SELL" if action in {"SELL", "SHORT"} else "BUY",
            "quantity": int(quantity), "entry_date": entry_date.isoformat(),
            "entry_price": entry_price, "stop_loss": levels.get("stop_loss"),
            "target_price": levels.get("target_1") or levels.get("target"),
            "strategy": trade.get("strategy") or action,
            "recommendation_run_id": run_id, "hold_until": hold_until.isoformat(),
            "notes": "Created from daily-report recommendation",
        })
        st.success(f"{symbol} is now an active tracked trade.")
        st.rerun()


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
            if database:
                start_recommended_trade_control(database, run_id, trade)
            else:
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


def news_impact(score: float | int | None) -> str:
    """Convert the signed FinBERT score into a user-facing impact label."""
    numeric_score = float(score or 0)
    if numeric_score >= 60:
        return "SUPER POSITIVE"
    if numeric_score >= 15:
        return "POSITIVE"
    if numeric_score <= -60:
        return "SUPER NEGATIVE"
    if numeric_score <= -15:
        return "NEGATIVE"
    return "NEUTRAL"


def likely_news_reaction(impact: str, materiality: str = "LOW") -> str:
    strength = "strong " if str(materiality).upper() == "HIGH" else ""
    reactions = {
        "SUPER POSITIVE": f"Could trigger {strength}buying interest and upward volatility.",
        "POSITIVE": "May support the price or improve buying interest.",
        "NEUTRAL": "Limited directional effect is likely unless new details emerge.",
        "NEGATIVE": "May create selling pressure or weaken buying interest.",
        "SUPER NEGATIVE": f"Could trigger {strength}selling pressure and downward volatility.",
    }
    return reactions.get(impact, reactions["NEUTRAL"])


def news_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten report news into one readable row per stock headline."""
    rows: list[dict[str, Any]] = []
    candidates = [*report.get("trades", []), *report.get("watchlist", []),
                  *report.get("rejected", [])]
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        symbol = str(candidate.get("symbol", "UNKNOWN"))
        news = candidate.get("news") or {}
        assessments = {
            str(item.get("title", "")): item
            for item in news.get("article_assessments", []) if isinstance(item, dict)
        }
        for headline in news.get("headlines", []):
            if not isinstance(headline, dict):
                continue
            title = str(headline.get("title", "")).strip()
            if not title or (symbol, title) in seen:
                continue
            seen.add((symbol, title))
            assessment = assessments.get(title, {})
            probabilities = assessment.get("probabilities", {})
            article_score = (float(probabilities.get("positive", 0))
                             - float(probabilities.get("negative", 0)))
            score = article_score if probabilities else float(news.get("score", 0) or 0)
            impact = news_impact(score)
            rows.append({
                "Stock": symbol,
                "News": title,
                "Source": headline.get("source") or "Unknown",
                "Read": headline.get("url"),
                "Published": headline.get("published") or "N/A",
                "Impact": impact,
                "Sentiment score": round(score, 2),
                "Likely stock reaction": likely_news_reaction(
                    impact, str(assessment.get("materiality", news.get("materiality", "LOW")))
                ),
            })
    return rows


def show_news(report: dict[str, Any]) -> None:
    rows = news_rows(report)
    st.subheader("Stocks in the news")
    st.caption("Impact is AI-estimated from headline sentiment. It indicates possible short-term "
               "pressure, not a guaranteed price move or trade recommendation.")
    if not rows:
        st.info("No analyzed stock headlines are available in this report. News is fetched only "
                "for the final shortlist when live news analysis is enabled.")
        return
    counts = pd.DataFrame(rows).groupby(["Stock", "Impact"]).size().unstack(fill_value=0)
    st.dataframe(counts.reset_index(), width="stretch", hide_index=True)
    st.dataframe(
        pd.DataFrame(rows), width="stretch", hide_index=True,
        column_config={
            "Sentiment score": st.column_config.NumberColumn(format="%.1f", help="-100 to +100"),
            "News": st.column_config.TextColumn(width="large"),
            "Read": st.column_config.LinkColumn(display_text="Open article"),
            "Likely stock reaction": st.column_config.TextColumn(width="large"),
        },
    )


def show_report(report: dict[str, Any], database: ReportDatabase | None = None) -> None:
    summary_cards(report)
    tabs = st.tabs(["Candidates", "Stocks in news", "Market & context", "Rejected",
                    "Complete report", "JSON"])
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
        show_news(report)
    with tabs[2]:
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
    with tabs[3]:
        rejected = report.get("rejected", [])
        if rejected:
            run_id = str(report.get("run_id", ""))
            for item in rejected:
                with st.expander(str(item.get("symbol", "UNKNOWN"))):
                    symbol = str(item.get("symbol", "UNKNOWN"))
                    st.error("Analytical status: REJECTED. Marking this as TRADED records your "
                             "actual action and does not convert it into an approved recommendation.")
                    if database:
                        start_recommended_trade_control(database, run_id, item)
                    for reason in item.get("reasons", []):
                        st.write(f"• {reason}")
        else:
            st.success("No candidates were rejected.")
    with tabs[4]:
        st.code(DailyReportPresenter.render(report), language="text")
    with tabs[5]:
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
    market_running = feature_is_running("market")
    if st.button("Refresh global markets", disabled=market_running):
        def refresh_markets() -> dict[str, Any]:
            market, sectors = ContextEnrichment(
                True,
                sector_history_provider=(platform.provider
                                         if platform.settings.market_data_source == "kite" else None),
            ).market_and_sectors(force_refresh=True)
            return {"market": market, "sectors": sectors}
        start_feature_job("market", refresh_markets)
        st.success("Market refresh started in the background. You can change pages safely.")
        st.rerun()
    if market_running:
        st.info("Global markets are refreshing in the background.")
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
        submitted = st.form_submit_button("Scan bearish options", type="primary",
                                          disabled=feature_is_running("bearish"))
    if submitted:
        start_feature_job("bearish", lambda: platform.bearish_option_candidates(
            limit=2, minimum_score=int(minimum_score), option_month=option_month.strip() or None
        ))
        st.success("Bearish-options scan started in the background. You can change pages safely.")
        st.rerun()
    if feature_is_running("bearish"):
        st.info("The bearish-options scan is running in the background.")
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


def _latest_trade_price(platform: TradingPlatform, trade: dict[str, Any]) -> float | None:
    """Best available price for status display; failures never hide the position."""
    symbol = str(trade.get("symbol", ""))
    try:
        live_provider = getattr(platform.provider, "provider", None)
        if trade.get("instrument_type") == "EQUITY" and live_provider is not None:
            price = live_provider.get_ltp(symbol)
            return float(price) if price is not None else None
        if trade.get("instrument_type") == "EQUITY":
            history = platform.provider.get_data(symbol)
            if history is not None and not history.empty:
                return float(history["Close"].iloc[-1])
    except Exception:
        return None
    return None


def _trade_status(trade: dict[str, Any], current_price: float | None) -> dict[str, Any]:
    entry = float(trade["entry_price"])
    quantity = int(trade["quantity"])
    direction = 1 if trade["side"] == "BUY" else -1
    pnl = None if current_price is None else round(
        (current_price - entry) * quantity * direction - float(trade.get("fees") or 0), 2
    )
    stop, target = trade.get("stop_loss"), trade.get("target_price")
    stop_hit = current_price is not None and stop is not None and (
        current_price <= float(stop) if direction == 1 else current_price >= float(stop)
    )
    target_hit = current_price is not None and target is not None and (
        current_price >= float(target) if direction == 1 else current_price <= float(target)
    )
    review_due = bool(trade.get("hold_until") and date.today().isoformat() >= trade["hold_until"])
    if stop_hit:
        decision, reason = "EXIT", f"Stop-loss ₹{float(stop):,.2f} has been reached."
    elif target_hit:
        decision, reason = "EXIT / BOOK PROFIT", f"Target ₹{float(target):,.2f} has been reached."
    elif review_due:
        decision, reason = "REVIEW / EXIT", "The planned hold-until date has been reached."
    else:
        decision, reason = "HOLD", "Hold while price remains between stop-loss and target."
    return {"pnl": pnl, "decision": decision, "reason": reason}


def active_trade_status_panel(platform: TradingPlatform, database: ReportDatabase) -> None:
    """Persistent daily view for all trades that have not been completed."""
    open_trades = database.list_actual_trades("OPEN")
    st.subheader("Daily Status — Active Trades")
    if not open_trades:
        st.info("No active trades. Mark an approved recommendation as TRADED to start tracking it.")
        return
    st.caption("These trades remain here on every run until you mark them completed.")
    for trade in open_trades:
        current = _latest_trade_price(platform, trade)
        status = _trade_status(trade, current)
        pnl_label = "Price unavailable" if status["pnl"] is None else f"₹{status['pnl']:,.2f}"
        result = "—" if status["pnl"] is None else ("PROFIT" if status["pnl"] >= 0 else "LOSS")
        with st.container(border=True):
            st.markdown(f"### {trade['symbol']} · {status['decision']}")
            columns = st.columns(5)
            columns[0].metric("Entry", f"₹{trade['entry_price']:,.2f}")
            columns[1].metric("Current", "Unavailable" if current is None else f"₹{current:,.2f}")
            columns[2].metric("Unrealized P&L", pnl_label)
            columns[3].metric("Position", result)
            columns[4].metric("Hold until", trade.get("hold_until") or "Target / stop")
            st.write(status["reason"])
            st.caption(f"Stop: {value(trade.get('stop_loss'))} · Target: "
                       f"{value(trade.get('target_price'))} · Quantity: {trade['quantity']}")
            with st.form(f"complete-active-{trade['id']}"):
                finish = st.columns(3)
                exit_price = finish[0].number_input(
                    "Exit price", min_value=0.01, value=float(current or trade["entry_price"]),
                    step=0.05, key=f"daily-exit-price-{trade['id']}")
                exit_date = finish[1].date_input("Exit date", value=date.today(),
                                                 key=f"daily-exit-date-{trade['id']}")
                exit_fees = finish[2].number_input("Exit fees", min_value=0.0, value=0.0,
                                                   key=f"daily-exit-fees-{trade['id']}")
                completed = st.form_submit_button("Mark completed", type="primary")
            if completed:
                pnl = database.close_actual_trade(trade["id"], exit_date.isoformat(),
                                                  exit_price, exit_fees)
                outcome = "PROFIT" if pnl >= 0 else "LOSS"
                st.success(f"Completed as {outcome}. Final P&L: ₹{pnl:,.2f}")
                st.rerun()


def daily_report_page(platform: TradingPlatform, database: ReportDatabase) -> None:
    st.title("Daily report")
    active_trade_status_panel(platform, database)
    st.divider()
    active_future = daily_report_jobs().future(st.session_state.get("daily_report_job_id"))
    job_running = active_future is not None and not active_future.done()
    with st.form("daily-report-form"):
        left, middle, right = st.columns(3)
        limit = left.number_input("Maximum final trades", 1, 50, 5)
        minimum_score = middle.number_input("Minimum technical score", 0, 100, 40)
        option_month = right.text_input("Option month (optional)", placeholder="YYYY-MM")
        submitted = st.form_submit_button("Run report", type="primary", disabled=job_running)
    if submitted:
        job_id = daily_report_jobs().submit(
            platform, database, int(limit), int(minimum_score), option_month.strip() or None
        )
        st.session_state["daily_report_job_id"] = job_id
        st.session_state["daily_report_synced_job_id"] = None
        st.session_state["daily_report_job_error"] = None
        # Do not leave the previous report on screen while a fresh live-market
        # report is running; it makes stale output appear to be the new result.
        st.session_state.pop("current_report", None)
        st.success("Daily report started in the background. You can open History or any other page.")
        st.rerun()
    if job_running:
        st.info("A daily report is running in the background. Navigation will not stop it.")
    report = None if job_running else st.session_state.get("current_report")
    if report:
        show_report(report, database)


def analyze_page(platform: TradingPlatform) -> None:
    st.title("Analyze one stock")
    with st.form("analyze-form"):
        symbol = st.text_input("NSE symbol", placeholder="RELIANCE").strip().upper()
        submitted = st.form_submit_button("Analyze", type="primary",
                                          disabled=feature_is_running("analysis"))
    if submitted and symbol:
        start_feature_job("analysis", lambda: platform.analyze(symbol))
        st.session_state["analysis_symbol"] = symbol
        st.success(f"Analysis for {symbol} started in the background. You can change pages safely.")
        st.rerun()
    if feature_is_running("analysis"):
        st.info(f"Analysis for {st.session_state.get('analysis_symbol', symbol)} is running in the background.")
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


def trade_tracker_page(platform: TradingPlatform, database: ReportDatabase) -> None:
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
    active_trade_status_panel(platform, database)

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
            hold_until = st.date_input("Hold until / review date", value=date.today() + timedelta(days=10),
                                       min_value=entry_date)
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
                    "hold_until": hold_until.isoformat(),
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
    trade_frame = pd.DataFrame(trades)
    trade_frame["outcome"] = trade_frame.apply(
        lambda row: ("ACTIVE" if row["status"] == "OPEN" else
                     "PROFIT" if float(row["realized_pnl"] or 0) >= 0 else "LOSS"), axis=1)
    display_columns.insert(13, "hold_until")
    display_columns.insert(15, "outcome")
    st.dataframe(trade_frame[display_columns], width="stretch", hide_index=True)

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
    sync_feature_jobs()
    with st.sidebar:
        st.header("Navigation")
        page = st.radio("Page", ("Dashboard", "Daily report", "Bearish options", "Analyze",
                                 "Trade tracker", "History", "System"),
                        label_visibility="collapsed")
        st.divider()
        st.caption(f"Source: {platform.settings.market_data_source.upper()}")
        st.caption("Paper trading only")
        daily_report_status()
    if page == "Dashboard":
        dashboard(platform, database)
    elif page == "Daily report":
        daily_report_page(platform, database)
    elif page == "Analyze":
        analyze_page(platform)
    elif page == "Bearish options":
        bearish_options_page(platform)
    elif page == "Trade tracker":
        trade_tracker_page(platform, database)
    elif page == "History":
        history_page(database)
    else:
        system_page(platform, database)


if __name__ == "__main__":
    main()
