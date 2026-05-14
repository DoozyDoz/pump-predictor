"""Token universe management — Binance spot USDT pairs ranked by volume."""

from datetime import datetime
from src.config import UNIVERSE_SIZE, MIN_DAILY_VOLUME_USD
from src.db import db_session
from src.binance import get_spot_usdt_symbols, get_24h_tickers


def refresh_universe() -> list[str]:
    """
    Fetch all USDT spot pairs, rank by 24h quote volume via ticker,
    return top UNIVERSE_SIZE with >= MIN_DAILY_VOLUME_USD volume.
    """
    try:
        all_symbols = get_spot_usdt_symbols()
    except Exception as e:
        print(f"Binance exchangeInfo failed: {e}")
        return _fallback_universe()

    if not all_symbols:
        return _fallback_universe()

    # Rank by 24h quote volume from ticker (single API call)
    symbol_set = set(all_symbols)
    ranked = []
    try:
        tickers = get_24h_tickers()
        for t in tickers:
            sym = t.get("symbol", "")
            if sym not in symbol_set:
                continue
            vol = float(t.get("quoteVolume", 0) or 0)
            if vol >= MIN_DAILY_VOLUME_USD:
                ranked.append((sym, vol))
    except Exception:
        pass

    ranked.sort(key=lambda x: x[1], reverse=True)
    symbols = ranked[:UNIVERSE_SIZE]
    symbols = [s[0] for s in symbols]

    if len(symbols) < UNIVERSE_SIZE:
        # Fill remaining with any missing USDT pairs (no volume filter)
        for sym in all_symbols:
            if sym not in symbols and len(symbols) < UNIVERSE_SIZE:
                symbols.append(sym)

    _persist_universe(symbols)
    print(f"Universe refreshed: {len(symbols)} tokens")
    return symbols


def daily_volume_check(symbols: list[str]) -> list[str]:
    """Filter out tokens below MIN_DAILY_VOLUME_USD using 24h ticker."""
    try:
        tickers = {t["symbol"]: t for t in get_24h_tickers()}
    except Exception:
        print("Volume check: ticker fetch failed — passing all")
        return symbols

    passed = []
    for sym in symbols:
        t = tickers.get(sym)
        if t is None:
            passed.append(sym)
            continue
        try:
            vol = float(t.get("quoteVolume", 0) or 0)
        except (ValueError, TypeError):
            passed.append(sym)
            continue
        ok = vol >= MIN_DAILY_VOLUME_USD
        if ok:
            passed.append(sym)
        _update_volume_check(sym, vol, ok)
    return passed


def _persist_universe(symbols: list[str]):
    with db_session() as conn:
        now = datetime.utcnow().isoformat()
        for sym in symbols:
            conn.execute(
                "INSERT OR IGNORE INTO tokens (symbol, exchange, market, added_at) "
                "VALUES (?, 'B', 'spot', ?)",
                (sym, now),
            )
        conn.execute(
            "UPDATE tokens SET in_universe = FALSE WHERE exchange = 'B' AND market = 'spot'"
        )
        placeholders = ",".join("?" * len(symbols))
        conn.execute(
            f"UPDATE tokens SET in_universe = TRUE WHERE exchange = 'B' AND market = 'spot' "
            f"AND symbol IN ({placeholders})",
            symbols,
        )


def _update_volume_check(symbol: str, volume: float, ok: bool):
    with db_session() as conn:
        conn.execute(
            "UPDATE tokens SET last_volume_check = ?, last_volume_ok = ? "
            "WHERE symbol = ? AND exchange = 'B'",
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
