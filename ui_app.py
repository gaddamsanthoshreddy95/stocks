"""Windows-friendly local Streamlit interface for the trading platform."""

from __future__ import annotations

import json
import inspect
import os
from pathlib import Path
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from threading import Lock
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from src.application.errors import PlatformError
from src.application.platform import TradingPlatform
from src.assistant import (CodexService, CodexUnavailableError, OpenAIAnalyst,
                           OpenAIAuthenticationError, OpenAIConfigurationError,
                           OllamaAnalyst, OllamaConfigurationError, StockAnalyzerTools,
                           GeminiAnalyst, GeminiAuthenticationError,
                           GeminiConfigurationError)
from src.assistant.context_tools import UIContext
from src.news.ai_sentiment import AISentimentAnalyzer
from src.presenter.daily_report import DailyReportPresenter
from src.ui.database import ReportDatabase
from src.ui.live_prices import KiteLivePriceFeed
from src.ui.stock_explainer import explain_stock_question
from src.workflow.context_enrichment import ContextEnrichment


st.set_page_config(page_title="Alphatrace", page_icon="📈", layout="wide")


APP_CSS = """
<style>
    .block-container {padding-top: 1.7rem; padding-bottom: 3rem; max-width: 1500px;}
    [data-testid="stSidebar"] {border-right: 1px solid rgba(125,125,125,.18);}
    [data-testid="stMetric"] {background: rgba(125,125,125,.055); border: 1px solid
        rgba(125,125,125,.18); border-radius: 12px; padding: .8rem 1rem;}
    div[data-testid="stVerticalBlockBorderWrapper"] {border-radius: 14px;}
    [data-testid="stMetricValue"] {font-size:clamp(1rem, 1.55vw, 1.65rem); white-space:normal;
        overflow-wrap:anywhere; line-height:1.2;}
    [data-testid="stMetricDelta"] {white-space:normal; overflow-wrap:anywhere;}
    .hero {padding: 1.15rem 1.3rem; border: 1px solid rgba(99,102,241,.28);
        border-radius: 16px; background: linear-gradient(120deg, rgba(37,99,235,.14),
        rgba(16,185,129,.06)); margin-bottom: 1rem;}
    .hero h1 {font-size: 1.65rem; margin: 0 0 .25rem 0;}
    .hero p {margin: 0; opacity: .75;}
    .badge {display:inline-block; padding:.2rem .55rem; border-radius:999px;
        font-size:.72rem; font-weight:700; letter-spacing:.03em; margin:0; white-space:nowrap;}
    .badge-row {display:flex; flex-wrap:wrap; gap:.4rem; margin:.25rem 0 .75rem;}
    .good {color:#34d399; background:rgba(16,185,129,.14);}
    .warn {color:#fbbf24; background:rgba(245,158,11,.14);}
    .bad {color:#fb7185; background:rgba(244,63,94,.14);}
    .info {color:#60a5fa; background:rgba(59,130,246,.14);}
    .muted {color:#a1a1aa; background:rgba(113,113,122,.14);}
    .candidate-title {font-size:1.15rem; font-weight:750; margin-bottom:.35rem;}
    .candidate-reason {opacity:.78; min-height:2.8rem; overflow-wrap:anywhere; line-height:1.45;}
    .candidate-metrics {display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:.5rem;
        margin:.8rem 0;}
    .candidate-metric {min-width:0; padding:.55rem .65rem; border-radius:10px;
        background:rgba(125,125,125,.06); border:1px solid rgba(125,125,125,.14);}
    .candidate-metric small {display:block; opacity:.7; margin-bottom:.15rem;}
    .candidate-metric strong {display:block; font-size:clamp(.88rem,1.1vw,1.1rem);
        overflow-wrap:anywhere; line-height:1.25;}
    .candidate-targets {overflow-wrap:anywhere; line-height:1.45; margin-bottom:.65rem; opacity:.78;}
    .section-note {opacity:.68; font-size:.82rem;}
    .health-strip {display:flex; flex-wrap:wrap; gap:.45rem; padding:.7rem .85rem;
        border:1px solid rgba(125,125,125,.18); border-radius:12px; margin:.4rem 0 1rem;}
    :focus-visible {outline: 3px solid #60a5fa!important; outline-offset: 2px;}
    @media (max-width: 760px) {
        .block-container {padding: .8rem .7rem 2rem;}
        .hero {padding: .9rem;}
        .hero h1 {font-size:1.35rem;}
        [data-testid="stHorizontalBlock"] {flex-wrap:wrap;}
        [data-testid="column"] {min-width: min(100%, 260px); flex: 1 1 46%!important;}
        .candidate-metrics {grid-template-columns:1fr 1fr;}
        button {min-height:44px;}
    }
</style>
"""


def apply_theme(preferences: dict[str, Any] | None = None) -> None:
    st.markdown(APP_CSS, unsafe_allow_html=True)
    preferences = preferences or {}
    if preferences.get("high_contrast"):
        st.markdown("<style>[data-testid='stMetric'], div[data-testid='stVerticalBlockBorderWrapper']"
                    "{border-width:2px!important} .section-note,.candidate-reason{opacity:1!important}</style>",
                    unsafe_allow_html=True)
    if preferences.get("reduce_motion"):
        st.markdown("<style>*,*::before,*::after{scroll-behavior:auto!important;animation:none!important;"
                    "transition:none!important}</style>", unsafe_allow_html=True)
    if preferences.get("compact_mode"):
        st.markdown("<style>.block-container{padding-top:.8rem}.stElementContainer{margin-bottom:-.15rem}</style>",
                    unsafe_allow_html=True)


def money(number: Any, fallback: str = "—") -> str:
    try:
        return f"₹{float(number):,.2f}" if number is not None else fallback
    except (TypeError, ValueError):
        return fallback


def number(number: Any, digits: int = 2, fallback: str = "—") -> str:
    try:
        return f"{float(number):,.{digits}f}" if number is not None else fallback
    except (TypeError, ValueError):
        return fallback


def status_class(status: Any) -> str:
    label = str(status or "UNAVAILABLE").upper()
    if label in {"TRADE", "APPROVED", "BUY", "BUY NOW", "AVAILABLE", "OPEN", "PROFIT"}:
        return "good"
    if label in {"REJECTED", "REJECT", "SELL", "EXIT", "LOSS", "FAILED"}:
        return "bad"
    if "WAIT" in label or label in {"WATCH", "WATCHLIST", "REVIEW", "NEUTRAL"}:
        return "warn"
    return "muted" if label in {"UNAVAILABLE", "UNKNOWN", "STALE"} else "info"


def badge(status: Any) -> str:
    label = str(status or "UNAVAILABLE").replace("_", " ").upper()
    return f'<span class="badge {status_class(label)}">{label}</span> '


def hero(title: str, subtitle: str, badges: list[Any] | None = None) -> None:
    labels = "".join(badge(item) for item in (badges or []))
    labels = f'<div class="badge-row">{labels}</div>' if labels else ""
    st.markdown(f'<div class="hero"><h1>{title}</h1><p>{subtitle}</p>'
                f'{labels}</div>',
                unsafe_allow_html=True)


@st.cache_resource
def services() -> tuple[TradingPlatform, ReportDatabase]:
    return TradingPlatform(), ReportDatabase()


def missing_required_credentials(environment: dict[str, str] | None = None) -> list[str]:
    """Return credentials that must exist before the selected data provider can start."""
    environment = environment or os.environ
    if str(environment.get("MARKET_DATA_SOURCE", "kite")).lower() != "kite":
        return []
    return [name for name in ("KITE_API_KEY", "KITE_ACCESS_TOKEN")
            if not str(environment.get(name, "")).strip()]


def apply_session_credentials() -> None:
    """Restore temporary secrets on every Streamlit rerun without persisting them."""
    for name in ("KITE_API_KEY", "KITE_ACCESS_TOKEN", "OPENAI_API_KEY"):
        value_ = st.session_state.get(f"_session_credential_{name}")
        if value_:
            os.environ[name] = value_


def required_credential_gate() -> None:
    """Ask for required broker credentials before constructing the application platform."""
    missing = missing_required_credentials()
    if not missing:
        return
    apply_theme({})
    hero("Connect market data", "Required credentials are missing. Sign in to continue; "
         "values are kept only in this Streamlit process.", ["CREDENTIALS REQUIRED"])
    st.warning("Kite market-data mode cannot start until the required credentials are supplied. "
               "Nothing entered here is written to SQLite or project files.")
    with st.form("required-kite-credentials", clear_on_submit=True):
        api_key = (st.text_input("Kite API key", type="password", autocomplete="off")
                   if "KITE_API_KEY" in missing else "")
        access_token = (st.text_input("Kite access token", type="password", autocomplete="off",
                                      help="Kite access tokens normally need daily renewal.")
                        if "KITE_ACCESS_TOKEN" in missing else "")
        submitted = st.form_submit_button("Sign in and continue", type="primary")
    if submitted:
        supplied = {"KITE_API_KEY": api_key, "KITE_ACCESS_TOKEN": access_token}
        empty = [name for name in missing if not supplied.get(name, "").strip()]
        if empty:
            st.error("Enter all required values: " + ", ".join(empty))
        else:
            for name in missing:
                value_ = supplied[name].strip()
                st.session_state[f"_session_credential_{name}"] = value_
                os.environ[name] = value_
            services.clear()
            st.rerun()
    st.caption("For persistent local configuration, place KITE_API_KEY and KITE_ACCESS_TOKEN in "
               "`.env`, then restart. Never commit that file.")
    st.stop()


@st.cache_resource
def kite_live_price_feed(_provider: Any) -> KiteLivePriceFeed:
    return KiteLivePriceFeed(_provider)


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
            open_symbols = {trade["symbol"] for trade in database.list_actual_trades("OPEN")}
            parameters = inspect.signature(platform.daily_report).parameters
            report = (platform.daily_report(
                limit, minimum_score, option_month, excluded_symbols=open_symbols
            ) if "excluded_symbols" in parameters else
                platform.daily_report(limit, minimum_score, option_month))
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
    st.session_state[f"{feature}_job_started_at"] = datetime.now().timestamp()


