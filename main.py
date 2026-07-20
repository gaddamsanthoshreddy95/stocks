import argparse
import json
import logging

from src.application.errors import PlatformError
from src.application.platform import TradingPlatform
from src.presenter.daily_report import DailyReportPresenter


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="AI Quantitative Trading Platform")
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
    platform = TradingPlatform()
    try:
        if args.command == "analyze":
            result = platform.analyze(args.symbol)
        elif args.command == "backtest":
            result = platform.backtest(args.symbol)
        elif args.command == "suggest":
            result = platform.suggest_stocks(args.limit, args.minimum_score)
        elif args.command == "daily-report":
            result = platform.daily_report(args.limit, args.minimum_score)
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
        print(DailyReportPresenter.render(result))
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":

    main()
