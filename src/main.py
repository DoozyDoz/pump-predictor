"""Entry point for pump prediction pipeline."""

import argparse
from src.db import init_db
from src.pipeline import run_daily
from src.backtest import run_backtest, print_summary
from src.universe import refresh_universe


def cmd_init(_args):
    init_db()
    print("Database initialized.")


def cmd_universe(_args):
    symbols = refresh_universe()
    print(f"\nUniverse ({len(symbols)} tokens):")
    for s in symbols:
        print(f"  {s}")


def cmd_daily(args):
    run_daily(portfolio_usd=args.portfolio)


def cmd_backtest(args):
    from src.db import db_session
    with db_session() as conn:
        rows = conn.execute(
            "SELECT symbol FROM tokens WHERE in_universe = TRUE AND market = 'spot' ORDER BY id"
        ).fetchall()
    symbols = [r[0] for r in rows] if rows else refresh_universe()
    if args.limit:
        symbols = symbols[:args.limit]
    print(f"Running backtest on {len(symbols)} symbols...")
    results = run_backtest(symbols)
    print_summary(results)


def main():
    parser = argparse.ArgumentParser(description="Crypto Pump Prediction")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="Initialize database")

    sub.add_parser("universe", help="Refresh token universe")

    p = sub.add_parser("daily", help="Run daily batch pipeline")
    p.add_argument("--portfolio", type=float, default=1000.0, help="Portfolio size in USD")

    p = sub.add_parser("backtest", help="Run funding-rate backtest")
    p.add_argument("--limit", type=int, default=0, help="Limit to N tokens (faster test)")

    args = parser.parse_args()

    if args.command == "init":
        cmd_init(args)
    elif args.command == "universe":
        cmd_universe(args)
    elif args.command == "daily":
        cmd_daily(args)
    elif args.command == "backtest":
        cmd_backtest(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