def job_progress(feature: str) -> tuple[int, str]:
    elapsed = max(0, datetime.now().timestamp() - float(
        st.session_state.get(f"{feature}_job_started_at", datetime.now().timestamp())))
    stages = ((0, 12, "Connecting to data sources"), (12, 30, "Calculating technical signals"),
              (30, 50, "Checking context and events"), (50, 70, "Validating trade structures"),
              (70, 88, "Ranking candidates"), (88, 95, "Finalizing results"))
    index = min(int(elapsed // 8), len(stages) - 1)
    minimum, maximum, label = stages[index]
    progress = min(maximum, minimum + int(elapsed % 8 / 8 * (maximum - minimum)))
    return progress, f"{label} · {int(elapsed)}s elapsed"


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
            progress, label = job_progress("daily_report")
            st.caption("Daily report · " + label)
            st.progress(progress, text="Analysis in progress")
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
            progress, progress_label = job_progress(feature)
            st.caption(f"{label} · {progress_label}")
            st.progress(progress)
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


def display_date(raw: Any, fallback: str = "Target / stop") -> str:
    """Render stored ISO dates without allowing narrow metric cards to clip them."""
    if not raw:
        return fallback
    try:
        return date.fromisoformat(str(raw)[:10]).strftime("%d %b %Y")
    except ValueError:
        return str(raw)


def data_age(raw: Any, now: datetime | None = None) -> str:
    """Return a compact, user-facing age for a timestamp."""
    if not raw:
        return "unknown age"
    try:
        timestamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        seconds = max(0, int(((now or datetime.now(timezone.utc)) - timestamp).total_seconds()))
    except (TypeError, ValueError):
        return "unknown age"
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def system_health(platform: TradingPlatform, database: ReportDatabase,
                  latest: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """Summarize operational dependencies without initiating network calls."""
    reports = database.list_reports(1)
    generated_at = reports[0].get("generated_at") if reports else None
    source = str(platform.settings.market_data_source).upper()
    news_states = []
    for candidate in [*(latest or {}).get("trades", []), *(latest or {}).get("watchlist", [])]:
        news_states.append(str((candidate.get("news") or {}).get("news_state", "NOT_REQUESTED")))
    news_status = ("FAILED" if any(state in {"FETCH_FAILED", "FAILED"} for state in news_states)
                   else "READY" if any(state == "ANALYZED" for state in news_states)
                   else "NOT CHECKED")
    return [
        {"name": "Market data", "status": "LIVE" if source == "KITE" else "CACHED"},
        {"name": "News", "status": news_status},
        {"name": "Database", "status": "READY"},
        {"name": "Latest report", "status": data_age(generated_at)},
        {"name": "Trading", "status": "PAPER ONLY"},
    ]


def render_health_strip(platform: TradingPlatform, database: ReportDatabase,
                        latest: dict[str, Any] | None = None) -> None:
    labels = "".join(
        f"<span>{item['name']}: {badge(item['status'])}</span>"
        for item in system_health(platform, database, latest)
    )
    st.markdown(f'<div class="health-strip">{labels}</div>', unsafe_allow_html=True)


def request_navigation(page: str) -> None:
    """Defer page changes until before the sidebar radio is created next run."""
    st.session_state["pending_nav_page"] = str(page)
    st.rerun()


def render_metric_cards(metrics: list[tuple[str, Any]], per_row: int = 3) -> None:
    """Render metrics in bounded rows so values remain readable at common widths."""
    per_row = max(1, min(int(per_row), 3))
    for start in range(0, len(metrics), per_row):
        row = metrics[start:start + per_row]
        columns = st.columns(len(row))
        for column, (label, metric) in zip(columns, row):
            column.metric(label, metric)


def summary_cards(report: dict[str, Any]) -> None:
    summary = report.get("summary", {})
    metrics = [
        ("Stocks scanned", summary.get("stocks_scanned", 0)),
        ("Candidates reviewed", summary.get("context_reviewed", 0)),
        ("Trades", summary.get("trades_generated", 0)),
        ("Watchlist", summary.get("watchlisted", 0)),
        ("Market", report.get("market", {}).get("regime", "UNAVAILABLE")),
    ]
    render_metric_cards(metrics)


def adverse_before_target_probability(risk: dict[str, Any] | None) -> float | None:
    """Read the direct value, with compatibility for reports saved before it existed."""
    risk = risk or {}
    direct = risk.get("probability_adverse_barrier_before_target")
    if direct is not None:
        return float(direct)
    stayed_above = risk.get("probability_stays_above_adverse_barrier")
    return None if stayed_above is None else round(100 - float(stayed_above), 2)


def _compact_percent(value_: Any, default: str = "—") -> str:
    if value_ is None:
        return default
    numeric = float(value_)
    return f"{numeric:g}%"


def adverse_risk_view(risk: dict[str, Any] | None) -> dict[str, Any]:
    """Build consistent, plain-language adverse-path content for every UI surface."""
    risk = risk or {}
    probability = adverse_before_target_probability(risk)
    barrier = risk.get("adverse_barrier_percent")
    target = risk.get("target_percent")
    direction = str(risk.get("direction") or "BULLISH").upper()
    movement = "Rise" if direction == "BEARISH" else "Fall"
    past_tense = "rose" if direction == "BEARISH" else "fell"
    barrier_text = _compact_percent(barrier, "adverse barrier")
    label = f"{movement} {barrier_text} before target"
    sample_count = int(risk.get("sample_count") or 0)
    available = bool(risk.get("available")) and probability is not None
    reliability = str(risk.get("reliability") or (
        "HIGH" if available and risk.get("data_resolution") == "15_MINUTE"
        else "MEDIUM" if available else "UNAVAILABLE"
    )).replace("_", " ")
    if available:
        cases = f"{probability:g}"
        explanation = (f"In {cases} of every 100 comparable historical cases, the stock "
                       f"{past_tense} {barrier_text} before reaching this target.")
    else:
        explanation = ("This probability is unavailable. "
                       + str(risk.get("reason") or "There is not enough comparable history."))
    return {
        "available": available, "probability": probability, "label": label,
        "explanation": explanation, "sample_count": sample_count,
        "minimum_samples": risk.get("minimum_samples"),
        "horizon": (f"{int(risk['horizon_days'])} trading days"
                    if risk.get("horizon_days") is not None else "Unavailable"),
        "target": _compact_percent(target, "Unavailable"),
        "barrier": barrier_text,
        "resolution": str(risk.get("data_resolution") or "UNAVAILABLE").replace("_", " "),
        "reliability": reliability,
        "status": "AVAILABLE" if available else "UNAVAILABLE",
    }


def render_adverse_risk_panel(risk: dict[str, Any] | None) -> None:
    """Show the percentage first, followed by its meaning and evidence quality."""
    view = adverse_risk_view(risk)
    probability = f"{view['probability']:.1f}%" if view["probability"] is not None else "—"
    st.markdown("**Historical price-path risk**")
    render_metric_cards([
        (view["label"], probability),
        ("Comparable cases", view["sample_count"]),
        ("Time horizon", view["horizon"]),
        ("Target move", view["target"]),
        ("Data resolution", view["resolution"]),
        ("Estimate reliability", view["reliability"]),
    ])
    (st.info if view["available"] else st.warning)(view["explanation"])
    minimum = view.get("minimum_samples")
    if minimum is not None:
        st.caption(f"Availability: {view['status']} · Sample requirement: at least {minimum} comparable cases.")


def candidate_rows(report: dict[str, Any], execution_marks: dict[str, str] | None = None) -> list[dict[str, Any]]:
    execution_marks = execution_marks or {}
    rows = []
    for trade in [*report.get("trades", []), *report.get("watchlist", [])]:
        event = trade.get("event_risk", {})
        stability = trade.get("selection_stability") or {}
        zones = trade.get("supply_demand") or {}
        demand = zones.get("nearest_demand") or {}
        supply = zones.get("nearest_supply") or {}
        recovery = trade.get("intraday_recovery") or {}
        rows.append({
            "Symbol": trade.get("symbol"), "Status": trade.get("status"),
            "Entry status": trade.get("selection_status", "UNAVAILABLE"),
            "Trigger price": (trade.get("entry_selection") or {}).get("trigger_price"),
            "Selection reason": trade.get("selection_reason"),
            "Selection stability": stability.get("status"),
            "Recent appearances": stability.get("appearances"),
            "Executable trade": "YES" if trade.get("status") == "TRADE" else "NO",
            "Actually traded": execution_marks.get(str(trade.get("symbol")), "NOT_TRADED"),
            "Action": trade.get("final_action"), "Quality": trade.get("quality_grade"),
            "Quality score": trade.get("quality_score"),
            "Stock filters": "PASS" if trade.get("stock_selection_filters", {}).get("passed") else "FAIL",
            "Failed filters": ", ".join(trade.get("stock_selection_filters", {}).get("failed_checks", [])),
            "Readiness": trade.get("execution_readiness_score"),
            "R:R": trade.get("levels", {}).get("risk_reward"),
            "Support": trade.get("levels", {}).get("support"),
            "Resistance": trade.get("levels", {}).get("resistance"),
            "Demand zone": (
                f"{demand.get('lower')}–{demand.get('upper')}" if demand else "UNAVAILABLE"
            ),
            "Supply zone": (
                f"{supply.get('lower')}–{supply.get('upper')}" if supply else "UNAVAILABLE"
            ),
            "15m recovery": recovery.get("state", "NOT_REQUIRED"),
            "Recovery score": recovery.get("score"),
            "Relative strength": trade.get("relative_strength", {}).get("score"),
            "RS status": trade.get("relative_strength", {}).get("status"),
            "Stay above adverse barrier": trade.get("adverse_move_risk", {}).get(
                "probability_stays_above_adverse_barrier"),
            "Target before adverse barrier": trade.get("adverse_move_risk", {}).get(
                "probability_target_before_adverse_barrier"),
            "Adverse move before target": adverse_before_target_probability(
                trade.get("adverse_move_risk")),
            "Adverse barrier": trade.get("adverse_move_risk", {}).get("adverse_barrier_percent"),
            "No overnight gap beyond barrier": trade.get("adverse_move_risk", {}).get(
                "probability_no_overnight_gap_beyond_barrier"),
            "Event risk": event.get("event_risk_score"),
            "Event data": event.get("event_data_availability_state"),
            "Option approval": trade.get("option_trade_approval", {}).get("status"),
        })
    return rows


def option_confirmation(candidate: dict[str, Any]) -> dict[str, Any]:
    """Summarize existing Zerodha option evidence without inventing missing signals."""
    approval = candidate.get("option_trade_approval") or {}
    option = candidate.get("option_strategy") or {}
    plan = option.get("trade") or {}
    legs = plan.get("legs") or []
    context = candidate.get("option_context") or {}
    directional = str(candidate.get("option_status") or
                      context.get("directional_support") or "UNAVAILABLE").upper()
    scores: list[float] = []
    reasons: list[str] = []
    if approval:
        approved = bool(approval.get("approved") or approval.get("status") == "APPROVED")
        scores.append(100.0 if approved else 25.0)
        reasons.append("structure approved" if approved else "structure not approved")
    if directional not in {"", "UNAVAILABLE", "UNKNOWN", "NONE"}:
        directional_score = {"CONFIRMED": 100, "STRONG": 100, "SUPPORTIVE": 85,
                             "NEUTRAL": 55, "MIXED": 45, "WEAK": 35,
                             "CONFLICT": 10, "UNRELIABLE": 10}.get(directional, 50)
        scores.append(float(directional_score))
        reasons.append(f"directional evidence {directional.lower()}")
    spreads = []
    liquid_legs = 0
    for leg in legs:
        bid, ask = leg.get("bid"), leg.get("ask")
        try:
            midpoint = (float(bid) + float(ask)) / 2
            if midpoint > 0:
                spreads.append((float(ask) - float(bid)) / midpoint * 100)
        except (TypeError, ValueError):
            pass
        try:
            liquid_legs += int(float(leg.get("open_interest") or 0) > 0 and
                               float(leg.get("volume") or 0) > 0)
        except (TypeError, ValueError):
            pass
    if spreads:
        average_spread = sum(spreads) / len(spreads)
        scores.append(90 if average_spread <= 2 else 70 if average_spread <= 5 else
                      40 if average_spread <= 10 else 15)
        reasons.append(f"average bid/ask spread {average_spread:.1f}%")
    else:
        average_spread = None
    if legs:
        liquidity_ratio = liquid_legs / len(legs)
        scores.append(liquidity_ratio * 100)
        reasons.append(f"{liquid_legs}/{len(legs)} legs have OI and volume")
    if not scores:
        return {"score": None, "label": "Unavailable", "reason": "No option evidence recorded.",
                "spread_percent": None}
    score = round(sum(scores) / len(scores))
    label = "Confirmed" if score >= 75 else "Mixed" if score >= 45 else "Avoid options"
    return {"score": score, "label": label, "reason": "; ".join(reasons),
            "spread_percent": round(average_spread, 2) if average_spread is not None else None}


def freshness_status(generated_at: Any, now: datetime | None = None) -> dict[str, str]:
    """Classify report age so cached or old recommendations are visually obvious."""
    age = data_age(generated_at, now)
    try:
        timestamp = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        hours = max(0, ((now or datetime.now(timezone.utc)) - timestamp).total_seconds() / 3600)
    except (TypeError, ValueError):
        return {"label": "UNKNOWN", "age": age}
    label = "FRESH" if hours <= 1 else "AGING" if hours <= 24 else "STALE"
    return {"label": label, "age": age}


def ranking_explanation(candidate: dict[str, Any]) -> str:
    """Explain the strongest positive and negative contributors in plain language."""
    positives, negatives = [], []
    quality = candidate.get("quality_score")
    readiness = candidate.get("execution_readiness_score")
    rr = (candidate.get("levels") or {}).get("risk_reward")
    relative = (candidate.get("relative_strength") or {}).get("score")
    options = option_confirmation(candidate)
    if quality is not None:
        (positives if float(quality) >= 70 else negatives).append(f"quality {float(quality):.0f}")
    if readiness is not None:
        (positives if float(readiness) >= 70 else negatives).append(f"readiness {float(readiness):.0f}")
    if rr is not None:
        (positives if float(rr) >= 2 else negatives).append(f"R:R {float(rr):.2f}")
    if relative is not None:
        (positives if float(relative) >= 60 else negatives).append(f"relative strength {float(relative):.0f}")
    if options["score"] is not None:
        (positives if options["score"] >= 75 else negatives).append(
            f"options {options['label'].lower()} ({options['score']})")
    parts = []
    if positives:
        parts.append("Supports: " + ", ".join(positives[:3]))
    if negatives:
        parts.append("Limits: " + ", ".join(negatives[:3]))
    return ". ".join(parts) or "Insufficient scoring evidence."


def ranked_opportunity_rows(candidates: list[dict[str, Any]], generated_at: Any,
                            previous: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Build one sortable decision table across trades, watchlist, and rejections."""
    changes = {row["Symbol"]: row for row in report_changes(
        {"trades": [c for c in candidates if c.get("_opportunity_source") == "TRADE"],
         "watchlist": [c for c in candidates if c.get("_opportunity_source") == "WATCHLIST"],
         "rejected": [c for c in candidates if c.get("_opportunity_source") == "REJECTED"]}, previous)}
    freshness = freshness_status(generated_at)
    ordered = sorted(candidates, key=lambda item: (
        -float(item.get("quality_score") or 0),
        -float(item.get("execution_readiness_score") or 0), str(item.get("symbol"))))
    rows = []
    for rank, item in enumerate(ordered, 1):
        option = option_confirmation(item)
        change = changes.get(str(item.get("symbol")), {})
        rows.append({"Rank": rank, "Symbol": item.get("symbol"),
                     "Action": item.get("final_action") or item.get("status"),
                     "Quality": item.get("quality_score"),
                     "Readiness": item.get("execution_readiness_score"),
                     "R:R": (item.get("levels") or {}).get("risk_reward"),
                     "Sector strength": (item.get("sector_context") or {}).get("score"),
                     "Options": option["label"], "Option score": option["score"],
                     "Data": f"{freshness['label']} · {freshness['age']}",
                     "Change": change.get("Change", "UNCHANGED"),
                     "Score Δ": change.get("Score change"),
                     "Why ranked": ranking_explanation(item),
                     "Primary blocker": primary_blocker(item) if
                     item.get("_opportunity_source") == "REJECTED" else "—"})
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


def candidate_table_config() -> dict[str, Any]:
    return {
        "Quality score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
        "Readiness": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
        "R:R": st.column_config.NumberColumn(format="%.2f"),
        "Support": st.column_config.NumberColumn(format="₹%.2f"),
        "Resistance": st.column_config.NumberColumn(format="₹%.2f"),
        "Trigger price": st.column_config.NumberColumn(format="₹%.2f"),
        "Event risk": st.column_config.NumberColumn(format="%.0f"),
        "Stay above adverse barrier": st.column_config.NumberColumn(format="%.1f%%"),
        "Target before adverse barrier": st.column_config.NumberColumn(format="%.1f%%"),
        "Adverse move before target": st.column_config.NumberColumn(format="%.1f%%"),
        "Adverse barrier": st.column_config.NumberColumn(format="%.1f%%"),
        "No overnight gap beyond barrier": st.column_config.NumberColumn(format="%.1f%%"),
        "Selection reason": st.column_config.TextColumn(width="large"),
    }


def decision_checks(trade: dict[str, Any]) -> list[dict[str, Any]]:
    """Translate overlapping backend gates into one consistent UI checklist."""
    news = trade.get("news") or {}
    event = trade.get("event_risk") or {}
    eligibility = trade.get("trade_eligibility") or {}
    confirmation = trade.get("entry_confirmation") or {}
    risk = trade.get("risk") or trade.get("position_size") or {}
    recovery = trade.get("intraday_recovery") or {}
    return [
        {"label": "Policy permits execution",
         "passed": bool(eligibility.get("eligible", trade.get("status") == "TRADE"))},
        {"label": "Entry confirmation is complete",
         "passed": bool(confirmation.get("passed", trade.get("selection_status") == "BUY NOW"))},
        {"label": "Sharp-fall recovery is confirmed or not required",
         "passed": not recovery.get("required") or bool(recovery.get("confirmed"))},
        {"label": "News evidence is complete",
         "passed": news.get("news_state") in {"ANALYZED", "NO_RELEVANT_NEWS"}},
        {"label": "No hard event-risk block",
         "passed": not bool(event.get("hard_block") or event.get("block_new_trades"))},
        {"label": "Position size is positive",
         "passed": int(risk.get("quantity") or 0) > 0},
    ]


def opportunity_groups(candidates: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Assign every candidate to exactly one next-action group."""
    groups: dict[str, list[dict[str, Any]]] = {
        "Buy now": [], "Wait for confirmation": [], "Watchlist": [], "No trade / blocked": [],
    }
    for item in candidates:
        status = str(item.get("status", "")).upper()
        action = str(item.get("final_action", "")).upper()
        selection = str(item.get("selection_status", "")).upper()
        source = str(item.get("_opportunity_source", "")).upper()
        if source == "REJECTED" or status == "REJECTED" or action in {"NO_TRADE", "REJECT"}:
            group = "No trade / blocked"
        elif status == "TRADE" and (selection == "BUY NOW" or action in {"BUY", "TRADE"}):
            group = "Buy now"
        elif "WAIT" in selection or "WAIT" in action:
            group = "Wait for confirmation"
        else:
            group = "Watchlist"
        groups[group].append(item)
    return groups


def primary_blocker(candidate: dict[str, Any]) -> str:
    """Classify the most actionable reason a candidate did not become a trade."""
    text = " ".join(str(item) for item in [candidate.get("selection_reason"),
                    candidate.get("rejection_reason"), *(candidate.get("reasons") or [])]).lower()
    if "risk/reward" in text or "risk reward" in text:
        return "Insufficient risk/reward"
    if "entry" in text or "confirmation" in text or "breakout" in text or "pullback" in text:
        return "Entry confirmation"
    if "market" in text or "regime" in text:
        return "Market regime"
    if "news" in text or "event" in text or "earnings" in text:
        return "News / event risk"
    if "liquid" in text or "volume" in text or "spread" in text:
        return "Liquidity"
    if "stability" in text or "persistent" in text or "appearance" in text:
        return "Selection stability"
    if "unavailable" in text or "missing" in text or "failed" in text:
        return "Missing data"
    return "Other policy gate"


def trade_override_required(candidate: dict[str, Any]) -> bool:
    status = str(candidate.get("status") or "UNKNOWN").upper()
    action = str(candidate.get("final_action") or candidate.get("action") or "UNKNOWN").upper()
    return status != "TRADE" or action in {
        "NO_TRADE", "REJECT", "WAIT", "WAIT_FOR_CONFIRMATION", "WATCHLIST"
    }


def rejection_summary(rejected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, list[str]] = {}
    for item in rejected:
        counts.setdefault(primary_blocker(item), []).append(str(item.get("symbol", "UNKNOWN")))
    return [{"Blocker": blocker, "Count": len(symbols), "Symbols": ", ".join(symbols)}
            for blocker, symbols in sorted(counts.items(), key=lambda pair: (-len(pair[1]), pair[0]))]


def candidate_history_rows(reports: list[dict[str, Any]], symbol: str) -> list[dict[str, Any]]:
    """Extract one symbol's progression from ordered immutable reports."""
    rows = []
    for report in reports:
        candidate = None
        bucket_name = ""
        for bucket in ("trades", "watchlist", "rejected"):
            candidate = next((item for item in report.get(bucket, [])
                              if str(item.get("symbol", "")).upper() == symbol.upper()), None)
            if candidate is not None:
                bucket_name = bucket
                break
        if candidate is None:
            continue
        rows.append({"Date": report.get("date"), "Status": candidate.get("status") or bucket_name.upper(),
                     "Action": candidate.get("final_action"), "Quality": candidate.get("quality_score"),
                     "Readiness": candidate.get("execution_readiness_score"),
                     "R:R": (candidate.get("levels") or {}).get("risk_reward")})
    return rows


def price_action_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Project technical setup data into one decision-oriented UI record."""
    entries = result.get("entries") or []
    entry = max(entries, key=lambda item: float(item.get("confidence") or 0), default={})
    score = result.get("score") or {}
    retest, breakout = result.get("retest") or {}, result.get("breakout") or {}
    patterns = result.get("patterns") or []
    setup = (entry.get("setup") or (patterns[0].get("pattern") if patterns else None)
             or ((result.get("reversals") or [{}])[-1].get("setup")) or "No active setup")
    return {
        "direction": result.get("direction") or "NEUTRAL",
        "category": score.get("category") or "REJECT",
        "setup": setup,
        "entry_mode": entry.get("entry_mode") or "NOT_READY",
        "entry": entry.get("entry_price"),
        "stop": entry.get("stop_loss"),
        "targets": entry.get("targets") or [],
        "risk_reward": entry.get("risk_reward") or [],
        "confidence": entry.get("confidence", score.get("score", 0)),
        "breakout": breakout.get("quality", "NONE"),
        "retest": retest.get("retest_quality", "NONE"),
        "warning": next(iter(result.get("rejection_reasons") or []), None),
    }


def setup_lifecycle_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Return Pattern → Breakout → Retest → Confirmation → Entry states."""
    pattern = (result.get("patterns") or [{}])[0]
    breakout, retest = result.get("breakout") or {}, result.get("retest") or {}
    entry = (result.get("entries") or [{}])[0]
    failed_retest = retest.get("retest_quality") == "FAILED"
    rows = [
        ("Pattern", bool(pattern), False, (pattern.get("candle_indexes") or [None])[-1],
         pattern.get("pattern")),
        ("Breakout", bool(breakout), breakout.get("quality") == "FALSE_BREAKOUT",
         breakout.get("breakout_candle"), breakout.get("quality")),
        ("Retest", bool(retest), failed_retest, retest.get("retest_timestamp"),
         retest.get("retest_type")),
        ("Confirmation", bool(retest.get("confirmation_timestamp")), failed_retest,
         retest.get("confirmation_timestamp"), retest.get("retest_quality")),
        ("Entry", bool(entry), False, entry.get("signal_timestamp"), entry.get("entry_mode")),
    ]
    output = []
    blocked = False
    for stage, complete, failed, timestamp, detail in rows:
        status = "FAILED" if failed else "COMPLETE" if complete else "BLOCKED" if blocked else "PENDING"
        blocked = blocked or failed
        output.append({"Stage": stage, "Status": status, "Time": display_date(timestamp),
                       "Detail": str(detail or "Waiting")})
    return output


def setup_score_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    score = result.get("score") or {}
    weights = score.get("weights") or {}
    return [{"Component": name.replace("_", " ").title(), "Score": round(float(value or 0), 1),
             "Weight": round(float(weights.get(name, 0)) * 100, 1)}
            for name, value in (score.get("component_scores") or {}).items()]


def entry_alternative_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [{
        "Mode": str(entry.get("entry_mode", "")).replace("_", " ").title(),
        "Entry": entry.get("entry_price"), "Stop": entry.get("stop_loss"),
        "Target 1": (entry.get("targets") or [None])[0],
        "Best R:R": max(entry.get("risk_reward") or [0]),
        "Confidence": entry.get("confidence"), "Expires": entry.get("expiry_maximum_waiting_bars"),
    } for entry in sorted(result.get("entries") or [],
                          key=lambda item: float(item.get("confidence") or 0), reverse=True)]


def grouped_setup_rejections(result: dict[str, Any]) -> list[dict[str, Any]]:
    groups = {
        "Setup invalid": ("INVALID", "FALSE", "PATTERN", "STRUCTURE"),
        "Entry not ready": ("WAIT", "CONFIRM", "RETEST", "EXPIR"),
        "Risk/reward": ("RISK", "REWARD", "STOP", "TARGET"),
        "Opposing zone": ("ZONE", "RESISTANCE", "SUPPORT"),
        "Volume/volatility": ("VOLUME", "VOLAT", "ATR", "EXTEND"),
        "Option safety": ("STRIKE", "OPTION", "PREMIUM"),
    }
    reasons = list(result.get("rejection_reasons") or [])
    for event in result.get("breakout_events") or []:
        reasons.extend(event.get("reason_codes") or [])
    rows = []
    for reason in dict.fromkeys(str(item) for item in reasons if item):
        category = next((name for name, terms in groups.items()
                         if any(term in reason.upper() for term in terms)), "Other")
        rows.append({"Category": category, "Reason code": reason,
                     "Explanation": reason.replace("_", " ").title()})
    return sorted(rows, key=lambda item: (item["Category"], item["Reason code"]))


def option_safety_view(result: dict[str, Any]) -> dict[str, Any]:
    option = (result.get("option_selling") or result.get("technical_validation")
              or ((result.get("entry_validation") or {}).get("technical_validation")) or {})
    return {
        "available": bool(option),
        "spot": option.get("spot_price"), "strike": option.get("suggested_strike"),
        "invalidation": option.get("technical_invalidation"),
        "breakeven": option.get("breakeven"),
        "support": option.get("nearest_support"), "resistance": option.get("nearest_resistance"),
        "support_strength": option.get("support_strength"),
        "resistance_strength": option.get("resistance_strength"),
        "breakout": option.get("breakout_status", "NONE"),
        "retest": option.get("retest_status", "NONE"),
        "strike_safety": option.get("technical_strike_safety"),
        "invalidation_distance": option.get("distance_strike_to_invalidation"),
        "warnings": option.get("warnings") or [],
    }


def portfolio_snapshot(trades: list[dict[str, Any]], prices: dict[str, float | None]) -> dict[str, Any]:
    """Calculate live portfolio totals without hiding positions with missing quotes."""
    deployed = risk = unrealized = 0.0
    priced = overdue = 0
    for trade in trades:
        if trade.get("status") != "OPEN":
            continue
        entry, quantity = float(trade["entry_price"]), int(trade["quantity"])
        deployed += entry * quantity
        if trade.get("stop_loss") is not None:
            risk += abs(entry - float(trade["stop_loss"])) * quantity
        current = prices.get(str(trade.get("symbol")))
        if current is not None:
            direction = 1 if trade.get("side") == "BUY" else -1
            unrealized += (float(current) - entry) * quantity * direction - float(trade.get("fees") or 0)
            priced += 1
        overdue += int(bool(trade.get("hold_until") and str(trade["hold_until"]) <= date.today().isoformat()))
    return {"capital_deployed": round(deployed, 2), "risk_at_stops": round(risk, 2),
            "unrealized_pnl": round(unrealized, 2), "priced_positions": priced,
            "open_positions": sum(item.get("status") == "OPEN" for item in trades),
            "overdue_reviews": overdue}


def render_candidate_cards(platform: TradingPlatform, report: dict[str, Any],
                           database: ReportDatabase | None = None,
                           key_prefix: str = "candidates") -> None:
    candidates = [*report.get("trades", []), *report.get("watchlist", [])]
    if not candidates:
        st.info("No actionable or watchlist candidates were produced. Review rejection reasons below.")
        return
    run_id = str(report.get("run_id", ""))
    for start in range(0, len(candidates), 2):
        columns = st.columns(2)
        for column, trade in zip(columns, candidates[start:start + 2]):
            with column.container(border=True):
                levels = trade.get("levels") or {}
                adverse = trade.get("adverse_move_risk") or {}
                adverse_view = adverse_risk_view(adverse)
                status = trade.get("status", "WATCHLIST")
                symbol = str(trade.get("symbol", "UNKNOWN"))
                option_signal = option_confirmation(trade)
                st.markdown(
                    f'<div class="candidate-title">{symbol}</div>'
                    f'<div class="badge-row">{badge(status)}{badge(trade.get("final_action"))}'
                    f'{badge(trade.get("quality_grade"))}{badge("OPTIONS " + option_signal["label"])}</div>',
                    unsafe_allow_html=True,
                )
                st.markdown(f'<div class="candidate-reason">{trade.get("selection_reason") or "No summary supplied."}</div>',
                            unsafe_allow_html=True)
                st.markdown(
                    '<div class="candidate-metrics">'
                    f'<div class="candidate-metric"><small>Entry</small><strong>{money(levels.get("entry"))}</strong></div>'
                    f'<div class="candidate-metric"><small>Stop</small><strong>{money(levels.get("stop_loss"))}</strong></div>'
                    f'<div class="candidate-metric"><small>Risk / reward</small><strong>{number(levels.get("risk_reward"))}</strong></div>'
                    '</div>'
                    f'<div class="candidate-targets">Targets: {money(levels.get("target_1"))} · '
                    f'{money(levels.get("target_2"))}<br>Readiness: '
                    f'{number(trade.get("execution_readiness_score"), 0)}<br>'
                    f'{adverse_view["label"]}: '
                    f'{_compact_percent(adverse_view["probability"])}</div>',
                    unsafe_allow_html=True,
                )
                if st.button("Open trade details",
                             key=f"card-{key_prefix}-{run_id}-{symbol}", width="stretch"):
                    st.session_state[f"candidate_focus_{key_prefix}"] = symbol
    focus_key = f"candidate_focus_{key_prefix}"
    focused = st.session_state.get(focus_key)
    focused_trade = next((item for item in candidates if item.get("symbol") == focused), None)
    if focused_trade:
        with st.container(border=True):
            left, right = st.columns([5, 1])
            left.subheader(f"{focused} trade workspace")
            if right.button("Close", key=f"close-candidate-focus-{key_prefix}", width="stretch"):
                st.session_state.pop(focus_key, None)
                st.rerun()
            render_trade_detail(platform, report, focused_trade, database, key_prefix)


def render_candidate_comparison(report: dict[str, Any]) -> None:
    candidates = [*report.get("trades", []), *report.get("watchlist", [])]
    if len(candidates) < 2:
        return
    by_symbol = {str(item.get("symbol")): item for item in candidates}
    selected = st.multiselect("Compare 2–4 candidates", list(by_symbol),
                              max_selections=4, key="candidate-comparison")
    if len(selected) < 2:
        st.caption("Choose at least two symbols to open a side-by-side decision matrix.")
        return
    rows = []
    fields = (
        ("Status", lambda item: item.get("status")),
        ("Action", lambda item: item.get("final_action")),
        ("Quality", lambda item: item.get("quality_score")),
        ("Readiness", lambda item: item.get("execution_readiness_score")),
        ("Risk / reward", lambda item: (item.get("levels") or {}).get("risk_reward")),
        ("Entry", lambda item: (item.get("levels") or {}).get("entry")),
        ("Stop loss", lambda item: (item.get("levels") or {}).get("stop_loss")),
        ("Relative strength", lambda item: (item.get("relative_strength") or {}).get("score")),
        ("Event risk", lambda item: (item.get("event_risk") or {}).get("event_risk_score")),
        ("Adverse move before target (%)", lambda item: adverse_before_target_probability(
            item.get("adverse_move_risk"))),
        ("Option approval", lambda item: (item.get("option_trade_approval") or {}).get("status")),
    )
    for metric, getter in fields:
        row = {"Metric": metric}
        row.update({symbol: getter(by_symbol[symbol]) for symbol in selected})
        rows.append(row)
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def render_trade_detail(platform: TradingPlatform, report: dict[str, Any], trade: dict[str, Any],
                        database: ReportDatabase | None = None,
                        key_prefix: str = "detail") -> None:
    levels = trade.get("levels") or {}
    adverse = trade.get("adverse_move_risk") or {}
    adverse_view = adverse_risk_view(adverse)
    run_id, symbol = str(report.get("run_id", "")), str(trade.get("symbol", ""))
    selection = trade.get("entry_selection") or {}
    selection_status = trade.get("selection_status") or selection.get("status")
    if selection_status:
        message = f"{selection_status}: {trade.get('selection_reason') or selection.get('reason', '')}"
        (st.success if selection_status == "BUY NOW" else st.info if "WAIT" in selection_status
         else st.warning)(message)
    render_metric_cards([
        ("Price", money(trade.get("current_price"))), ("Support", money(levels.get("support"))),
        ("Resistance", money(levels.get("resistance"))),
        ("Risk / reward", number(levels.get("risk_reward"))),
        ("Quality", number(trade.get("quality_score"), 0)),
        (adverse_view["label"], _compact_percent(adverse_view["probability"])),
    ])
    render_price_chart(platform, symbol, levels)
    option = trade.get("option_strategy") or {}
    plan = option.get("trade") or {}
    detail_tabs = st.tabs(["Trade plan", "Decision trail", "Evidence", "Options", "Ask AI", "Record trade"])
    with detail_tabs[0]:
        st.dataframe(pd.DataFrame([{"Entry": levels.get("entry"), "Stop loss": levels.get("stop_loss"),
            "Target 1": levels.get("target_1"), "Target 2": levels.get("target_2"),
            "Target 3": levels.get("target_3")}]), width="stretch", hide_index=True)
        render_adverse_risk_panel(adverse)
        st.markdown("**Execution checklist**")
        for check in decision_checks(trade):
            icon = "✓" if check["passed"] else "✕"
            st.write(f"{icon} {check['label']}")
    with detail_tabs[1]:
        render_decision_timeline(trade)
    with detail_tabs[2]:
        evidence = trade.get("evidence") or trade.get("evidence_summary") or {}
        if evidence:
            st.json(evidence, expanded=False)
        else:
            notes = trade.get("reasons") or [trade.get("selection_reason")]
            for note in filter(None, notes):
                st.write(f"• {note}")
    with detail_tabs[3]:
        option_signal = option_confirmation(trade)
        render_metric_cards([
            ("Option confirmation", option_signal["label"]),
            ("Confirmation score", number(option_signal["score"], 0)),
            ("Average spread", (number(option_signal["spread_percent"], 1) + "%")
             if option_signal["spread_percent"] is not None else "—"),
        ])
        st.caption(option_signal["reason"])
        st.caption(f"{plan.get('strategy', option.get('strategy', 'No approved strategy'))} · "
                   f"Expiry {plan.get('expiry', option.get('expiry', 'unavailable'))}")
        legs = option_leg_rows(trade)
        if legs and trade.get("option_trade_approval", {}).get("status") == "APPROVED":
            st.dataframe(pd.DataFrame(legs), width="stretch", hide_index=True)
        else:
            st.info((option.get("rejection") or {}).get("reason", "No executable option structure approved."))
    with detail_tabs[4]:
        if database:
            render_ai_chat(database, symbol=symbol, report=report,
                           scope=f"candidate-{run_id}-{symbol}", compact=True)
        else:
            st.info("AI context requires the local report database.")
    with detail_tabs[5]:
        if database:
            start_recommended_trade_control(platform, database, run_id, trade, key_prefix)
        else:
            st.info("Trade recording requires the local report database.")


def price_figure(frame: pd.DataFrame, symbol: str, levels: dict[str, Any] | None = None,
                 moving_averages: tuple[int, ...] = (20, 50), show_volume: bool = True,
                 show_rsi: bool = True) -> go.Figure:
    """Build a compact, decision-oriented OHLCV chart without mutating source data."""
    data = frame.copy()
    data.columns = [str(column).title() for column in data.columns]
    x = data.index
    visibility = (levels or {}).get("overlay_visibility") or {}
    enabled = lambda name: visibility.get(name, True)
    figure = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=.025,
                           row_heights=[.64, .18, .18])
    figure.add_trace(go.Candlestick(x=x, open=data["Open"], high=data["High"], low=data["Low"],
                                    close=data["Close"], name=symbol), row=1, col=1)
    colors_by_window = {20: "#60a5fa", 50: "#f59e0b", 200: "#f472b6"}
    for window in moving_averages:
        color = colors_by_window.get(window, "#a1a1aa")
        if len(data) >= window:
            figure.add_trace(go.Scatter(x=x, y=data["Close"].rolling(window).mean(),
                                        name=f"{window} DMA", line={"width": 1.4, "color": color}), row=1, col=1)
    for label, key, color in (("Entry", "entry", "#60a5fa"), ("Stop", "stop_loss", "#fb7185"),
                              ("Target", "target_1", "#34d399"), ("Support", "support", "#a78bfa"),
                              ("Resistance", "resistance", "#fbbf24")):
        price = (levels or {}).get(key)
        if key in ("entry", "stop_loss", "target_1") and not enabled("trade_levels"):
            continue
        if key in ("support", "resistance") and not enabled("zones"):
            continue
        if price is not None:
            figure.add_hline(y=float(price), line_dash="dot", line_color=color,
                             annotation_text=label, row=1, col=1)
    if (levels or {}).get("show_price_action_overlays", True):
        for zone in ((levels or {}).get("zones", []) if enabled("zones") else []):
            color = "#34d399" if zone.get("type") == "SUPPORT" else "#fb7185"
            figure.add_hrect(y0=float(zone["lower"]), y1=float(zone["upper"]),
                             fillcolor=color, opacity=.10, line_width=0, row=1, col=1)
        swings = (levels or {}).get("swings", [])[-12:] if enabled("swings") else []
        if swings:
            figure.add_trace(go.Scatter(
                x=[s["timestamp"] for s in swings], y=[s["price"] for s in swings],
                mode="markers+text", text=[s.get("label", s.get("type", "")) for s in swings],
                textposition="top center", marker={"size": 8, "color": "#fbbf24"},
                name="Confirmed swings"), row=1, col=1)
        event_overlays = (
                ("Breakout", (levels or {}).get("breakout"), "#22d3ee"),
                ("Retest", (levels or {}).get("retest"), "#a78bfa"),
                ("Confirmation", (levels or {}).get("confirmation"), "#34d399"))
        for name, event, color in event_overlays:
            if not enabled(name.lower()):
                continue
            if event and event.get("timestamp") is not None and event.get("price") is not None:
                figure.add_trace(go.Scatter(
                    x=[event["timestamp"]], y=[event["price"]], mode="markers+text",
                    text=[name], textposition="bottom center",
                    marker={"size": 11, "color": color, "symbol": "diamond"}, name=name), row=1, col=1)
        pattern = (levels or {}).get("pattern") if enabled("patterns") else None
        if pattern:
            figure.add_trace(go.Scatter(
                x=[pattern.get("timestamp")], y=[pattern.get("price")],
                mode="markers+text", text=[pattern.get("name", "Pattern")],
                textposition="top center", marker={"size": 10, "color": "#f472b6", "symbol": "star"},
                name="Pattern"), row=1, col=1)
        for index, target in enumerate(
                (levels or {}).get("price_action_targets", []) if enabled("trade_levels") else [], 1):
            figure.add_hline(y=float(target), line_dash="dash", line_color="#34d399",
                             opacity=.55, annotation_text=f"PA target {index}", row=1, col=1)
    if show_volume and "Volume" in data:
        colors = ["#34d399" if close >= opened else "#fb7185"
                  for close, opened in zip(data["Close"], data["Open"])]
        figure.add_trace(go.Bar(x=x, y=data["Volume"], marker_color=colors, name="Volume"), row=2, col=1)
    delta = data["Close"].diff()
    gain, loss = delta.clip(lower=0).rolling(14).mean(), -delta.clip(upper=0).rolling(14).mean()
    rsi = 100 - (100 / (1 + gain / loss.replace(0, float("nan"))))
    if show_rsi:
        figure.add_trace(go.Scatter(x=x, y=rsi, name="RSI 14", line={"color": "#a78bfa"}), row=3, col=1)
        figure.add_hline(y=70, line_dash="dot", line_color="#fb7185", row=3, col=1)
        figure.add_hline(y=30, line_dash="dot", line_color="#34d399", row=3, col=1)
    figure.update_layout(height=650, margin={"l": 10, "r": 10, "t": 35, "b": 10},
                         template="plotly_dark", hovermode="x unified", legend_orientation="h",
                         xaxis_rangeslider_visible=False)
    return figure


def render_price_chart(platform: TradingPlatform, symbol: str,
                       levels: dict[str, Any] | None = None) -> None:
    try:
        frame = platform.provider.get_data(symbol)
        if frame is None or frame.empty or not {"Open", "High", "Low", "Close"}.issubset(frame.columns):
            st.info("Price history is unavailable for this chart.")
            return
        controls = st.columns([1, 2, 1, 1, 1, 1])
        period = controls[0].selectbox("Chart period", ("3M", "6M", "1Y", "ALL"), index=1,
                                       key=f"chart-period-{symbol}")
        averages = tuple(controls[1].multiselect("Moving averages", (20, 50, 200), default=(20, 50),
                                                 key=f"chart-ma-{symbol}"))
        show_volume = controls[2].toggle("Volume", value=True, key=f"chart-volume-{symbol}")
        show_rsi = controls[3].toggle("RSI", value=True, key=f"chart-rsi-{symbol}")
        compare_index = controls[4].toggle("vs Nifty", value=False, key=f"chart-index-{symbol}")
        show_price_action = controls[5].toggle("Price action", value=True,
                                               key=f"chart-price-action-{symbol}")
        overlay_visibility = {}
        if show_price_action:
            with st.expander("Chart overlays", expanded=False):
                overlay_columns = st.columns(4)
                overlay_options = (
                    ("zones", "Zones"), ("swings", "Structure"), ("patterns", "Patterns"),
                    ("breakout", "Breakouts"), ("retest", "Retests"),
                    ("confirmation", "Confirmations"), ("trade_levels", "Entry / stop / targets"),
                )
                for index, (name, label) in enumerate(overlay_options):
                    overlay_visibility[name] = overlay_columns[index % 4].toggle(
                        label, value=True, key=f"chart-overlay-{name}-{symbol}")
        length = {"3M": 65, "6M": 130, "1Y": 260, "ALL": len(frame)}[period]
        visible = frame.tail(length)
        chart_levels = dict(levels or {})
        chart_levels["show_price_action_overlays"] = show_price_action
        chart_levels["overlay_visibility"] = overlay_visibility
        st.plotly_chart(price_figure(visible, symbol, chart_levels, averages, show_volume, show_rsi), width="stretch",
                        config={"displaylogo": False, "scrollZoom": True,
                                "toImageButtonOptions": {"filename": f"{symbol}-chart", "scale": 2}})
        if compare_index:
            benchmark = platform.provider.get_data("NIFTY 50")
            if benchmark is not None and not benchmark.empty:
                comparison = pd.concat([visible["Close"].rename(symbol),
                                        benchmark["Close"].rename("NIFTY 50")], axis=1).dropna()
                normalized = comparison / comparison.iloc[0] * 100
                relative = go.Figure()
                relative.add_trace(go.Scatter(x=normalized.index, y=normalized[symbol], name=symbol))
                relative.add_trace(go.Scatter(x=normalized.index, y=normalized["NIFTY 50"], name="NIFTY 50"))
                relative.update_layout(title="Relative performance (start = 100)", template="plotly_dark",
                                       height=330, margin={"l": 10, "r": 10, "t": 50, "b": 10})
                st.plotly_chart(relative, width="stretch", config={"displaylogo": False})
            else:
                st.info("Nifty history is unavailable for this comparison.")
        last = visible.index[-1]
        st.caption(f"Last candle: {last} · Levels are analytical references, not broker orders.")
        st.download_button("Export visible OHLCV", visible.to_csv().encode(),
                           file_name=f"{symbol}-{period.lower()}-ohlcv.csv", mime="text/csv",
                           key=f"download-chart-{symbol}")
    except Exception as exc:
        st.info(f"Chart unavailable: {exc}")


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


def start_recommended_trade_control(platform: TradingPlatform, database: ReportDatabase,
                                    run_id: str, trade: dict[str, Any],
                                    key_prefix: str = "recommendation",
                                    save_report_mark: bool = True) -> None:
    """Record the user's actual action while preserving the analytical recommendation."""
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
    live_entry = _polled_equity_price(platform, symbol)
    suggested_entry = float(live_entry or levels.get("entry") or trade.get("current_price") or 0)
    analytical_status = str(trade.get("status") or "UNKNOWN").upper()
    analytical_action = str(trade.get("final_action") or trade.get("action") or "UNKNOWN").upper()
    override_required = trade_override_required(trade)
    suggested_hold = date.today() + timedelta(days=10)
    widget_scope = f"{key_prefix}-{run_id}-{symbol}"
    with st.form(f"start-trade-{widget_scope}"):
        st.markdown("**I traded this stock**")
        st.caption("This records what you actually traded. It does not change the report's original analysis.")
        if override_required:
            st.warning(f"Analytical decision: {analytical_status} · {analytical_action}. "
                       "You are recording a discretionary override.")
        columns = st.columns(4)
        quantity = columns[0].number_input("Quantity", min_value=1, value=1, step=1,
                                           key=f"qty-{widget_scope}")
        default_side = 1 if analytical_action in {"SELL", "SHORT", "BEARISH"} else 0
        side = columns[1].selectbox("Actual side", ("BUY", "SELL"), index=default_side,
                                    key=f"side-{widget_scope}")
        entry_date = columns[2].date_input("Trade date", value=date.today(),
                                           key=f"entry-date-{widget_scope}")
        hold_until = columns[3].date_input("Hold until (review date)", value=suggested_hold,
                                           min_value=entry_date,
                                           key=f"hold-{widget_scope}")
        entry_price = st.number_input(
            "Actual executed entry price", min_value=0.01,
            value=max(0.01, suggested_entry), step=0.05, key=f"actual-entry-{widget_scope}",
            help="Enter your broker execution price. A live/reference price is prefilled when available.")
        if live_entry is not None:
            st.caption(f"Current reference price: ₹{live_entry:,.2f}. Confirm your actual fill above.")
        override_confirmed = (st.checkbox(
            "I understand this overrides a non-approved or not-yet-confirmed analytical decision",
            key=f"override-{widget_scope}") if override_required else True)
        confirmed = st.checkbox("I confirm the quantity, side, entry price, and review date",
                                key=f"confirm-start-{widget_scope}")
        submitted = st.form_submit_button(
            "Mark TRADED & start tracking", type="primary"
        )
    if submitted:
        if not override_confirmed or not confirmed:
            st.error("Confirm the trade details and any discretionary override before saving.")
            return
        try:
            database.add_actual_trade({
                "symbol": symbol, "instrument_type": "EQUITY", "side": side,
                "quantity": int(quantity), "entry_date": entry_date.isoformat(),
                "entry_price": entry_price, "stop_loss": levels.get("stop_loss"),
                "target_price": levels.get("target_1") or levels.get("target"),
                "strategy": trade.get("strategy") or analytical_action,
                "recommendation_run_id": run_id, "hold_until": hold_until.isoformat(),
                "notes": (f"Created from report candidate; original status={analytical_status}; "
                          f"original action={analytical_action}; discretionary_override={override_required}"),
            })
            if save_report_mark:
                database.set_candidate_execution(run_id, symbol, True)
            st.success(f"{symbol} is now an active tracked trade.")
            st.rerun()
        except ValueError as exc:
            st.error(f"Could not start trade tracking: {exc}")


def selected_stock_details(platform: TradingPlatform, report: dict[str, Any],
                           database: ReportDatabase | None = None) -> None:
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
            selection = trade.get("entry_selection") or {}
            selection_status = trade.get("selection_status") or selection.get("status")
            if selection_status:
                message = f"{selection_status}: {trade.get('selection_reason') or selection.get('reason', '')}"
                if selection_status == "BUY NOW":
                    st.success(message)
                elif selection_status in {"WAIT FOR BREAKOUT", "WAIT FOR PULLBACK"}:
                    st.info(message)
                else:
                    st.warning(message)
            if database:
                start_recommended_trade_control(platform, database, run_id, trade, "selected")
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
            render_adverse_risk_panel(trade.get("adverse_move_risk"))
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
                "Age": data_age(headline.get("published")),
                "Impact": impact,
                "Analysis": news.get("analysis_method", "UNAVAILABLE"),
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
    candidates = [*report.get("trades", []), *report.get("watchlist", []),
                  *report.get("rejected", [])]
    news_payloads = [item.get("news") or {} for item in candidates]
    stale_count = sum(int(item.get("stale_article_count") or 0) for item in news_payloads)
    analyzed_count = len(rows)
    provider_names = sorted({str(item.get("analysis_method")) for item in news_payloads
                             if item.get("analysis_method")})
    freshness = max((int(item.get("max_age_hours") or 0) for item in news_payloads), default=0)
    status = st.columns(4)
    status[0].metric("Fresh articles", analyzed_count)
    status[1].metric("Old/undated excluded", stale_count)
    status[2].metric("Freshness window", f"{freshness}h" if freshness else "Unknown")
    status[3].metric("Analysis", ", ".join(provider_names) or "Unavailable")
    counts = pd.DataFrame(rows).groupby(["Stock", "Impact"]).size().unstack(fill_value=0)
    st.dataframe(counts.reset_index(), width="stretch", hide_index=True)
    st.dataframe(
        pd.DataFrame(rows), width="stretch", hide_index=True,
        column_config={
            "Sentiment score": st.column_config.NumberColumn(format="%.1f", help="-100 to +100"),
            "News": st.column_config.TextColumn(width="large"),
            "Read": st.column_config.LinkColumn(display_text="Open article"),
            "Published": st.column_config.DatetimeColumn(format="DD MMM YYYY, h:mm a"),
            "Likely stock reaction": st.column_config.TextColumn(width="large"),
        },
    )
    with st.expander("News collection diagnostics"):
        st.write(f"Articles displayed: {analyzed_count}")
        st.write(f"Old, undated, or invalid articles excluded: {stale_count}")
        st.write(f"Analysis provider: {', '.join(provider_names) or 'Unavailable'}")
        st.caption("News is collected only for shortlisted candidates; the analysis provider does "
                   "not itself guarantee that a headline is current.")


def show_report(platform: TradingPlatform, report: dict[str, Any],
                database: ReportDatabase | None = None) -> None:
    summary_cards(report)
    st.caption(f"Report {report.get('date', 'latest')} · Run {report.get('run_id', 'unavailable')} · "
               "Actionable decisions are green; watch conditions are amber.")
    tabs = st.tabs(["Overview", "Candidates", "Ask report", "Market", "News", "Rejections", "Diagnostics"])
    with tabs[0]:
        all_candidates = [*report.get("trades", []), *report.get("watchlist", [])]
        top_report = {**report, "trades": report.get("trades", [])[:3],
                      "watchlist": report.get("watchlist", [])[:max(0, 3 - len(report.get("trades", [])))]}
        st.subheader("Top decisions")
        render_candidate_cards(platform, top_report, database, key_prefix="report-overview")
        if len(all_candidates) > 3:
            st.caption(f"Showing the top 3 of {len(all_candidates)} candidates. Use Opportunities "
                       "for the complete decision workspace.")
        if all_candidates and st.button(
                "Open all opportunities", key=f"report-opportunities-{report.get('run_id')}"):
            request_navigation("Opportunities")
        grouped = rejection_summary(report.get("rejected", []))
        if grouped:
            st.subheader("Important blockers")
            st.dataframe(pd.DataFrame(grouped[:4]), width="stretch", hide_index=True,
                         column_config={"Symbols": st.column_config.TextColumn(width="large")})
        if database:
            history = database.list_reports(100)
            position = next((index for index, item in enumerate(history)
                             if item.get("run_id") == report.get("run_id")), None)
            previous = (database.get_report(history[position + 1]["id"])
                        if position is not None and position + 1 < len(history) else None)
            if previous:
                st.subheader("Changes since previous report")
                changes = report_changes(report, previous)
                if changes:
                    st.dataframe(pd.DataFrame(changes), width="stretch", hide_index=True,
                                 column_config={"Score change": st.column_config.NumberColumn(format="%+.1f")})
                else:
                    st.success("No candidate status or score changes since the previous report.")
    with tabs[1]:
        marks = (database.get_candidate_executions(str(report.get("run_id")))
                 if database and report.get("run_id") else {})
        rows = candidate_rows(report, marks)
        if rows:
            statuses = sorted({str(row["Status"]) for row in rows})
            selected_statuses = st.multiselect("Filter status", statuses, default=statuses)
            query = st.text_input("Search symbol", placeholder="e.g. RELIANCE", key="candidate-search")
            filtered = [row for row in rows if row["Status"] in selected_statuses and
                        query.upper() in str(row["Symbol"]).upper()]
            st.dataframe(pd.DataFrame(filtered), width="stretch", hide_index=True,
                         column_config=candidate_table_config(),
                         column_order=("Symbol", "Status", "Action", "Quality", "Quality score",
                                       "Readiness", "R:R", "Trigger price", "Support", "Resistance",
                                       "Adverse barrier", "Adverse move before target",
                                       "Target before adverse barrier",
                                       "Option approval", "Event risk", "Selection reason"))
            st.download_button("Export candidate CSV", pd.DataFrame(filtered).to_csv(index=False).encode(),
                               file_name=f"candidates-{report.get('date', 'latest')}.csv",
                               mime="text/csv", key=f"candidate-csv-{report.get('run_id')}")
        else:
            st.info("No executable or watchlist candidates were produced.")
        if rows:
            with st.expander("Compare candidates"):
                render_candidate_comparison(report)
            st.subheader("Expanded candidate details")
            selected_stock_details(platform, report, database)
    with tabs[2]:
        st.subheader("Ask why a stock was selected")
        st.caption("Answers use only the scores, filters, risks, and decisions saved in this report. "
                   "Try: “Why did you suggest IDEA?”")
        chat_key = f"report_explainer_{report.get('run_id', 'latest')}"
        messages = st.session_state.setdefault(chat_key, [])
        if not messages:
            messages.append({"role": "assistant", "content":
                             "Ask me about any trade, watchlist, or rejected symbol in this report."})
        for message in messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])
        if prompt := st.chat_input("Why did you suggest IDEA?", key=f"prompt_{chat_key}"):
            messages.append({"role": "user", "content": prompt})
            messages.append({"role": "assistant",
                             "content": explain_stock_question(report, prompt)})
            st.rerun()
    with tabs[3]:
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
    with tabs[4]:
        show_news(report)
    with tabs[5]:
        rejected = report.get("rejected", [])
        if rejected:
            grouped = rejection_summary(rejected)
            st.subheader("Rejections by primary blocker")
            st.dataframe(pd.DataFrame(grouped), width="stretch", hide_index=True,
                         column_config={"Symbols": st.column_config.TextColumn(width="large")})
            run_id = str(report.get("run_id", ""))
            for item in rejected:
                with st.expander(str(item.get("symbol", "UNKNOWN"))):
                    symbol = str(item.get("symbol", "UNKNOWN"))
                    st.warning(f"{item.get('selection_status', 'AVOID')}: "
                               f"{item.get('selection_reason', 'Final selection gates did not pass.')}")
                    st.error("Analytical status: REJECTED. Marking this as TRADED records your "
                             "actual action and does not convert it into an approved recommendation.")
                    if database:
                        start_recommended_trade_control(platform, database, run_id, item, "rejected")
                    for reason in item.get("reasons", []):
                        st.write(f"• {reason}")
        else:
            st.success("No candidates were rejected.")
    with tabs[6]:
        st.caption("Advanced operational and raw-data views.")
        diagnostic_tabs = st.tabs(["Health", "Complete text", "Raw JSON"])
        with diagnostic_tabs[0]:
            st.subheader("Context availability")
            st.json(report.get("context_statistics", {}), expanded=False)
            st.subheader("Dependency health")
            st.json(report.get("dependency_health", {}), expanded=False)
        with diagnostic_tabs[1]:
            st.code(DailyReportPresenter.render(report), language="text")
        with diagnostic_tabs[2]:
            st.download_button("Download report JSON", json.dumps(report, indent=2, default=str),
                               file_name=f"daily-report-{report.get('date', 'latest')}.json",
                               mime="application/json")
            st.json(report, expanded=False)


def dashboard(platform: TradingPlatform, database: ReportDatabase) -> None:
    india_now = datetime.now(ZoneInfo("Asia/Kolkata"))
    market_open = india_now.weekday() < 5 and (9, 15) <= (india_now.hour, india_now.minute) <= (15, 30)
    history = database.list_reports(10)
    latest = database.get_report(history[0]["id"]) if history else None
    regime = (latest or {}).get("market", {}).get("regime", "UNAVAILABLE")
    hero("Today's trading desk", "Decision summary and fresh market context in one place.",
         [f"Market {'open' if market_open else 'closed'}", regime,
          platform.settings.market_data_source])
    render_health_strip(platform, database, latest)
    counts = database.counts()
    trade_summary = database.actual_trade_summary()
    render_metric_cards([
        ("Open positions", trade_summary["open"]),
        ("Realized P&L", money(trade_summary["realized_pnl"])),
        ("Latest trades", (latest or {}).get("summary", {}).get("trades_generated", 0)),
        ("Watchlist", (latest or {}).get("summary", {}).get("watchlisted", 0)),
        ("Reports", counts["reports"]),
    ])
    if latest:
        if len(history) > 1:
            previous = database.get_report(history[1]["id"])
            changes = report_changes(latest, previous)
            if changes:
                with st.expander(f"What changed since the previous report ({len(changes)})", expanded=True):
                    st.dataframe(pd.DataFrame(changes[:10]), width="stretch", hide_index=True,
                                 column_config={"Score change": st.column_config.NumberColumn(format="%+.1f")})
        st.subheader("Latest opportunities")
        render_candidate_cards(platform, latest, database)
    else:
        st.info("Generate the first daily report to populate today's opportunities.")
    if trade_summary["open"]:
        st.info(f"{trade_summary['open']} live position(s) are being tracked in the dedicated "
                "Positions workspace.")
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
    with st.expander("Recent report archive"):
        if history:
            st.dataframe(pd.DataFrame(history), width="stretch", hide_index=True,
                         column_config={"generated_at": st.column_config.DatetimeColumn(
                             "Generated", format="DD MMM YYYY, h:mm a")})


def opportunities_page(platform: TradingPlatform, database: ReportDatabase) -> None:
    """Decision-oriented view of the newest immutable report."""
    history = database.list_reports(2)
    latest = database.get_report(history[0]["id"]) if history else None
    previous = database.get_report(history[1]["id"]) if len(history) > 1 else None
    hero("Opportunities", "Actionable trades and research candidates grouped by the next decision.")
    render_health_strip(platform, database, latest)
    if not latest:
        st.info("No report is available. Generate one from Daily report first.")
        return
    report_age = data_age(history[0].get("generated_at"))
    preferences = database.get_preferences()
    st.caption(f"Report generated {report_age} · {display_date(latest.get('date'), 'Unknown date')} · "
               f"Run {latest.get('run_id', 'unavailable')}")
    candidates = [
        *[{**item, "_opportunity_source": "TRADE"} for item in latest.get("trades", [])],
        *[{**item, "_opportunity_source": "WATCHLIST"} for item in latest.get("watchlist", [])],
        *[{**item, "_opportunity_source": "REJECTED"} for item in latest.get("rejected", [])],
    ]
    summary = latest.get("summary") or {}
    scanned = int(summary.get("stocks_scanned") or len(candidates))
    qualified = len(latest.get("trades", [])) + len(latest.get("watchlist", []))
    funnel = st.columns(4)
    funnel[0].metric("Scanned", scanned)
    funnel[1].metric("Qualified", qualified,
                     f"{qualified / scanned * 100:.0f}%" if scanned else None)
    funnel[2].metric("Watchlist", len(latest.get("watchlist", [])))
    funnel[3].metric("Buy now", len(latest.get("trades", [])))

    generated_at = history[0].get("generated_at") if history else None
    freshness = freshness_status(generated_at)
    st.markdown(f"Data freshness: {badge(freshness['label'])} {freshness['age']}",
                unsafe_allow_html=True)
    ranked_rows = ranked_opportunity_rows(candidates, generated_at, previous)
    st.subheader("Ranked opportunity board")
    st.caption("One sortable view of every candidate. Option confirmation uses recorded Zerodha "
               "chain evidence and never upgrades an otherwise rejected equity setup.")
    if ranked_rows:
        st.dataframe(pd.DataFrame(ranked_rows), width="stretch", hide_index=True,
                     column_config={
                         "Quality": st.column_config.ProgressColumn(min_value=0, max_value=100,
                                                                    format="%.0f"),
                         "Readiness": st.column_config.ProgressColumn(min_value=0, max_value=100,
                                                                      format="%.0f"),
                         "Option score": st.column_config.ProgressColumn(min_value=0, max_value=100,
                                                                         format="%.0f"),
                         "R:R": st.column_config.NumberColumn(format="%.2f"),
                         "Score Δ": st.column_config.NumberColumn(format="%+.0f"),
                         "Why ranked": st.column_config.TextColumn(width="large"),
                         "Primary blocker": st.column_config.TextColumn(width="medium"),
                     })
    else:
        st.info("No candidates are available for ranking.")

    sectors = sorted({str(item.get("sector")) for item in candidates if item.get("sector")})
    grades = sorted({str(item.get("quality_grade")) for item in candidates if item.get("quality_grade")})
    preset_values = {
        "Custom": (0, 0, False, False),
        "High-quality swing": (2.0, 70, False, False),
        "F&O liquid": (1.5, 60, False, True),
        "HAL-like momentum": (2.0, 70, True, False),
    }
    preset = st.selectbox("Saved filter preset", list(preset_values), key="opportunity-preset",
                          help="Choose a starting screen; you can still refine symbol, sector, and grade.")
    preset_rr, preset_readiness, preset_news, preset_options = preset_values[preset]
    controls = st.columns([2, 1, 1, 1])
    query = controls[0].text_input("Search symbol", placeholder="RELIANCE", key="opportunity-search")
    sector = controls[1].selectbox("Sector", ["All", *sectors], key="opportunity-sector")
    grade = controls[2].selectbox("Grade", ["All", *grades], key="opportunity-grade")
    minimum_rr = controls[3].number_input("Minimum R:R", min_value=0.0, value=float(preset_rr),
                                         step=0.25, key=f"opportunity-min-rr-{preset}")
    advanced = st.columns([1, 1, 1])
    minimum_readiness = advanced[0].number_input(
        "Minimum readiness", min_value=0, max_value=100, value=int(preset_readiness),
        key=f"opportunity-readiness-{preset}",
        help="Readiness estimates how close the setup is to execution; it is not trade approval.")
    complete_only = advanced[1].toggle("Only complete news data", value=preset_news,
                                       key=f"opportunity-complete-{preset}")
    density_options = ("Comfortable", "Compact", "Table only")
    density = advanced[2].selectbox(
        "Display density", density_options,
        index=density_options.index(preferences.get("opportunity_density", "Comfortable")),
        key="opportunity-density")
    option_only = st.toggle("Only candidates with confirmed option evidence", value=preset_options,
                            key=f"opportunity-options-confirmed-{preset}",
                            help="This is a confirmation filter, not a substitute for equity quality gates.")

    def included(item: dict[str, Any]) -> bool:
        rr = float((item.get("levels") or {}).get("risk_reward") or 0)
        readiness = float(item.get("execution_readiness_score") or 0)
        news_state = str((item.get("news") or {}).get("news_state", ""))
        return (query.upper() in str(item.get("symbol", "")).upper()
                and (sector == "All" or str(item.get("sector")) == sector)
                and (grade == "All" or str(item.get("quality_grade")) == grade)
                and rr >= minimum_rr and readiness >= minimum_readiness
                and (not complete_only or news_state in {"ANALYZED", "NO_RELEVANT_NEWS"})
                and (not option_only or option_confirmation(item)["label"] == "Confirmed"))

    filtered = [item for item in candidates if included(item)]
    with st.expander("Candidate history"):
        symbols = sorted({str(item.get("symbol")) for item in candidates})
        if symbols:
            history_symbol = st.selectbox("Symbol history", symbols, key="opportunity-history-symbol")
            saved = database.list_reports(20)
            report_payloads = [database.get_report(item["id"]) for item in reversed(saved)]
            progression = candidate_history_rows([item for item in report_payloads if item], history_symbol)
            if progression:
                frame = pd.DataFrame(progression)
                st.dataframe(frame, width="stretch", hide_index=True)
                chart_values = frame.set_index("Date")[["Quality", "Readiness", "R:R"]].apply(
                    pd.to_numeric, errors="coerce")
                st.line_chart(chart_values)
            else:
                st.info("No saved history is available for this symbol.")
        else:
            st.info("No candidates are available in this report.")
    groups = opportunity_groups(filtered)
    tabs = st.tabs([f"{label} ({len(items)})" for label, items in groups.items()])
    for tab, (label, items) in zip(tabs, groups.items()):
        with tab:
            if not items:
                st.info(f"No {label.lower()} candidates match the current filters.")
                continue
            bucket_report = {**latest, "trades": [], "watchlist": [], "rejected": []}
            target_bucket = "rejected" if label == "No trade / blocked" else (
                "trades" if label == "Buy now" else "watchlist")
            bucket_report[target_bucket] = items
            if target_bucket == "rejected":
                rows = [{"Symbol": item.get("symbol"), "Final action": item.get("final_action"),
                         "Primary blocker": (item.get("rejection_reason")
                                             or item.get("selection_reason")
                                             or "; ".join(item.get("reasons") or [])
                                             or "Policy or evidence gate failed"),
                         "Quality": item.get("quality_grade"),
                         "Readiness": item.get("execution_readiness_score")}
                        for item in items]
                st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True,
                             column_config={"Primary blocker": st.column_config.TextColumn(width="large")})
                by_symbol = {str(item.get("symbol")): item for item in items}
                selected_override = st.selectbox(
                    "Record a discretionary trade", list(by_symbol),
                    key="opportunity-rejected-trade-symbol",
                    help="This records your actual action without changing the rejected analysis.")
                with st.expander(f"Trade override · {selected_override}"):
                    start_recommended_trade_control(
                        platform, database, str(latest.get("run_id", "")),
                        by_symbol[selected_override], "opportunity-rejected")
            elif density == "Comfortable":
                render_candidate_cards(platform, bucket_report, database,
                                       key_prefix="opportunities-" + label.lower().replace(" ", "-"))
            else:
                rows = candidate_rows(bucket_report)
                columns = (("Symbol", "Action", "Quality", "Readiness", "R:R")
                           if density == "Compact" else None)
                st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True,
                             column_config=candidate_table_config(), column_order=columns)
                st.download_button("Export visible CSV", pd.DataFrame(rows).to_csv(index=False).encode(),
                                   file_name=f"opportunities-{label.lower().replace(' ', '-')}.csv",
                                   mime="text/csv", key=f"opportunity-export-{label}")


