"""Optional FastAPI adapter for :class:`src.application.TradingPlatform`."""

try:
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel, Field
except ImportError as exc:  # Keeps importing the analysis package dependency-light.
    raise RuntimeError("REST API requires fastapi; install requirements.txt first") from exc

from src.application.errors import PlatformError
from src.application.platform import TradingPlatform
from src.news.ai_sentiment import AISentimentAnalyzer

app = FastAPI(title="Alphatrace", version="1.0.0")
platform = TradingPlatform()


class SymbolRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=30)


class PaperTradeRequest(SymbolRequest):
    side: str
    quantity: int | None = Field(default=None, gt=0)


class OutcomeRequest(BaseModel):
    recommendation_id: str = Field(min_length=1)
    won: bool
    return_percent: float | None = None
    exit_price: float | None = Field(default=None, gt=0)
    mfe_percent: float | None = None
    mae_percent: float | None = None


def _call(operation):
    try:
        return operation()
    except PlatformError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "mode": "paper",
            "dependencies": AISentimentAnalyzer().dependency_health()}


@app.post("/analyze")
def analyze(request: SymbolRequest):
    return _call(lambda: platform.analyze(request.symbol))


@app.get("/suggestions")
def suggestions(limit: int = 5, minimum_score: int = 40, technical_only: bool = False):
    return _call(lambda: platform.suggest_stocks(limit, minimum_score)
                 if technical_only else platform.suggest_futures(limit, minimum_score))


@app.get("/daily-report")
def daily_report(limit: int = 5, minimum_score: int = 40,
                 option_month: str | None = None):
    return _call(lambda: platform.daily_report(limit, minimum_score, option_month))


@app.get("/futures-opportunities")
def futures_opportunities(limit: int = 5, include_backtest: bool = True, mode: str = 'LIVE_SCAN'):
    return _call(lambda: platform.scan_futures_opportunities(limit, include_backtest, mode))


@app.post("/backtest")
def backtest(request: SymbolRequest):
    return _call(lambda: platform.backtest(request.symbol))


@app.post("/papertrade")
def paper_trade(request: PaperTradeRequest):
    return _call(lambda: platform.paper_trade(request.symbol, request.side, request.quantity))


@app.get("/portfolio")
def portfolio():
    return platform.portfolio()


@app.post("/outcomes")
def record_outcome(request: OutcomeRequest):
    return _call(lambda: platform.record_trade_outcome(
        request.recommendation_id, request.won, request.return_percent,
        request.exit_price, request.mfe_percent, request.mae_percent,
    ))

# Additive workspace routes. Disabled workspaces reject operations before broker reads.
def _futures_workspace_call(operation):
    from src.futures_workspace.service import FuturesWorkspace
    try:
        return operation(FuturesWorkspace(TradingPlatform(settings=platform.settings)))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

class FuturesMembershipRequest(SymbolRequest):
    action: str
    category: str | None = None

@app.get('/futures-trading')
def futures_trading_status():
    def read(w):
        w.check_enabled()
        return {'memberships':w.store.members(),'jobs':w.store.jobs(),'performance':w.performance()}
    return _futures_workspace_call(read)

@app.post('/futures-trading/rotate')
def futures_trading_rotate():
    return _futures_workspace_call(lambda w:w.rotate())

@app.post('/futures-trading/recheck')
def futures_trading_recheck():
    return _futures_workspace_call(lambda w:w.rotate(selected_only=True))

@app.post('/futures-trading/scan-selected')
def futures_trading_scan_selected():
    return _futures_workspace_call(lambda w:w.scan_daily())

@app.post('/futures-trading/universe-refresh')
def futures_trading_refresh():
    return _futures_workspace_call(lambda w:w.refresh_universe())

@app.post('/futures-trading/membership')
def futures_trading_membership(request: FuturesMembershipRequest):
    return _futures_workspace_call(lambda w:w.manage(request.symbol,request.action,request.category))

@app.get('/futures-trading/versions')
def futures_trading_versions():
    def read(w):
        w.check_enabled()
        return w.store.versions()
    return _futures_workspace_call(read)

@app.post('/futures-trading/versions/{version}/restore')
def futures_trading_restore(version:int):
    def restore(w):
        w.check_enabled()
        return w.store.rollback(version)
    return _futures_workspace_call(restore)

class FuturesReplayRequest(BaseModel):
    universe_snapshots: list[dict] = Field(default_factory=list)
    equity_histories: dict = Field(default_factory=dict)
    futures_histories: dict = Field(default_factory=dict)

@app.post('/futures-trading/research-replay')
def futures_trading_replay(request:FuturesReplayRequest):
    return _futures_workspace_call(lambda w:w.replay(request.model_dump()))
