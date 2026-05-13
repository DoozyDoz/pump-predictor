"""Token universe management — top N spot tokens by some ranking metric."""

from datetime import datetime, timedelta
from src.config import UNIVERSE_SIZE, MIN_DAILY_VOLUME_USD
from src.db import db_session
from src.coinalyze import get_spot_symbols, get_ohlcv_history, CoinAnalyzeError


def refresh_universe() -> list[str]:
    """
    Fetch all USDT spot symbols, rank by recent volume, return top UNIVERSE_SIZE
    with >= MIN_DAILY_VOLUME_USD daily volume.
    Returns spot symbols (e.g. BTCUSD.A).
    """
    try:
        all_symbols = get_spot_symbols()
    except CoinAnalyzeError as e:
        print(f"CoinAnalyze spot-markets failed: {e}")
        return _fallback_universe()

    if not all_symbols:
        return _fallback_universe()

    ranked = _rank_by_volume(all_symbols)
    symbols = ranked[:UNIVERSE_SIZE]

    _persist_universe(symbols)
    print(f"Universe refreshed: {len(symbols)} tokens")
    return symbols


def daily_volume_check(symbols: list[str]) -> list[str]:
    """Filter out tokens below MIN_DAILY_VOLUME_USD using recent daily OHLCV."""
    passed = []
    to_dt = datetime.utcnow()
    from_dt = to_dt - timedelta(days=3)
    for sym in symbols:
        try:
            ohlcv = get_ohlcv_history(sym, from_dt=from_dt, to_dt=to_dt, interval="daily")
        except CoinAnalyzeError:
            passed.append(sym)
            continue
        if ohlcv:
            volumes = [c.get("v", 0) for c in ohlcv if c.get("v")]
            avg_vol = sum(volumes) / len(volumes) if volumes else 0
            if avg_vol >= MIN_DAILY_VOLUME_USD:
                passed.append(sym)
                _update_volume_check(sym, avg_vol, True)
            else:
                _update_volume_check(sym, avg_vol, False)
        else:
            passed.append(sym)
    return passed


def _rank_by_volume(symbols: list[str]) -> list[str]:
    """
    Fetch recent daily volume for symbols, rank by volume descending.
    Queries batches to avoid excessive API calls.
    """
    results = []
    to_dt = datetime.utcnow()
    from_dt = to_dt - timedelta(days=2)
    for sym in symbols:
        try:
            candles = get_ohlcv_history(sym, from_dt=from_dt, to_dt=to_dt, interval="daily")
        except CoinAnalyzeError:
            continue
        if candles:
            avg_vol = sum(c.get("v", 0) for c in candles) / len(candles)
            if avg_vol >= MIN_DAILY_VOLUME_USD:
                results.append((sym, avg_vol))
        if len(results) >= UNIVERSE_SIZE:
            break
    results.sort(key=lambda x: x[1], reverse=True)
    return [r[0] for r in results]


def _persist_universe(symbols: list[str]):
    with db_session() as conn:
        now = datetime.utcnow().isoformat()
        for sym in symbols:
            conn.execute(
                "INSERT OR IGNORE INTO tokens (symbol, exchange, market, added_at) VALUES (?, 'A', 'spot', ?)",
                (sym, now),
            )
        conn.execute(
            "UPDATE tokens SET in_universe = FALSE WHERE exchange = 'A' AND market = 'spot'"
        )
        placeholders = ",".join("?" * len(symbols))
        conn.execute(
            f"UPDATE tokens SET in_universe = TRUE WHERE exchange = 'A' AND market = 'spot' AND symbol IN ({placeholders})",
            symbols,
        )


def _update_volume_check(symbol: str, volume: float, ok: bool):
    with db_session() as conn:
        conn.execute(
            "UPDATE tokens SET last_volume_check = ?, last_volume_ok = ? WHERE symbol = ? AND exchange = 'A'",
            (volume, ok, symbol),
        )


def _fallback_universe() -> list[str]:
    with db_session() as conn:
        rows = conn.execute(
            "SELECT symbol FROM tokens WHERE in_universe = TRUE AND market = 'spot' ORDER BY id"
        ).fetchall()
    if rows:
        print(f"Using fallback universe: {len(rows)} tokens from DB")
        return [r[0] for r in rows]
    raise RuntimeError("No universe in DB and API call failed — cannot proceed")