def positions_page(platform: TradingPlatform, database: ReportDatabase) -> None:
    """Dedicated live-position and journal workspace."""
    trade_tracker_page(platform, database)


def bearish_options_page(platform: TradingPlatform, database: ReportDatabase) -> None:
    st.title("Bearish option opportunities")
    st.caption("Shows up to two validated bearish stocks with Long Put, Bear Put Spread, or hedged Bear Call Spread structures; it never forces a trade.")
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
            risk = candidate.get("adverse_move_risk", {})
            risk_view = adverse_risk_view(risk)
            filters = candidate.get("stock_selection_filters", {})
            st.dataframe(pd.DataFrame([{
                "Bearish stock filters": "PASS" if filters.get("passed") else "FAIL",
                f"Target before +{risk_view['barrier']}": risk.get(
                    "probability_target_before_adverse_barrier"),
                risk_view["label"]: risk_view["probability"],
                "No upward overnight gap": risk.get("probability_no_overnight_gap_beyond_barrier"),
                "Path samples": risk.get("sample_count"),
                "Relative strength vs Nifty": candidate.get("relative_strength", {}).get("relative_strength"),
                "Sector score": candidate.get("sector_context", {}).get("score"),
            }]), width="stretch", hide_index=True)
            render_adverse_risk_panel(risk)
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
            st.divider()
            st.markdown("**Record a bearish stock trade**")
            st.caption("This records a short-equity paper position. SELL profits when the stock "
                       "falls and loses when it rises. It does not place a broker order or record "
                       "the option legs themselves.")
            bearish_candidate = {
                **candidate, "status": "TRADE", "final_action": "SELL",
                "strategy": "BEARISH STOCK / SHORT",
                "levels": {
                    **levels,
                    # For a short, the protective stop belongs above entry and
                    # the profit target belongs below it. Preserve scan levels
                    # when they already have that orientation.
                    "stop_loss": (levels.get("stop_loss") if levels.get("stop_loss") and
                                  float(levels["stop_loss"]) > float(candidate["current_price"])
                                  else levels.get("resistance")),
                    "target_1": (levels.get("target_1") if levels.get("target_1") and
                                 float(levels["target_1"]) < float(candidate["current_price"])
                                 else levels.get("support")),
                },
            }
            scan_id = f"bearish-{st.session_state.get('bearish_job_id', 'current')}"
            start_recommended_trade_control(
                platform, database, scan_id, bearish_candidate,
                key_prefix="bearish-stock", save_report_mark=False)
    if not result.get("candidates"):
        st.json(result.get("rejection_counts", {}), expanded=True)


