# Prepared scanner performance

The prior production run took approximately 24 minutes. It did all company/news research and backtesting in the foreground. That production run is not directly comparable with the controlled benchmark below.

Benchmark command: `.venv/bin/python scripts/benchmark_futures_scan.py`.

| Measurement | Result |
|---|---:|
| Reference scan, 12 identical simulated symbols, 5 actual backtests | 34.77 seconds |
| Prepared live scan, same 12 symbols | 3.82 seconds |
| Matched ranked decisions and historical rates | Yes |
| Relative speedup in this controlled comparison | 9.09× |
| Full 214-symbol warm scan, LONG and SHORT plus margin/execution checks | 25.40 seconds |
| Full 214-symbol incremental scan | 99.97 seconds |
| Live backtests | 0 |

The full-universe runs used default margin validation and underlying-movement settings. The small reference comparison used the legacy futures-movement/no-required-margin settings on both paths to isolate the refactor. Both full-universe runs fetched batched quotes and read-only margin estimates. The incremental run made 219 historical requests, paced at 0.35 seconds or slower. Feeds used deterministic candles, warm news, complete cached research and simulated 2ms API latency.

These are simulated-feed measurements of real scoring, backtesting and cache code. They do not establish real Kite latency, market-session trading readiness or guaranteed execution time. Unavailable/new unchecked news, stale data or missing preparation/margin sources still prevent approval. See `futures_scan_benchmark.json` for per-stage measurements and request counts.
