"""Entry point for pump prediction pipeline."""

import argparse
from datetime import datetime
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
            "SELECT symbol FROM tokens WHERE in_universe = TRUE AND market = 'spot' "
            "AND exchange = 'B' ORDER BY id"
        ).fetchall()
    symbols = [r[0] for r in rows] if rows else refresh_universe()
    if args.limit:
        symbols = symbols[:args.limit]
    print(f"Running backtest on {len(symbols)} symbols...")
    results = run_backtest(symbols)
    print_summary(results)


def cmd_import_coinglass(args):
    """Import historical derivatives data from CoinGlass into signal_snapshots."""
    init_db()  # ensure signal_snapshots table exists
    from src.db import db_session
    from src.snapshots import store_snapshots
    from src.coinglass import (
        get_funding_history, get_open_interest_history, get_ls_ratio_history,
    )

    with db_session() as conn:
        rows = conn.execute(
            "SELECT symbol FROM tokens WHERE in_universe = TRUE AND market = 'spot' "
            "AND exchange = 'B' ORDER BY id"
        ).fetchall()
    symbols = [r[0] for r in rows] if rows else refresh_universe()
    if args.limit:
        symbols = symbols[:args.limit]

    months = args.months
    interval = args.interval
    total = len(symbols)
    print(f"Importing {months}mo of CoinGlass data for {total} symbols "
          f"(interval={interval})...")

    snapshots = []
    for i, sym in enumerate(symbols):
        pct = (i + 1) / total * 100
        added = 0
        try:
            # Funding rate history
            data = get_funding_history(sym, months=months, interval=interval)
            for c in data:
                ts = datetime.fromtimestamp(c["t"], datetime.UTC).strftime("%Y-%m-%d")
                snapshots.append({"symbol": sym, "signal_type": "funding_rate",
                                  "value": c["c"], "snapshot_ts": ts})
                added += 1
        except Exception as e:
            print(f"  {sym} funding: {e}")

        try:
            # Open interest history
            data = get_open_interest_history(sym, months=months, interval=interval)
            for c in data:
                ts = datetime.fromtimestamp(c["t"], datetime.UTC).strftime("%Y-%m-%d")
                snapshots.append({"symbol": sym, "signal_type": "oi_value",
                                  "value": c["c"], "snapshot_ts": ts})
                added += 1
        except Exception as e:
            print(f"  {sym} OI: {e}")

        try:
            # LS ratio history
            data = get_ls_ratio_history(sym, months=months, interval=interval)
            for c in data:
                ts = datetime.fromtimestamp(c["t"], datetime.UTC).strftime("%Y-%m-%d")
                snapshots.append({"symbol": sym, "signal_type": "ls_ratio",
                                  "value": c["r"], "snapshot_ts": ts})
                added += 1
        except Exception as e:
            print(f"  {sym} LS: {e}")

        print(f"  [{pct:.0f}%] {sym}: {added} snapshots")

        # Flush every 10 symbols to avoid memory buildup
        if len(snapshots) >= 50000:
            store_snapshots(snapshots)
            snapshots = []

    if snapshots:
        store_snapshots(snapshots)

    from src.snapshots import snapshot_count
    print(f"\nImport complete. Snapshot counts: {snapshot_count()}")


def main():
    parser = argparse.ArgumentParser(description="Crypto Pump Prediction")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="Initialize database")

    sub.add_parser("universe", help="Refresh token universe")

    p = sub.add_parser("daily", help="Run daily batch pipeline")
    p.add_argument("--portfolio", type=float, default=1000.0, help="Portfolio size in USD")

    p = sub.add_parser("backtest", help="Run funding-rate backtest")
    p.add_argument("--limit", type=int, default=0, help="Limit to N tokens (faster test)")

    p = sub.add_parser("import-coinglass", help="Import CoinGlass history into signal_snapshots")
    p.add_argument("--limit", type=int, default=0, help="Limit to N tokens")
    p.add_argument("--months", type=int, default=12, help="Months of history")
    p.add_argument("--interval", type=str, default="1d", help="Candle interval (1d, 4h, 1h)")

    args = parser.parse_args()

    if args.command == "init":
        cmd_init(args)
    elif args.command == "universe":
        cmd_universe(args)
    elif args.command == "daily":
        cmd_daily(args)
    elif args.command == "backtest":
        cmd_backtest(args)
    elif args.command == "import-coinglass":
        cmd_import_coinglass(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