@st.cache_data(ttl=5, show_spinner=False)
def _polled_equity_price(_platform: TradingPlatform, symbol: str) -> float | None:
    """Rate-limited REST/cache fallback used until WebSocket ticks arrive."""
    try:
        live_provider = getattr(_platform.provider, "provider", None)
        if live_provider is not None:
            price = live_provider.get_ltp(symbol)
            return float(price) if price is not None else None
        history = _platform.provider.get_data(symbol)
        if history is not None and not history.empty:
            return float(history["Close"].iloc[-1])
    except Exception:
        return None
    return None


def _latest_trade_price(platform: TradingPlatform, trade: dict[str, Any],
                        feed: KiteLivePriceFeed | None = None) -> float | None:
    """Best available price for status display; failures never hide the position."""
    if trade.get("instrument_type") != "EQUITY":
        return None
    symbol = str(trade.get("symbol", ""))
    streamed = feed.quote(symbol) if feed else None
    return float(streamed["price"]) if streamed else _polled_equity_price(platform, symbol)


def _trade_status(trade: dict[str, Any], current_price: float | None) -> dict[str, Any]:
    entry = float(trade["entry_price"])
    quantity = int(trade["quantity"])
    direction = 1 if trade["side"] == "BUY" else -1
    pnl = None if current_price is None else round(
        (current_price - entry) * quantity * direction - float(trade.get("fees") or 0), 2
    )
    pnl_percent = None if pnl is None else round(pnl / (entry * quantity) * 100, 2)
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
    stop_distance = None if current_price is None or stop is None else round(
        ((current_price - float(stop)) * direction / current_price) * 100, 2)
    target_distance = None if current_price is None or target is None else round(
        ((float(target) - current_price) * direction / current_price) * 100, 2)
    return {"pnl": pnl, "pnl_percent": pnl_percent, "decision": decision, "reason": reason,
            "stop_distance": stop_distance, "target_distance": target_distance}


