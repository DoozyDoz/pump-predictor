"""
Signal snapshot storage — builds local history for percentile calculations.

Binance OI/LS history is limited to ~30 days. By storing one snapshot per token
per signal type per day, we accumulate a growing local history that eventually
replaces the need for deep Binance history queries.
"""

from datetime import datetime, timedelta
from src.db import db_session


SIGNAL_TYPES = ["funding_rate", "oi_value", "ls_ratio", "taker_ratio"]


def store_snapshots(snapshots: list[dict]):
    """
    Insert today's signal values. One row per symbol per signal_type.
    Deduplicates by (symbol, signal_type, snapshot_ts).

    snapshots: [{"symbol": "BTCUSDT", "signal_type": "funding_rate",
                  "value": -0.000123, "snapshot_ts": "2026-05-14"}, ...]
    """
    if not snapshots:
        return
    with db_session() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO signal_snapshots (symbol, signal_type, value, snapshot_ts) "
            "VALUES (:symbol, :signal_type, :value, :snapshot_ts)",
            snapshots,
        )
    print(f"Snapshots: stored {len(snapshots)} values")


def get_snapshot_history(symbol: str, signal_type: str,
                         since_days: int = 90) -> list[dict]:
    """
    Query local snapshots for a symbol + signal_type.
    Returns [{t, c/r}] format matching Binance normalized history.

    signal_type: 'funding_rate' → key='c'
                 'oi_value' → key='c'
                 'ls_ratio' → key='r'
                 'taker_ratio' → key='c'
    """
    key = "r" if signal_type == "ls_ratio" else "c"
    cutoff = (datetime.utcnow() - timedelta(days=since_days)).strftime("%Y-%m-%d")

    with db_session() as conn:
        rows = conn.execute(
            "SELECT value, snapshot_ts FROM signal_snapshots "
            "WHERE symbol = ? AND signal_type = ? AND snapshot_ts >= ? "
            "ORDER BY snapshot_ts ASC",
            (symbol, signal_type, cutoff),
        ).fetchall()

    candles = []
    for row in rows:
        try:
            dt = datetime.strptime(row["snapshot_ts"], "%Y-%m-%d")
            ts = int(dt.timestamp())
        except ValueError:
            ts = 0
        candles.append({"t": ts, key: row["value"]})
    return candles


def snapshot_count() -> dict[str, int]:
    """Return counts per signal type for monitoring."""
    with db_session() as conn:
        rows = conn.execute(
            "SELECT signal_type, COUNT(*) as cnt FROM signal_snapshots GROUP BY signal_type"
        ).fetchall()
    return {row["signal_type"]: row["cnt"] for row in rows}
