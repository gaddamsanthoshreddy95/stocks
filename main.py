import argparse
import json
import logging
from pathlib import Path

from src.application.errors import PlatformError
from src.application.platform import TradingPlatform
from src.presenter.daily_report import DailyReportPresenter


LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def _configure_logging(report_log: str | None = None) -> Path | None:
    """Configure console logging and replace the prior daily-report log."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    log_path = None
    if report_log:
        log_path = Path(report_log).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if log_path.exists():
            if not log_path.is_file():
                raise ValueError(f"Log path is not a file: {log_path}")
            log_path.unlink()
        handlers.append(logging.FileHandler(log_path, mode="w", encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, handlers=handlers, force=True)
    return log_path


def _append_report_output(log_path: Path | None, output: str) -> None:
    if log_path is not None:
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(output)
            stream.write("\n")


def main():
    parser = argparse.ArgumentParser(description="Alphatrace")
    subcommands = parser.add_subparsers(dest="command", required=True)
    for name in ("analyze", "backtest"):
        command = subcommands.add_parser(name)
        command.add_argument("symbol")
    suggest = subcommands.add_parser("suggest", help="rank the available cached stocks")
    suggest.add_argument("--limit", type=int, default=5)
    suggest.add_argument("--minimum-score", type=int, default=40)
    daily = subcommands.add_parser("daily-report", help="generate the final daily trading report")
    daily.add_argument("--limit", type=int, default=5, help="maximum final trades; top 20 are risk-reviewed")
    daily.add_argument("--minimum-score", type=int, default=40)
    daily.add_argument("--option-month", metavar="YYYY-MM",
                       help="restrict all option-chain lookup to this expiry month")
    daily.add_argument("--log-file", default="reports/daily_report.log",
                       help="fresh runtime and report log (default: %(default)s)")
    daily.add_argument("--json", action="store_true", help="emit machine-readable JSON instead of the CLI report")
    outcome = subcommands.add_parser("record-outcome", help="record a completed recommendation for calibration")
    outcome.add_argument("recommendation_id")
    outcome.add_argument("outcome", choices=("WIN", "LOSS"))
    outcome.add_argument("--return-percent", type=float)
    outcome.add_argument("--exit-price", type=float)
    outcome.add_argument("--mfe-percent", type=float)
    outcome.add_argument("--mae-percent", type=float)
    paper = subcommands.add_parser("papertrade")
    paper.add_argument("symbol")
    paper.add_argument("side", choices=("BUY", "SELL"))
    paper.add_argument("--quantity", type=int)
    subcommands.add_parser("portfolio")
    args = parser.parse_args()
    try:
        log_path = _configure_logging(args.log_file if args.command == "daily-report" else None)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    platform = TradingPlatform()
    try:
        if args.command == "analyze":
            result = platform.analyze(args.symbol)
        elif args.command == "backtest":
            result = platform.backtest(args.symbol)
        elif args.command == "suggest":
            result = platform.suggest_stocks(args.limit, args.minimum_score)
        elif args.command == "daily-report":
            result = platform.daily_report(args.limit, args.minimum_score, args.option_month)
        elif args.command == "record-outcome":
            result = platform.record_trade_outcome(
                args.recommendation_id, args.outcome == "WIN", args.return_percent,
                args.exit_price, args.mfe_percent, args.mae_percent,
            )
        elif args.command == "papertrade":
            result = platform.paper_trade(args.symbol, args.side, args.quantity)
        else:
            result = platform.portfolio()
    except PlatformError as exc:
        parser.error(str(exc))
    if args.command == "daily-report" and not args.json:
        output = DailyReportPresenter.render(result)
    else:
        output = json.dumps(result, indent=2, default=str)
    print(output)
    if args.command == "daily-report":
        _append_report_output(log_path, output)


if __name__ == "__main__":

    main()