@st.fragment(run_every=1)
def live_trade_status_panel(platform: TradingPlatform, database: ReportDatabase) -> None:
    """Auto-refreshing read-only prices and P&L for open positions."""
    open_trades = database.list_actual_trades("OPEN")
    st.subheader("Live Positions")
    if not open_trades:
        live_provider = getattr(platform.provider, "provider", None)
        if live_provider is not None:
            kite_live_price_feed(live_provider).update_symbols([])
        st.info("No active trades. Mark an approved recommendation as TRADED to start tracking it.")
        return
    india_now = datetime.now(ZoneInfo("Asia/Kolkata"))
    market_open = (india_now.weekday() < 5 and
                   (india_now.hour, india_now.minute) >= (9, 15) and
                   (india_now.hour, india_now.minute) <= (15, 30))
    live_provider = getattr(platform.provider, "provider", None)
    feed = None
    if live_provider is not None:
        feed = kite_live_price_feed(live_provider)
        feed.update_symbols([
            trade["symbol"] for trade in open_trades
            if trade.get("instrument_type") == "EQUITY"
        ])
    feed_status = feed.status() if feed else {"connected": False, "error": None}
    connection_label = ("LIVE WEBSOCKET" if feed_status["connected"] else
                        "CONNECTING / POLLING" if feed else "CACHED PRICE")
    header = st.columns([3, 1])
    header[0].caption(
        f"{connection_label} · Updating every second · "
        f"{india_now:%d %b %Y, %I:%M:%S %p} IST · Market {'OPEN' if market_open else 'CLOSED'}"
    )
    header[1].button("Refresh now", key="refresh-live-positions", width="stretch")
    st.caption("Open trades remain live here until completed. Closing a trade freezes its realized P&L.")
    if feed_status.get("error") and not feed_status["connected"]:
        st.warning(f"Live stream unavailable; quote polling fallback is active. {feed_status['error']}")
    live_prices = {str(trade["symbol"]): _latest_trade_price(platform, trade, feed)
                   for trade in open_trades}
    snapshot = portfolio_snapshot(open_trades, live_prices)
    render_metric_cards([
        ("Unrealized P&L", money(snapshot["unrealized_pnl"])),
        ("Capital deployed", money(snapshot["capital_deployed"])),
        ("Risk at stops", money(snapshot["risk_at_stops"])),
        ("Prices available", f"{snapshot['priced_positions']}/{snapshot['open_positions']}"),
        ("Reviews due", snapshot["overdue_reviews"]),
    ])
    if snapshot["overdue_reviews"]:
        st.warning(f"{snapshot['overdue_reviews']} position review date(s) are due or overdue.")
    for trade in open_trades:
        current = live_prices[str(trade["symbol"])]
        status = _trade_status(trade, current)
        pnl_label = "Price unavailable" if status["pnl"] is None else f"₹{status['pnl']:,.2f}"
        pnl_delta = None if status["pnl_percent"] is None else f"{status['pnl_percent']:+.2f}%"
        result = "—" if status["pnl"] is None else ("PROFIT" if status["pnl"] >= 0 else "LOSS")
        with st.container(border=True):
            title, state = st.columns([4, 1])
            title.markdown(f"### {trade['symbol']} · {status['decision']}")
            state.markdown(badge(result), unsafe_allow_html=True)
            # Two rows keep values readable on laptop and mobile widths. ISO dates
            # are formatted to a shorter, unambiguous display value.
            prices = st.columns(4)
            prices[0].metric("Entry", f"₹{trade['entry_price']:,.2f}")
            prices[1].metric("Current", "Unavailable" if current is None else f"₹{current:,.2f}")
            prices[2].metric("Live P&L", pnl_label, delta=pnl_delta)
            prices[3].metric("Quantity", f"{trade['quantity']:,}")
            plan = st.columns(4)
            plan[0].metric("Hold until", display_date(trade.get("hold_until")))
            plan[1].metric("Stop", money(trade.get("stop_loss")))
            plan[2].metric("Target", money(trade.get("target_price")))
            plan[3].metric("Side", trade.get("side", "—"))
            st.write(status["reason"])
            stop_distance = ("N/A" if status["stop_distance"] is None else
                             f"{status['stop_distance']:+.2f}%")
            target_distance = ("N/A" if status["target_distance"] is None else
                               f"{status['target_distance']:+.2f}%")
            st.caption(f"Stop: {value(trade.get('stop_loss'))} ({stop_distance} away) · Target: "
                       f"{value(trade.get('target_price'))} ({target_distance} away) · "
                       f"Quantity: {trade['quantity']} · Side: {trade['side']}")
            streamed_quote = feed.quote(trade["symbol"]) if feed else None
            if streamed_quote:
                tick_time = datetime.fromisoformat(streamed_quote["received_at"]).astimezone(
                    ZoneInfo("Asia/Kolkata"))
                st.caption(f"Last market tick: {tick_time:%I:%M:%S %p} IST")
                tick_age = (india_now - tick_time).total_seconds()
                if market_open and tick_age > 30:
                    st.warning(f"Price may be stale: the last market tick is {int(tick_age)} seconds old.")
            if trade.get("instrument_type") == "OPTION" and current is None:
                st.warning("Live option P&L needs the exact NSE option trading symbol. "
                           "This position remains tracked, but cannot be auto-closed without a live quote.")


def active_trade_status_panel(platform: TradingPlatform, database: ReportDatabase) -> None:
    """Combine auto-refreshing status with stable, non-refreshing exit controls."""
    live_trade_status_panel(platform, database)
    open_trades = database.list_actual_trades("OPEN")
    if not open_trades:
        return
    st.subheader("Exit positions")
    st.caption("Exit controls stay stable while the live cards above refresh every second. "
               "This records a paper exit; it does not send a broker order.")
    live_provider = getattr(platform.provider, "provider", None)
    feed = kite_live_price_feed(live_provider) if live_provider is not None else None
    for trade in open_trades:
        current = _latest_trade_price(platform, trade, feed)
        with st.container(border=True):
            st.markdown(f"**{trade['symbol']} · {trade['side']} {trade['quantity']}**")
            if current is None:
                st.error("A live market price is unavailable, so this position cannot be auto-closed.")
            else:
                projected = ((current - float(trade["entry_price"])) * int(trade["quantity"])
                             * (1 if trade["side"] == "BUY" else -1)
                             - float(trade.get("fees") or 0))
                st.info(f"Latest exit reference: {money(current)} · Projected realized P&L: "
                        f"{money(projected)}")
            confirmed = st.checkbox("Confirm exit at the latest live market price",
                                    key=f"confirm-position-exit-{trade['id']}")
            completed = st.button("Exit position now", type="primary",
                                  disabled=not confirmed or current is None,
                                  key=f"mark-position-completed-{trade['id']}")
            if completed:
                try:
                    # Discard the five-second REST fallback cache and resolve the
                    # freshest quote available at the actual click rerun.
                    _polled_equity_price.clear()
                    execution_price = _latest_trade_price(platform, trade, feed)
                    if execution_price is None:
                        st.error("The live quote became unavailable. The position remains open.")
                        continue
                    pnl = database.close_actual_trade(
                        trade["id"], date.today().isoformat(), execution_price, 0)
                    outcome = "PROFIT" if pnl >= 0 else "LOSS"
                    st.success(f"Exited at {money(execution_price)} · {outcome} · "
                               f"Final P&L: ₹{pnl:,.2f}")
                    st.rerun()
                except ValueError as exc:
                    st.error(f"Could not complete this position: {exc}")


