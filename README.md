# AI Quantitative Trading Platform

An analysis-first trading platform for NSE equities. It uses Zerodha Kite for
historical OHLCV and live quotes, then provides technical analysis, trade
planning, backtesting, and a stateful paper-trading workflow.
Live order placement is intentionally not exposed by the public platform API.

## Quick start

Configure a valid daily Zerodha Kite access token in `.env`, then run:

```env
KITE_API_KEY=your_kite_api_key
KITE_ACCESS_TOKEN=your_daily_access_token
```

```bash
.venv/bin/python main.py analyze RELIANCE
.venv/bin/python main.py suggest --limit 5
.venv/bin/python main.py daily-report --limit 5
.venv/bin/python main.py backtest RELIANCE
.venv/bin/python main.py papertrade RELIANCE BUY --quantity 10
.venv/bin/python main.py portfolio
```

Runtime defaults can be configured without changing source code:

```bash
export TRADING_CAPITAL=100000
export TRADING_RISK_PERCENT=1
export MARKET_DATA_SOURCE=kite
export OPTION_CAPITAL=2500000
export OPTION_RISK_PER_TRADE=100000
```

## Application API

`TradingPlatform` is the supported integration point for scripts and services:

```python
from src.application import TradingPlatform

platform = TradingPlatform()
suggestions = platform.suggest_stocks(limit=5)
report = platform.analyze("RELIANCE")
backtest = platform.backtest("RELIANCE")
order = platform.paper_trade("RELIANCE", "BUY", quantity=10)
```

Start with `suggest_stocks()` to rank the configured market universe. Candidates
are filtered to actionable BUY, BUY ON DIP, or WATCH setups and include a trade
plan and suggested risk-based quantity. It normalizes symbols, validates
orders, calculates a position size from the configured capital/risk limit, and
raises clear application errors when data is unavailable or input is invalid.

## REST API

Install the dependencies, then start the API:

```bash
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m spacy download en_core_web_sm
.venv/bin/uvicorn src.api:app --reload
```

News analysis runs locally without a paid API. FinBERT classifies each
headline and description as positive, negative, or neutral, while spaCy
extracts named entities such as companies, people, and locations. The first
live analysis downloads `ProsusAI/finbert` from Hugging Face and caches it;
subsequent analyses reuse the local model. Override the defaults with
`NEWS_FINBERT_MODEL` and `NEWS_SPACY_MODEL` if required.

Available endpoints are `GET /health`, `GET /suggestions`, `GET /daily-report`, `POST /analyze`, `POST /backtest`,
`POST /papertrade`, `POST /outcomes`, and `GET /portfolio`. Request bodies for the first two are
`{"symbol": "RELIANCE"}`; paper trading also accepts `side` and optional
`quantity`.

`MARKET_DATA_SOURCE=cache` is available only as an explicit offline fallback
for development; it is not the default.

The local instrument file is used only to map a symbol such as `RELIANCE` to
Kite's instrument token. Candle/price data used in analysis comes from Kite on
each request.

This project is for research and paper trading. It does not constitute
investment advice.

## Daily recommendation report

`daily-report` is the end-to-end output: it scans the configured universe,
risk-reviews the top 20 ranked candidates, generates entry/stop/target/position
size details, enriches finalists with live option-chain intelligence when Kite
is enabled, and prints a market summary. Add `--json` for the equivalent
machine-readable report. It never submits live orders.

For live Kite reports, Google News RSS is collected only for shortlisted
stocks. FinBERT sentiment adjusts the unified score and estimated probability,
and the report includes source headlines plus spaCy entities. The system does
not fall back to hard-coded positive/negative keyword lists when local models
are unavailable. Cache mode intentionally does not fetch external news.

The reported probability is a documented heuristic until it has been
calibrated with recorded out-of-sample trade outcomes; it is not a promise of
performance.

Each daily recommendation receives a `recommendation_id`. After closing the
paper trade, record the result with `main.py record-outcome ID WIN` (optionally
add `--return-percent`, `--exit-price`, `--mfe-percent`, and `--mae-percent`).
The outcome store also records the predicted probability, entry, stop, targets,
readiness, expected value, probability error, and Brier score. Once at least 20 completed outcomes exist for a
strategy, the report blends its observed win rate into the estimated
probability; 200 outcomes is the report's validation milestone.
The daily report risk-reviews the top 20 ranked stocks by default, independently
of the final trade `--limit`. Override this with `RANKING_SHORTLIST_SIZE` (1–30).
Equity setups use confidence-aware reward/risk floors: A-grade 1.5
(`EQUITY_MIN_RISK_REWARD`), B-grade 1.3
(`EQUITY_B_GRADE_MIN_RISK_REWARD`), and watchlist/C-grade 1.2
(`EQUITY_WATCHLIST_MIN_RISK_REWARD`).