def daily_report_page(platform: TradingPlatform, database: ReportDatabase) -> None:
    hero("Daily report", "Generate and review a point-in-time research report. Live positions are "
         "managed separately in Positions.")
    active_future = daily_report_jobs().future(st.session_state.get("daily_report_job_id"))
    job_running = active_future is not None and not active_future.done()
    preferences = database.get_preferences()
    with st.form("daily-report-form"):
        left, middle, right = st.columns(3)
        limit = left.number_input("Maximum final trades", 1, 50,
                                  int(preferences.get("default_report_limit", 5)))
        minimum_score = middle.number_input("Minimum technical score", 0, 100,
                                            int(preferences.get("default_minimum_score", 40)),
                                            help="Initial technical screening floor; final execution gates still apply.")
        option_month = right.text_input("Option month (optional)", placeholder="YYYY-MM")
        submitted = st.form_submit_button("Run report", type="primary", disabled=job_running)
    if submitted:
        job_id = daily_report_jobs().submit(
            platform, database, int(limit), int(minimum_score), option_month.strip() or None
        )
        st.session_state["daily_report_job_id"] = job_id
        st.session_state["daily_report_synced_job_id"] = None
        st.session_state["daily_report_job_error"] = None
        st.session_state["daily_report_job_started_at"] = datetime.now().timestamp()
        # Do not leave the previous report on screen while a fresh live-market
        # report is running; it makes stale output appear to be the new result.
        st.session_state.pop("current_report", None)
        st.success("Daily report started in the background. You can open History or any other page.")
        st.rerun()
    if job_running:
        progress, current_stage = job_progress("daily_report")
        st.info("A daily report is running in the background. Navigation will not stop it.")
        st.progress(progress, text=current_stage)
        stages = ("Connect to data", "Technical screening", "Context and event checks",
                  "Trade validation", "Candidate ranking", "Final report")
        current_index = min(len(stages) - 1, int(progress / (100 / len(stages))))
        st.dataframe(pd.DataFrame([
            {"Stage": stage, "Status": "Complete" if index < current_index else
             "Running" if index == current_index else "Waiting"}
            for index, stage in enumerate(stages)
        ]), width="stretch", hide_index=True)
    report = None if job_running else st.session_state.get("current_report")
    if report:
        show_report(platform, report, database)


def analyze_page(platform: TradingPlatform) -> None:
    hero("Stock research", "Price action, momentum, evidence, and risk in one workspace.")
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
        render_analysis_workspace(platform, result)


def render_price_action_workspace(price_action: dict[str, Any],
                                  full_result: dict[str, Any]) -> None:
    """Render current setup first; keep implementation details secondary."""
    summary = price_action_summary(price_action)
    st.subheader("Setup summary")
    st.markdown("".join(badge(value) for value in (
        summary["direction"], summary["category"], summary["entry_mode"])),
        unsafe_allow_html=True)
    render_metric_cards([
        ("Setup", summary["setup"]), ("Confidence", f"{number(summary['confidence'], 0)}%"),
        ("Entry", money(summary["entry"])), ("Stop", money(summary["stop"])),
        ("Best R:R", number(max(summary["risk_reward"] or [0]), 2)),
        ("Breakout / retest", f"{summary['breakout']} / {summary['retest']}"),
    ], per_row=3)
    targets = summary["targets"]
    if targets:
        st.caption("Targets: " + " · ".join(money(target) for target in targets))
    if summary["warning"]:
        st.warning(str(summary["warning"]).replace("_", " ").title())

    lifecycle, confidence = st.columns([1, 1])
    with lifecycle.container(border=True):
        st.subheader("Setup lifecycle")
        st.caption("The entry becomes actionable only after its required stages complete.")
        st.dataframe(pd.DataFrame(setup_lifecycle_rows(price_action)),
                     width="stretch", hide_index=True)
    with confidence.container(border=True):
        st.subheader("Confidence breakdown")
        rows = setup_score_rows(price_action)
        if rows:
            score_frame = pd.DataFrame(rows).sort_values("Score")
            chart = go.Figure(go.Bar(
                x=score_frame["Score"], y=score_frame["Component"], orientation="h",
                marker_color=["#34d399" if score >= 70 else "#fbbf24" if score >= 45 else "#fb7185"
                              for score in score_frame["Score"]],
                customdata=score_frame[["Weight"]],
                hovertemplate="%{y}: %{x:.1f}/100<br>Weight: %{customdata[0]:.1f}%<extra></extra>",
            ))
            chart.update_layout(template="plotly_dark", height=max(310, len(rows) * 28),
                                margin={"l": 10, "r": 10, "t": 10, "b": 25},
                                xaxis={"range": [0, 100], "title": "Score"}, showlegend=False)
            st.plotly_chart(chart, width="stretch", config={"displaylogo": False})
        else:
            st.info("No component score is available.")

    st.subheader("Entry alternatives")
    alternatives = entry_alternative_rows(price_action)
    if alternatives:
        st.dataframe(pd.DataFrame(alternatives), width="stretch", hide_index=True,
                     column_config={
                         "Entry": st.column_config.NumberColumn(format="₹%.2f"),
                         "Stop": st.column_config.NumberColumn(format="₹%.2f"),
                         "Target 1": st.column_config.NumberColumn(format="₹%.2f"),
                         "Best R:R": st.column_config.NumberColumn(format="%.2f"),
                         "Confidence": st.column_config.ProgressColumn(min_value=0, max_value=100),
                     })
    else:
        st.info("No entry passes the current stop-distance, opposing-zone, and risk/reward gates.")

    structure_col, zone_col = st.columns(2)
    with structure_col.container(border=True):
        st.subheader("Active market structure")
        structure = price_action.get("market_structure") or {}
        render_metric_cards([
            ("State", structure.get("structure", "RANGE")),
            ("Latest event", (structure.get("latest_event") or {}).get("event_type", "—")),
        ], per_row=2)
        events = [event for event in structure.get("events", [])
                  if event.get("event_type") in ("HH", "HL", "LH", "LL", "BOS", "CHOCH",
                                                 "FAILED_HIGHER_HIGH", "FAILED_LOWER_LOW")][-12:]
        if events:
            st.dataframe(pd.DataFrame([{
                "Event": event["event_type"], "Time": display_date(event.get("confirmation_timestamp")),
                "Price": event.get("price"), "Confidence": event.get("confidence"),
            } for event in events]), width="stretch", hide_index=True)
    with zone_col.container(border=True):
        st.subheader("Nearest zones")
        midpoint = float(summary["entry"] or 0)
        zones = sorted(price_action.get("zones") or [],
                       key=lambda zone: abs(float(zone.get("midpoint") or 0) - midpoint))[:6]
        if zones:
            st.dataframe(pd.DataFrame([{
                "Type": zone.get("type"), "Range": f"{money(zone.get('lower'))} – {money(zone.get('upper'))}",
                "Strength": zone.get("classification"), "Score": zone.get("strength_score"),
                "Touches": zone.get("touches"), "Retests": zone.get("successful_retest_count"),
            } for zone in zones]), width="stretch", hide_index=True)
        else:
            st.info("No confirmed support/resistance zone is available.")

    rejection_rows = grouped_setup_rejections(price_action)
    with st.expander(f"Rejection reasons ({len(rejection_rows)})",
                     expanded=bool(rejection_rows)):
        if rejection_rows:
            st.dataframe(pd.DataFrame(rejection_rows), width="stretch", hide_index=True)
        else:
            st.success("No active technical rejection reason.")

    option = option_safety_view({
        **price_action,
        "option_selling": (full_result.get("option_selling")
                           or (full_result.get("option_strategy") or {}).get("technical_validation")),
        "entry_validation": full_result.get("entry_validation"),
    })
    with st.expander("Option-selling safety", expanded=option["available"]):
        if option["available"]:
            render_metric_cards([
                ("Nearest support", money(option["support"])),
                ("Nearest resistance", money(option["resistance"])),
                ("Strike safety", number(option["strike_safety"], 2)),
                ("Breakout", option["breakout"]), ("Retest", option["retest"]),
                ("Invalidation distance", number(option["invalidation_distance"], 2)),
            ], per_row=3)
            for warning in option["warnings"]:
                st.warning(str(warning).replace("_", " ").title())
            points = [(name, option.get(key), color) for name, key, color in (
                ("Support", "support", "#34d399"), ("Invalidation", "invalidation", "#fb7185"),
                ("Strike", "strike", "#fbbf24"), ("Breakeven", "breakeven", "#a78bfa"),
                ("Spot", "spot", "#60a5fa"), ("Resistance", "resistance", "#fb7185"))
                      if option.get(key) is not None]
            if points:
                safety_chart = go.Figure()
                safety_chart.add_trace(go.Scatter(
                    x=[point[1] for point in points], y=["Technical map"] * len(points),
                    mode="markers+text", text=[point[0] for point in points],
                    textposition="top center",
                    marker={"size": 14, "color": [point[2] for point in points]},
                    hovertemplate="%{text}: ₹%{x:.2f}<extra></extra>"))
                safety_chart.update_layout(
                    template="plotly_dark", height=180, showlegend=False,
                    margin={"l": 10, "r": 10, "t": 35, "b": 35},
                    yaxis={"visible": False}, xaxis_title="Underlying price / strike")
                st.plotly_chart(safety_chart, width="stretch", config={"displaylogo": False})
        else:
            st.info("Option-chain validation has not been run for this equity analysis.")

    with st.expander("Historical events and machine-readable details"):
        history_tabs = st.tabs(["Patterns", "Structure", "Zones", "Raw setup"])
        with history_tabs[0]:
            st.json(price_action.get("pattern_history") or [], expanded=False)
        with history_tabs[1]:
            st.json((price_action.get("market_structure") or {}).get("events") or [], expanded=False)
        with history_tabs[2]:
            st.json(price_action.get("zones") or [], expanded=False)
        with history_tabs[3]:
            st.json(price_action, expanded=False)


def render_analysis_workspace(platform: TradingPlatform, result: dict[str, Any]) -> None:
    symbol = str(result.get("symbol", "UNKNOWN"))
    analysis, decision = result.get("analysis") or {}, result.get("decision") or {}
    entry, plan = result.get("entry") or {}, result.get("trade_plan") or {}
    price_action = (result.get("price_action")
                    or analysis.get("price_action")
                    or (analysis.get("metadata") or {}).get("price_action")
                    or ((result.get("technical") or {}).get("metadata") or {}).get("price_action")
                    or {})
    active_entry = (price_action.get("entries") or [{}])[0]
    active_pattern = (price_action.get("patterns") or [{}])[0]
    levels = {
        "entry": plan.get("entry", entry.get("entry_price", entry.get("entry",
                 active_entry.get("entry_price")))),
        "stop_loss": plan.get("stop_loss", entry.get("stop_loss", active_entry.get("stop_loss"))),
        "target_1": plan.get("target", plan.get("target_1", entry.get("target",
                    (active_entry.get("targets") or [None])[0]))),
        "support": analysis.get("support"), "resistance": analysis.get("resistance"),
        "zones": price_action.get("zones", []),
        "swings": [
            {**pivot, "label": next((
                event["event_type"] for event in price_action.get("market_structure", {}).get("events", [])
                if event.get("timestamp") == pivot.get("timestamp")
                and event.get("event_type") in ("HH", "HL", "LH", "LL")), pivot.get("type", ""))}
            for pivot in price_action.get("market_structure", {}).get("confirmed_pivots", [])
        ],
        "breakout": ({
            "timestamp": price_action["breakout"].get("breakout_candle"),
            "price": price_action["breakout"].get("breakout_price")}
            if price_action.get("breakout") else None),
        "retest": ({
            "timestamp": price_action["retest"].get("retest_timestamp"),
            "price": price_action["retest"].get("entry_price")
                     or price_action["breakout"].get("breakout_price")}
            if price_action.get("retest") else None),
        "confirmation": ({
            "timestamp": price_action["retest"].get("confirmation_timestamp"),
            "price": price_action["retest"].get("entry_price")}
            if price_action.get("retest") and price_action["retest"].get("confirmation_timestamp") else None),
        "pattern": ({
            "timestamp": (active_pattern.get("candle_indexes") or [None])[-1],
            "price": active_pattern.get("confirmation_price"),
            "name": active_pattern.get("pattern")}
            if active_pattern else None),
        "price_action_targets": active_entry.get("targets", []),
    }
    verdict = decision.get("action") or decision.get("decision") or decision.get("signal") or "ANALYZED"
    hero(symbol, str(decision.get("reason") or "Technical research result"), [verdict])
    technical = analysis.get("technical_score", analysis.get("score"))
    render_metric_cards([
        ("Verdict", verdict), ("Technical score", number(technical, 0)),
        ("Entry", money(levels["entry"])), ("Stop", money(levels["stop_loss"])),
        ("Position size", number((result.get("position_size") or {}).get("quantity"), 0)),
    ])
    render_price_chart(platform, symbol, levels)
    tabs = st.tabs(["Verdict & risk", "Technical evidence", "Price action", "Setup", "Raw analysis"])
    with tabs[0]:
        left, right = st.columns(2)
        with left.container(border=True):
            st.subheader("Trade plan")
            st.json(plan, expanded=True)
        with right.container(border=True):
            st.subheader("Position sizing")
            st.json(result.get("position_size") or {}, expanded=True)
    with tabs[1]:
        cols = st.columns(3)
        for column, label, payload in zip(cols, ("Analysis", "Breakout", "Candlestick"),
                                          (analysis, result.get("breakout"), result.get("candlestick"))):
            with column.container(border=True):
                st.subheader(label)
                st.json(payload or {}, expanded=True)
    with tabs[2]:
        if not price_action:
            st.info("Rich price-action analysis is unavailable for this saved result.")
        else:
            render_price_action_workspace(price_action, result)
    with tabs[3]:
        st.json(result.get("setup_evaluation") or {}, expanded=True)
        st.json(result.get("market_quality") or {}, expanded=False)
    with tabs[4]:
        st.json(result, expanded=False)


def trade_performance(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate transparent journal statistics from closed trades."""
    closed = [trade for trade in trades if trade.get("status") == "CLOSED"]
    pnls = [float(trade.get("realized_pnl") or 0) for trade in closed]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    invested_risk = []
    multiples = []
    for trade, pnl in zip(closed, pnls):
        stop = trade.get("stop_loss")
        if stop is not None:
            risk = abs(float(trade["entry_price"]) - float(stop)) * int(trade["quantity"])
            if risk > 0:
                invested_risk.append(risk)
                multiples.append(pnl / risk)
    return {
        "closed": len(closed), "wins": len(wins), "losses": len(losses),
        "win_rate": (len(wins) / len(closed) * 100) if closed else 0,
        "net_pnl": sum(pnls), "expectancy": (sum(pnls) / len(closed)) if closed else 0,
        "average_win": (sum(wins) / len(wins)) if wins else 0,
        "average_loss": (sum(losses) / len(losses)) if losses else 0,
        "average_r": (sum(multiples) / len(multiples)) if multiples else None,
    }


def report_changes(current: dict[str, Any], previous: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return user-facing candidate transitions between immutable report snapshots."""
    def indexed(report: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for bucket, default in (("trades", "TRADE"), ("watchlist", "WATCHLIST"),
                                ("rejected", "REJECTED")):
            for candidate in (report or {}).get(bucket, []):
                item = dict(candidate)
                item["_status"] = item.get("status") or default
                result[str(item.get("symbol"))] = item
        return result
    now, before = indexed(current), indexed(previous)
    rows = []
    for symbol in sorted(set(now) | set(before)):
        latest, older = now.get(symbol), before.get(symbol)
        old_score = (older or {}).get("quality_score")
        new_score = (latest or {}).get("quality_score")
        old_action = (older or {}).get("final_action")
        new_action = (latest or {}).get("final_action")
        old_rank, new_rank = (older or {}).get("rank"), (latest or {}).get("rank")
        old_breakout = bool(((older or {}).get("technical") or {}).get("breakout", {}).get("confirmed"))
        new_breakout = bool(((latest or {}).get("technical") or {}).get("breakout", {}).get("confirmed"))
        if latest and not older:
            change = "NEW"
        elif older and not latest:
            change = "REMOVED"
        elif latest["_status"] != older["_status"]:
            change = ("BLOCKER CLEARED" if older["_status"] == "REJECTED"
                      and latest["_status"] != "REJECTED" else "STATUS CHANGED")
        elif old_action != new_action:
            change = "ACTION CHANGED"
        elif not old_breakout and new_breakout:
            change = "BREAKOUT CONFIRMED"
        elif old_rank is not None and new_rank is not None and old_rank != new_rank:
            change = "RANK MOVED"
        elif old_score != new_score:
            change = "SCORE CHANGED"
        else:
            continue
        rows.append({"Symbol": symbol, "Change": change,
                     "Previous status": (older or {}).get("_status", "—"),
                     "Current status": (latest or {}).get("_status", "—"),
                     "Previous score": old_score, "Current score": new_score,
                     "Previous rank": old_rank, "Current rank": new_rank,
                     "Score change": (float(new_score) - float(old_score)
                                      if new_score is not None and old_score is not None else None)})
    return rows


def decision_timeline(candidate: dict[str, Any]) -> list[dict[str, str]]:
    """Summarize the pipeline gates already recorded in a candidate snapshot."""
    gates = [
        ("Universe scan", True, "Symbol entered the analyzed shortlist."),
        ("Technical quality", candidate.get("quality_score") is not None,
         f"Quality {number(candidate.get('quality_score'), 0)} · grade {candidate.get('quality_grade', '—')}"),
        ("Entry setup", bool(candidate.get("entry_selection") or candidate.get("selection_status")),
         str(candidate.get("selection_reason") or "No entry explanation recorded.")),
        ("Event risk", bool(candidate.get("event_risk")),
         f"Risk score {(candidate.get('event_risk') or {}).get('event_risk_score', 'unavailable')}"),
        ("Option validation", bool(candidate.get("option_trade_approval")),
         str((candidate.get("option_trade_approval") or {}).get("status", "Not evaluated"))),
        ("Final decision", candidate.get("status") == "TRADE",
         f"{candidate.get('status', 'UNKNOWN')} · {candidate.get('final_action', 'NO ACTION')}"),
    ]
    return [{"Stage": stage, "State": "PASSED" if passed else "NOT PASSED", "Detail": detail}
            for stage, passed, detail in gates]


def outcome_rows(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for trade in trades:
        if trade.get("status") != "CLOSED":
            continue
        entry, exit_price = float(trade["entry_price"]), float(trade.get("exit_price") or 0)
        direction = 1 if trade.get("side") == "BUY" else -1
        return_percent = ((exit_price - entry) * direction / entry * 100) if entry else 0
        risk = (abs(entry - float(trade["stop_loss"])) * int(trade["quantity"])
                if trade.get("stop_loss") is not None else None)
        pnl = float(trade.get("realized_pnl") or 0)
        rows.append({"Symbol": trade.get("symbol"), "Strategy": trade.get("strategy") or "Unspecified",
                     "Entry date": trade.get("entry_date"), "Exit date": trade.get("exit_date"),
                     "P&L": pnl, "Return %": return_percent,
                     "R multiple": pnl / risk if risk else None,
                     "Outcome": "PROFIT" if pnl >= 0 else "LOSS",
                     "Review": ("Setup produced a profitable outcome; review whether the exit captured the planned target."
                                if pnl >= 0 else
                                "Setup lost money; review entry timing, market regime, and stop placement.")})
    return rows


def render_decision_timeline(candidate: dict[str, Any]) -> None:
    rows = decision_timeline(candidate)
    for index, row in enumerate(rows, 1):
        state = badge("APPROVED" if row["State"] == "PASSED" else "REVIEW")
        st.markdown(f"**{index}. {row['Stage']}** {state}  \n{row['Detail']}", unsafe_allow_html=True)


def render_performance_dashboard(trades: list[dict[str, Any]]) -> None:
    performance = trade_performance(trades)
    render_metric_cards([
        ("Net realized P&L", money(performance["net_pnl"])),
        ("Win rate", f"{performance['win_rate']:.1f}%"),
        ("Expectancy / trade", money(performance["expectancy"])),
        ("Average winner", money(performance["average_win"])),
        ("Average R", number(performance["average_r"])),
    ])
    closed = [trade for trade in trades if trade.get("status") == "CLOSED"]
    if not closed:
        st.info("Close your first tracked trade to unlock the equity curve and strategy breakdown.")
        return
    frame = pd.DataFrame(closed).sort_values(["exit_date", "id"])
    frame["cumulative_pnl"] = frame["realized_pnl"].fillna(0).astype(float).cumsum()
    left, right = st.columns([2, 1])
    curve = go.Figure(go.Scatter(x=frame["exit_date"], y=frame["cumulative_pnl"], mode="lines+markers",
                                 fill="tozeroy", line={"color": "#34d399", "width": 3}))
    curve.update_layout(title="Realized equity curve", template="plotly_dark", height=350,
                        margin={"l": 10, "r": 10, "t": 50, "b": 10}, yaxis_title="P&L (₹)")
    left.plotly_chart(curve, width="stretch", config={"displaylogo": False})
    grouped = frame.groupby(frame["strategy"].fillna("Unspecified"))["realized_pnl"].sum().sort_values()
    bars = go.Figure(go.Bar(x=grouped.values, y=grouped.index, orientation="h",
                            marker_color=["#34d399" if item >= 0 else "#fb7185" for item in grouped.values]))
    bars.update_layout(title="P&L by strategy", template="plotly_dark", height=350,
                       margin={"l": 10, "r": 10, "t": 50, "b": 10})
    right.plotly_chart(bars, width="stretch", config={"displaylogo": False})


def history_page(platform: TradingPlatform, database: ReportDatabase) -> None:
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
            show_report(platform, report, database)


def trade_tracker_page(platform: TradingPlatform, database: ReportDatabase) -> None:
    hero("Positions & risk", "Monitor live positions, portfolio exposure, and the paper-trade journal.",
         ["Paper trading only"])
    all_trades = database.list_actual_trades()
    active_trade_status_panel(platform, database)
    st.divider()
    with st.expander("Portfolio performance", expanded=True):
        render_performance_dashboard(all_trades)
        if all_trades:
            exposure = pd.DataFrame(all_trades)
            open_frame = exposure[exposure["status"] == "OPEN"].copy()
            if not open_frame.empty:
                open_frame["Capital"] = open_frame["entry_price"] * open_frame["quantity"]
                open_frame["Risk"] = (open_frame["entry_price"] - open_frame["stop_loss"]).abs() * open_frame["quantity"]
                deployed, total_risk = open_frame["Capital"].sum(), open_frame["Risk"].fillna(0).sum()
                risk_budget = float(os.getenv("TRADING_CAPITAL", "100000")) * float(
                    os.getenv("TRADING_RISK_PERCENT", "1")) / 100
                risk_columns = st.columns([1, 1, 2])
                risk_columns[0].metric("Capital deployed", money(deployed))
                risk_columns[1].metric("Risk at stops", money(total_risk))
                gauge = go.Figure(go.Indicator(
                    mode="gauge+number", value=(total_risk / risk_budget * 100 if risk_budget else 0),
                    number={"suffix": "%"}, title={"text": "Risk budget used"},
                    gauge={"axis": {"range": [0, 150]}, "bar": {"color": "#60a5fa"},
                           "steps": [{"range": [0, 75], "color": "rgba(16,185,129,.2)"},
                                     {"range": [75, 100], "color": "rgba(245,158,11,.25)"},
                                     {"range": [100, 150], "color": "rgba(244,63,94,.25)"}],
                           "threshold": {"line": {"color": "#fb7185", "width": 4}, "value": 100}}))
                gauge.update_layout(height=230, margin={"l": 20, "r": 20, "t": 50, "b": 10},
                                    template="plotly_dark")
                risk_columns[2].plotly_chart(gauge, width="stretch", config={"displaylogo": False})
                st.subheader("Open exposure")
                st.dataframe(open_frame[["symbol", "side", "strategy", "Capital", "Risk", "hold_until"]],
                             width="stretch", hide_index=True,
                             column_config={"Capital": st.column_config.NumberColumn(format="₹%.2f"),
                                            "Risk": st.column_config.NumberColumn(format="₹%.2f")})
                if len(open_frame) > 1:
                    grouped_exposure = open_frame.groupby(open_frame["strategy"].fillna("Unspecified"))["Capital"].sum()
                    st.plotly_chart(go.Figure(go.Pie(labels=grouped_exposure.index,
                                                     values=grouped_exposure.values, hole=.55)),
                                    width="stretch", config={"displaylogo": False})
                    largest = float(grouped_exposure.max() / grouped_exposure.sum() * 100)
                    if largest >= 50:
                        st.warning(f"Concentration warning: the largest strategy represents {largest:.0f}% of open capital.")
        outcomes = outcome_rows(all_trades)
        if outcomes:
            st.subheader("Outcome attribution")
            st.dataframe(pd.DataFrame(outcomes), width="stretch", hide_index=True,
                         column_config={"P&L": st.column_config.NumberColumn(format="₹%.2f"),
                                        "Return %": st.column_config.NumberColumn(format="%.2f%%"),
                                        "R multiple": st.column_config.NumberColumn(format="%.2fR"),
                                        "Review": st.column_config.TextColumn(width="large")})
        calendar_rows = []
        for trade in all_trades:
            calendar_rows.append({"Date": trade.get("entry_date"), "Event": "ENTRY",
                                  "Symbol": trade.get("symbol"), "Detail": trade.get("strategy")})
            if trade.get("hold_until") and trade.get("status") == "OPEN":
                calendar_rows.append({"Date": trade["hold_until"], "Event": "REVIEW",
                                      "Symbol": trade.get("symbol"), "Detail": "Hold-until review"})
            if trade.get("expiry") and trade.get("instrument_type") == "OPTION":
                calendar_rows.append({"Date": trade["expiry"], "Event": "EXPIRY",
                                      "Symbol": trade.get("symbol"), "Detail": trade.get("option_type")})
            if trade.get("exit_date"):
                calendar_rows.append({"Date": trade["exit_date"], "Event": "EXIT",
                                      "Symbol": trade.get("symbol"), "Detail": money(trade.get("realized_pnl"))})
        if calendar_rows:
            st.subheader("Trading calendar")
            calendar = pd.DataFrame(calendar_rows).sort_values("Date")
            st.dataframe(calendar, width="stretch", hide_index=True,
                         column_config={"Date": st.column_config.DateColumn(format="DD MMM YYYY")})
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
            capital = float(entry_price) * int(quantity)
            risk = (abs(float(entry_price) - float(stop_loss)) * int(quantity)
                    if stop_loss else None)
            st.info(f"Capital recorded: {money(capital)} · Risk to stop: {money(risk)}")
            add_confirmed = st.checkbox("I confirm these trade and risk details are correct")
            add = st.form_submit_button("Save actual trade", type="primary")
        if add:
            if not add_confirmed:
                st.error("Confirm the trade and risk details before saving.")
                return
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
    st.download_button("Export position history CSV",
                       trade_frame[display_columns].to_csv(index=False).encode(),
                       file_name="position-history.csv", mime="text/csv")

    st.subheader("Delete an incorrectly entered trade")
    trade_labels = {f"#{trade['id']} — {trade['symbol']} — {trade['status']}": trade["id"] for trade in trades}
    delete_label = st.selectbox("Trade record", list(trade_labels), key="delete-trade-record")
    delete_confirmed = st.checkbox("I confirm this trade record should be permanently deleted")
    if st.button("Delete selected trade record", disabled=not delete_confirmed):
        trade_id = trade_labels[delete_label]
        if database.delete_actual_trade(trade_id):
            st.success(f"Deleted actual trade record #{trade_id}. This cannot be undone.")
            st.rerun()


def watchlists_page(platform: TradingPlatform, database: ReportDatabase) -> None:
    hero("Watchlists & alerts", "Organize research symbols and monitor one-shot price conditions.")
    watchlists = database.list_watchlists()
    create_col, alert_col = st.columns(2)
    with create_col.container(border=True):
        st.subheader("Create watchlist")
        with st.form("create-watchlist", clear_on_submit=True):
            name = st.text_input("Name", placeholder="Breakout candidates")
            create = st.form_submit_button("Create", type="primary")
        if create:
            try:
                database.create_watchlist(name)
                st.success("Watchlist created.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    with alert_col.container(border=True):
        st.subheader("Create price alert")
        with st.form("create-alert", clear_on_submit=True):
            columns = st.columns(3)
            alert_symbol = columns[0].text_input("Symbol", placeholder="SBIN").upper()
            condition = columns[1].selectbox("Condition", ("ABOVE", "BELOW"))
            target = columns[2].number_input("Price", min_value=0.01, value=100.0)
            alert_label = st.text_input("Note", placeholder="Breakout entry")
            add_alert = st.form_submit_button("Add alert", type="primary")
        if add_alert:
            try:
                database.add_price_alert(alert_symbol, condition, target, alert_label)
                st.success("Alert created.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    watchlists = database.list_watchlists()
    if watchlists:
        labels = {f"{item['name']} ({item['symbol_count']})": item for item in watchlists}
        selected_label = st.selectbox("Active watchlist", list(labels))
        selected = labels[selected_label]
        with st.form("add-watchlist-symbol", clear_on_submit=True):
            add_columns = st.columns([3, 1])
            watch_symbol = add_columns[0].text_input("Add NSE symbol", placeholder="RELIANCE").upper()
            add_symbol = add_columns[1].form_submit_button("Add symbol", type="primary")
        if add_symbol:
            try:
                database.add_watchlist_symbol(selected["id"], watch_symbol)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        symbols = database.watchlist_symbols(selected["id"])
        if symbols:
            rows = []
            for item in symbols:
                current = _polled_equity_price(platform, item["symbol"])
                rows.append({"Symbol": item["symbol"], "Current price": current,
                             "Added": item["added_at"]})
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True,
                         column_config={"Current price": st.column_config.NumberColumn(format="₹%.2f")})
            remove = st.selectbox("Remove symbol", [item["symbol"] for item in symbols])
            if st.button("Remove from watchlist", type="secondary"):
                database.remove_watchlist_symbol(selected["id"], remove)
                st.rerun()
        else:
            st.info("This watchlist is empty.")
        confirm_delete = st.checkbox("Confirm deletion of this watchlist")
        if st.button("Delete watchlist", disabled=not confirm_delete):
            database.delete_watchlist(selected["id"])
            st.rerun()
    else:
        st.info("Create a watchlist to begin organizing research symbols.")
    st.subheader("Price alerts")
    alerts = database.list_price_alerts()
    if not alerts:
        st.info("No price alerts configured.")
        return
    alert_rows = []
    for alert in alerts:
        current = _polled_equity_price(platform, alert["symbol"]) if alert["enabled"] else None
        triggered = bool(alert["enabled"] and current is not None and
                         ((alert["condition"] == "ABOVE" and current >= alert["target_price"])
                          or (alert["condition"] == "BELOW" and current <= alert["target_price"])))
        if triggered:
            database.trigger_price_alert(alert["id"])
            st.toast(f"{alert['symbol']} crossed {alert['condition']} {money(alert['target_price'])}", icon="🔔")
        alert_rows.append({"ID": alert["id"], "Symbol": alert["symbol"],
                           "Condition": alert["condition"], "Target": alert["target_price"],
                           "Current": current, "Note": alert["label"],
                           "Status": "TRIGGERED" if triggered or alert["triggered_at"] else "ACTIVE"})
    st.dataframe(pd.DataFrame(alert_rows), width="stretch", hide_index=True,
                 column_config={"Target": st.column_config.NumberColumn(format="₹%.2f"),
                                "Current": st.column_config.NumberColumn(format="₹%.2f")})
    delete_alert = st.selectbox("Delete alert", [f"#{row['ID']} · {row['Symbol']}" for row in alert_rows])
    if st.button("Delete selected alert", type="secondary"):
        database.delete_price_alert(int(delete_alert.split(" · ")[0].removeprefix("#")))
        st.rerun()


def system_page(platform: TradingPlatform, database: ReportDatabase) -> None:
    hero("System & preferences", "Data health, accessibility, and workspace defaults.")
    preferences = database.get_preferences()
    with st.form("ui-preferences"):
        st.subheader("Interface preferences")
        options = st.columns(3)
        high_contrast = options[0].toggle("High contrast", value=bool(preferences.get("high_contrast")))
        reduce_motion = options[1].toggle("Reduce motion", value=bool(preferences.get("reduce_motion")))
        compact_mode = options[2].toggle("Compact layout", value=bool(preferences.get("compact_mode")))
        defaults = st.columns(3)
        opportunity_density = defaults[0].selectbox(
            "Default opportunity density", ("Comfortable", "Compact", "Table only"),
            index=("Comfortable", "Compact", "Table only").index(
                preferences.get("opportunity_density", "Comfortable")))
        default_report_limit = defaults[1].number_input(
            "Default maximum trades", 1, 50, int(preferences.get("default_report_limit", 5)))
        default_minimum_score = defaults[2].number_input(
            "Default technical score", 0, 100, int(preferences.get("default_minimum_score", 40)))
        default_page = st.selectbox(
            "Default workspace", ("Dashboard", "Opportunities", "Positions", "Daily report",
                                  "Analyze stock", "Bearish options", "Watchlists & alerts",
                                  "Report history", "System & diagnostics"),
            index=(list(("Dashboard", "Opportunities", "Positions", "Daily report",
                         "Analyze stock", "Bearish options", "Watchlists & alerts",
                         "Report history", "System & diagnostics")).index(preferences["default_page"])
                   if preferences.get("default_page") in {
                       "Dashboard", "Opportunities", "Positions", "Daily report", "Analyze stock",
                       "Bearish options", "Watchlists & alerts", "Report history",
                       "System & diagnostics"} else 0),
        )
        save_preferences = st.form_submit_button("Save preferences", type="primary")
    if save_preferences:
        for key, setting in {"high_contrast": high_contrast, "reduce_motion": reduce_motion,
                             "compact_mode": compact_mode, "default_page": default_page,
                             "opportunity_density": opportunity_density,
                             "default_report_limit": default_report_limit,
                             "default_minimum_score": default_minimum_score}.items():
            database.set_preference(key, setting)
        st.success("Preferences saved. Reloading the workspace.")
        st.rerun()
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


def _report_id_for(database: ReportDatabase, report: dict[str, Any] | None) -> int | None:
    if not report:
        rows = database.list_reports(1)
        return int(rows[0]["id"]) if rows else None
    run_id = str(report.get("run_id") or "")
    row = next((item for item in database.list_reports(100)
                if str(item.get("run_id")) == run_id), None)
    return int(row["id"]) if row else None


def render_ai_chat(database: ReportDatabase, symbol: str | None = None,
                   report: dict[str, Any] | None = None, scope: str = "global",
                   compact: bool = False) -> None:
    """Context-aware analyst conversation grounded in saved data and safe source reads."""
    tools = StockAnalyzerTools(database, Path(__file__).resolve().parent)
    session_api_key = st.session_state.get("_openai_session_api_key")
    session_gemini_key = st.session_state.get("_gemini_session_api_key")
    provider = st.selectbox(
        "Assistant provider",
        ("Google Gemini", "Codex via ChatGPT Plus", "OpenAI API", "Local model (Ollama)",
         "ChatGPT via MCP"),
        key=f"ai-provider-{scope}",
        help="Gemini supports a limited free tier. Codex uses ChatGPT plan limits; OpenAI API is billed separately.")
    if provider == "Google Gemini":
        analyst = GeminiAnalyst(api_key=session_gemini_key)
        provider_ready = analyst.configured
    elif provider == "Codex via ChatGPT Plus":
        analyst = CodexService(Path(__file__).resolve().parent)
        provider_ready = analyst.configured
    elif provider == "OpenAI API":
        analyst = OpenAIAnalyst(api_key=session_api_key)
        provider_ready = analyst.configured
    elif provider == "Local model (Ollama)":
        analyst = OllamaAnalyst()
        provider_ready = True
    else:
        analyst, provider_ready = None, False
    report_id = _report_id_for(database, report)
    history_key = f"ai_chat_history_{scope}"
    history = st.session_state.setdefault(history_key, [])
    status_columns = st.columns(3)
    status_columns[0].metric("Provider", "READY" if provider_ready else "SETUP NEEDED")
    status_columns[1].metric("Report", report.get("run_id", "Latest") if report else "Latest")
    status_columns[2].metric("Stock context", symbol or "Entire report")
    if provider == "ChatGPT via MCP":
        st.info("This mode keeps the conversation in ChatGPT and gives it read-only access to this "
                "project through MCP. It does not use an OpenAI API key in Streamlit.")
        with st.expander("Connect this project to ChatGPT", expanded=True):
            st.markdown("1. Start the MCP HTTP server on the machine hosting this app.\n"
                        "2. Put it behind an authenticated HTTPS URL or secure development tunnel.\n"
                        "3. In ChatGPT, enable Developer mode and add that URL as an MCP app.\n"
                        "4. Ask ChatGPT to use **Alphatrace** for project questions.")
            st.code(".venv\\Scripts\\python.exe run_mcp.py --transport streamable-http",
                    language="powershell")
            st.caption("Do not expose the endpoint publicly without authentication.")
        return
    if provider == "Codex via ChatGPT Plus" and not provider_ready:
        st.warning("Install Codex, then run `codex` and choose **Sign in with ChatGPT**. Restart "
                   "Streamlit afterward. No OpenAI API key is required.")
        st.code("winget install OpenJS.NodeJS.LTS\nnpm.cmd install -g @openai/codex\ncodex",
                language="powershell")
    elif provider == "Google Gemini" and not provider_ready:
        st.warning("A Google AI Studio API key is required before Gemini can answer.")
        with st.form(f"gemini-sign-in-{scope}", clear_on_submit=True):
            supplied_key = st.text_input(
                "Gemini API key", type="password", autocomplete="off",
                help="Used only in this Streamlit session. It is not saved to SQLite or project files.")
            sign_in = st.form_submit_button("Use Gemini for this session", type="primary")
        if sign_in:
            if supplied_key.strip():
                st.session_state["_gemini_session_api_key"] = supplied_key.strip()
                st.session_state.pop("_gemini_auth_error", None)
                st.rerun()
            else:
                st.error("Enter a Gemini API key to continue.")
        st.caption("For Azure, configure GEMINI_API_KEY as a secret-backed environment variable. "
                   "Google may use unpaid-tier prompts and responses to improve its products; do not "
                   "submit confidential or personal information.")
    elif provider == "Google Gemini" and session_gemini_key:
        signout_col, note_col = st.columns([1, 3])
        if signout_col.button("Sign out", key=f"gemini-sign-out-{scope}"):
            st.session_state.pop("_gemini_session_api_key", None)
            st.rerun()
        note_col.caption("Using a session-only Gemini key; it will be forgotten when this session ends.")
    elif provider == "Local model (Ollama)":
        st.info(f"Using local model `{analyst.model}` at `{analyst.base_url}`. No OpenAI API "
                "billing applies; your computer supplies the compute.")
        with st.expander("Local model setup"):
            st.code(f"winget install Ollama.Ollama\nollama pull {analyst.model}\nollama serve",
                    language="powershell")
    elif provider == "OpenAI API" and not provider_ready:
        st.warning("OpenAI sign-in is required before live answers can be generated.")
        with st.form(f"openai-sign-in-{scope}", clear_on_submit=True):
            supplied_key = st.text_input(
                "OpenAI API key", type="password", autocomplete="off",
                help="Used only in this Streamlit session. It is not saved to SQLite or project files.")
            sign_in = st.form_submit_button("Sign in for this session", type="primary")
        if sign_in:
            if supplied_key.strip():
                st.session_state["_openai_session_api_key"] = supplied_key.strip()
                st.session_state.pop("_openai_auth_error", None)
                st.rerun()
            else:
                st.error("Enter an OpenAI API key to continue.")
        st.caption("For persistent server-side setup, configure OPENAI_API_KEY in `.env` and restart. "
                   "A ChatGPT subscription login is separate from OpenAI API authentication.")
    elif provider == "OpenAI API" and session_api_key:
        signout_col, note_col = st.columns([1, 3])
        if signout_col.button("Sign out", key=f"openai-sign-out-{scope}"):
            st.session_state.pop("_openai_session_api_key", None)
            st.rerun()
        note_col.caption("Signed in with a session-only API key; it will be forgotten when this session ends.")
    if st.session_state.get("_openai_auth_error"):
        st.error(st.session_state["_openai_auth_error"])
    if st.session_state.get("_gemini_auth_error"):
        st.error(st.session_state["_gemini_auth_error"])
    for message in history[-12:]:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message.get("meta"):
                st.caption(message["meta"])
    suggestions = (["Why was this stock selected?", "Explain the fall risk in simple language.",
                    "Which checks passed or failed?", "How is this calculation implemented?"]
                   if symbol else
                   ["Summarize today's selected stocks.", "Which candidate has the lowest path risk?",
                    "Why are stocks on the watchlist?", "Explain the project decision architecture."])
    if not compact:
        st.caption("Suggested: " + " · ".join(suggestions))
    with st.form(f"ai-question-{scope}", clear_on_submit=True):
        question = st.text_area("Ask about the current stock, report, UI, or project code",
                                height=80, placeholder=suggestions[0], key=f"ai-input-{scope}")
        ask = st.form_submit_button("Ask AI", type="primary", disabled=not provider_ready)
    clear_col, context_col = st.columns(2)
    if clear_col.button("Clear conversation", key=f"clear-ai-{scope}", disabled=not history):
        st.session_state[history_key] = []
        st.rerun()
    if context_col.toggle("Show context preview", key=f"context-preview-{scope}"):
        preview = tools.context_for_question(
            question or "current selection", UIContext(page="AI Assistant", symbol=symbol,
                                                        report_id=report_id))
        st.json(preview, expanded=False)
    if ask and question.strip():
        user_question = question.strip()
        history.append({"role": "user", "content": user_question})
        context = tools.context_for_question(
            user_question, UIContext(page="Candidate Ask AI" if symbol else "AI Assistant",
                                     symbol=symbol, report_id=report_id, active_tab="Ask AI"))
        try:
            with st.spinner("Reading the current report and relevant project evidence…"):
                result = analyst.answer(user_question, context, history[:-1])
            source_bits = [f"model {result['model']}"]
            if report_id:
                source_bits.append(f"report #{report_id}")
            if context.get("project_search", {}).get("results"):
                paths = sorted({item["path"] for item in context["project_search"]["results"]})[:4]
                source_bits.append("code: " + ", ".join(paths))
            history.append({"role": "assistant", "content": result["text"],
                            "meta": " · ".join(source_bits)})
            st.rerun()
        except OpenAIAuthenticationError as exc:
            st.session_state.pop("_openai_session_api_key", None)
            st.session_state["_openai_auth_error"] = str(exc)
            history.append({"role": "assistant", "content":
                            "Authentication is required before I can answer. Please sign in above."})
            st.rerun()
        except GeminiAuthenticationError as exc:
            st.session_state.pop("_gemini_session_api_key", None)
            st.session_state["_gemini_auth_error"] = str(exc)
            history.append({"role": "assistant", "content":
                            "Gemini authentication is required before I can answer. Please sign in above."})
            st.rerun()
        except (OpenAIConfigurationError, GeminiConfigurationError, OllamaConfigurationError,
                CodexUnavailableError, RuntimeError) as exc:
            history.append({"role": "assistant", "content": f"I could not answer: {exc}"})
            st.rerun()


def ai_assistant_page(database: ReportDatabase) -> None:
    hero("AI Analyst", "Ask grounded questions about the current UI, saved reports, candidates, "
         "risk calculations, and safe project source.", ["READ ONLY", "REPORT GROUNDED"])
    reports = database.list_reports(100)
    if not reports:
        st.info("Generate and save a daily report before asking report-specific questions.")
        render_ai_chat(database, scope="global-no-report")
        return
    labels = {f"{row['report_date']} · {row['run_id']}": row for row in reports}
    selected_label = st.selectbox("Report context", list(labels), key="ai-report-context")
    selected_row = labels[selected_label]
    report = database.get_report(int(selected_row["id"])) or {}
    candidates = [*report.get("trades", []), *report.get("watchlist", []), *report.get("rejected", [])]
    symbols = ["Entire report", *dict.fromkeys(str(item.get("symbol")) for item in candidates
                                               if item.get("symbol"))]
    selected_symbol = st.selectbox("Stock context", symbols, key="ai-symbol-context")
    render_ai_chat(database, None if selected_symbol == "Entire report" else selected_symbol,
                   report, scope=f"global-{selected_row['run_id']}-{selected_symbol}")


def codex_workspace_page(database: ReportDatabase) -> None:
    hero("Codex Developer", "Inspect, propose, and—with explicit approval—implement project fixes.",
         ["REPOSITORY SANDBOX", "NO TRADING"])
    service = CodexService(Path(__file__).resolve().parent)
    left, right, third = st.columns(3)
    left.metric("Codex CLI", "READY" if service.configured else "SETUP NEEDED")
    right.metric("Repository", "alphatrace")
    third.metric("Default permission", "PROPOSE")
    if not service.configured:
        st.warning("Codex CLI is not installed or not on PATH. Install and authenticate Codex on the "
                   "server running Streamlit, then restart the UI.")
        with st.expander("Codex setup and login", expanded=True):
            st.code("npm.cmd install -g @openai/codex\ncodex", language="powershell")
            st.caption("Choose Sign in with ChatGPT and complete login in the browser. ChatGPT Plus "
                       "can be used subject to Codex plan limits; no API key is required. Credentials "
                       "are managed by Codex and are never collected by this UI.")
            if st.button("Refresh Codex status", key="refresh-codex-status"):
                st.rerun()
    st.info("Codex is instructed not to place trades, push, deploy, install dependencies, use network "
            "access, or read credentials. Review every result and Git diff before keeping a change.")
    mode = st.radio("Permission mode", ("EXPLAIN", "PROPOSE", "IMPLEMENT"), index=1,
                    horizontal=True, key="codex-mode",
                    help="EXPLAIN and PROPOSE are read-only. IMPLEMENT can edit project files.")
    with st.form("codex-request-form"):
        request = st.text_area("Development request", height=140,
                               placeholder="Find why the risk label is unavailable and propose a tested fix.")
        confirmation = (st.checkbox(
            "I authorize Codex to edit repository files for this request. I will review the diff.")
            if mode == "IMPLEMENT" else True)
        run_codex = st.form_submit_button(
            "Run Codex", type="primary", disabled=not service.configured or not confirmation)
    if run_codex and request.strip():
        try:
            with st.spinner(f"Codex {mode.lower()} session is running…"):
                result = service.run(request.strip(), mode, confirmed=bool(confirmation))
            st.session_state["last_codex_result"] = {
                "mode": result.mode, "output": result.output,
                "return_code": result.return_code, "event_count": len(result.events),
            }
        except (CodexUnavailableError, PermissionError, ValueError, RuntimeError, OSError) as exc:
            st.error(str(exc))
    result = st.session_state.get("last_codex_result")
    if result:
        st.subheader(f"Latest Codex result · {result['mode']}")
        auth_needed = (result["return_code"] != 0 and any(
            phrase in result["output"].lower()
            for phrase in ("login", "log in", "authentication", "unauthorized", "sign in")))
        if auth_needed:
            st.warning("Codex login is required. Run `codex login` on the Streamlit server, complete "
                       "authentication, then use Refresh Codex status.")
        else:
            (st.success if result["return_code"] == 0 else st.error)(
                f"Process exit code {result['return_code']} · {result['event_count']} streamed events")
        st.markdown(result["output"])
        st.caption("Inspect `git diff` before retaining code changes. Codex does not commit or push.")


def main() -> None:
    apply_session_credentials()
    required_credential_gate()
    platform, database = services()
    preferences = database.get_preferences()
    apply_theme(preferences)
    sync_feature_jobs()
    pending_page = st.session_state.pop("pending_nav_page", None)
    if pending_page:
        # Apply navigation requests before the keyed sidebar radio is created.
        st.session_state["nav_page"] = pending_page
    with st.sidebar:
        st.markdown("## 📈 Alphatrace")
        st.caption("Research & paper-trading desk")
        navigation = {
            "Dashboard": "Dashboard", "Opportunities": "Opportunities",
            "Positions": "Positions", "Daily report": "Daily report",
            "Analyze stock": "Analyze", "Bearish options": "Bearish options",
            "AI analyst": "AI Assistant", "Codex developer": "Codex",
            "Watchlists & alerts": "Watchlists", "Report history": "History",
            "System & diagnostics": "System",
        }
        with st.form("command-palette", clear_on_submit=True):
            command = st.text_input("Quick command", placeholder="Analyze RELIANCE or open History")
            run_command = st.form_submit_button("Run", width="stretch")
        if run_command and command.strip():
            instruction = command.strip()
            page_match = next((label for label, destination in navigation.items()
                               if destination.lower() in instruction.lower()
                               or label.split("·")[-1].strip().lower() in instruction.lower()), None)
            if instruction.lower().startswith("analyze "):
                target = instruction.split(maxsplit=1)[1].strip().upper()
                start_feature_job("analysis", lambda: platform.analyze(target))
                st.session_state["analysis_symbol"] = target
                request_navigation("Analyze stock")
            elif page_match:
                request_navigation(page_match)
            else:
                st.warning("Try “Analyze RELIANCE” or “open History”.")
        if "nav_page" not in st.session_state:
            preferred = preferences.get("default_page", "Dashboard")
            legacy_pages = {
                "TODAY · Overview": "Dashboard", "TODAY · Daily report": "Daily report",
                "RESEARCH · Analyze stock": "Analyze stock",
                "RESEARCH · Bearish options": "Bearish options",
                "PORTFOLIO · Positions & performance": "Positions",
                "WATCH · Lists & alerts": "Watchlists & alerts",
                "ARCHIVE · Report history": "Report history",
                "ADMIN · System & diagnostics": "System & diagnostics",
            }
            st.session_state["nav_page"] = legacy_pages.get(preferred, preferred)
        elif st.session_state["nav_page"] not in navigation:
            # Migrate an open Streamlit session after the navigation redesign.
            legacy_pages = {
                "TODAY · Overview": "Dashboard", "TODAY · Daily report": "Daily report",
                "RESEARCH · Analyze stock": "Analyze stock",
                "RESEARCH · Bearish options": "Bearish options",
                "PORTFOLIO · Positions & performance": "Positions",
                "WATCH · Lists & alerts": "Watchlists & alerts",
                "ARCHIVE · Report history": "Report history",
                "ADMIN · System & diagnostics": "System & diagnostics",
            }
            st.session_state["nav_page"] = legacy_pages.get(st.session_state["nav_page"], "Dashboard")
        selected_page = st.radio("Workspace", tuple(navigation), label_visibility="collapsed",
                                 key="nav_page")
        page = navigation[selected_page]
        st.divider()
        india_now = datetime.now(ZoneInfo("Asia/Kolkata"))
        market_open = india_now.weekday() < 5 and (9, 15) <= (india_now.hour, india_now.minute) <= (15, 30)
        st.markdown(badge("MARKET OPEN" if market_open else "MARKET CLOSED") +
                    badge(platform.settings.market_data_source), unsafe_allow_html=True)
        st.caption(f"{india_now:%d %b · %I:%M %p} IST")
        st.caption("Paper trading only · no broker orders")
        alerts = database.list_price_alerts()
        active_alerts = sum(bool(item["enabled"]) for item in alerts)
        triggered_alerts = sum(bool(item["triggered_at"]) for item in alerts)
        open_trades = database.list_actual_trades("OPEN")
        overdue_reviews = sum(bool(item.get("hold_until") and
                                   str(item["hold_until"]) <= date.today().isoformat())
                              for item in open_trades)
        report_failed = bool(st.session_state.get("daily_report_job_error"))
        notification_count = triggered_alerts + overdue_reviews + int(report_failed)
        if active_alerts or notification_count:
            with st.expander(f"🔔 Notifications ({notification_count} important)"):
                if triggered_alerts:
                    st.success(f"{triggered_alerts} price alert(s) triggered.")
                if overdue_reviews:
                    st.warning(f"{overdue_reviews} position review date(s) are due. Open Positions.")
                if report_failed:
                    st.error("The latest daily report failed. Open Daily report for details.")
                if active_alerts:
                    st.caption(f"{active_alerts} price alert(s) remain active.")
        daily_report_status()
    if page == "Dashboard":
        dashboard(platform, database)
    elif page == "Opportunities":
        opportunities_page(platform, database)
    elif page == "Positions":
        positions_page(platform, database)
    elif page == "Daily report":
        daily_report_page(platform, database)
    elif page == "Analyze":
        analyze_page(platform)
    elif page == "Bearish options":
        bearish_options_page(platform, database)
    elif page == "AI Assistant":
        ai_assistant_page(database)
    elif page == "Codex":
        codex_workspace_page(database)
    elif page == "History":
        history_page(platform, database)
    elif page == "Watchlists":
        watchlists_page(platform, database)
    else:
        system_page(platform, database)


if __name__ == "__main__":
    main()
