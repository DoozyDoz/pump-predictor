"""CoinAnalyze API client — wraps all /v1 endpoints."""

import requests
import time as _time
from typing import Optional
from datetime import datetime, timezone
from src.config import COINALYZE_API_KEY, COINALYZE_BASE, API_DELAY

BINANCE_EXCHANGE_CODE = "A"
PERP_SUFFIX = "_PERP"
_last_call = 0.0


class CoinAnalyzeError(Exception):
    pass


def _headers():
    return {"Authorization": f"Bearer {COINALYZE_API_KEY}"}


def _get(endpoint: str, params: dict | None = None, retries: int = 3) -> dict | list:
    global _last_call
    # Enforce minimum delay between calls
    elapsed = _time.time() - _last_call
    if elapsed < API_DELAY:
        _time.sleep(API_DELAY - elapsed)
    url = f"{COINALYZE_BASE}/{endpoint}"
    for attempt in range(retries):
        resp = requests.get(url, headers=_headers(), params=params, timeout=30)
        if resp.status_code == 429:
            wait = min(2 ** attempt, 10)
            _time.sleep(wait)
            continue
        if resp.status_code != 200:
            msg = resp.json().get("message", resp.text[:200]) if resp.text else resp.text[:200]
            raise CoinAnalyzeError(f"HTTP {resp.status_code}: {msg}")
        data = resp.json()
        _last_call = _time.time()
        if isinstance(data, dict) and data.get("error"):
            raise CoinAnalyzeError(str(data["error"]))
        return data
    raise CoinAnalyzeError("Rate limited after retries")


def _dt_to_ts(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def _now_ts() -> int:
    return _dt_to_ts(datetime.utcnow())


def _unwrap_history(data: list | dict, key: str = "history") -> list[dict]:
    """
    All history endpoints return [{symbol: ..., history: [...]}].
    Unwrap the inner list. Returns flat list of candle dicts.
    """
    if isinstance(data, list):
        all_candles = []
        for entry in data:
            if isinstance(entry, dict) and key in entry:
                all_candles.extend(entry[key])
        return all_candles
    if isinstance(data, dict) and key in data:
        return data[key]
    return []


def get_spot_symbols(exchange: str = BINANCE_EXCHANGE_CODE) -> list[str]:
    """Return all USDT-quoted spot symbols (e.g. BTCUSD.A, no _PERP suffix)."""
    markets = _get("spot-markets", {"exchange": exchange})
    if not isinstance(markets, list):
        return []
    return [
        m["symbol"]
        for m in markets
        if m.get("symbol")
        and m.get("exchange") == exchange
        and m.get("quote_asset") == "USDT"
    ]


def get_perp_symbols(exchange: str = BINANCE_EXCHANGE_CODE) -> list[str]:
    """Return all USDT-margined perpetual symbols (e.g. BTCUSDT_PERP.A)."""
    markets = _get("future-markets", {"exchange": exchange})
    if not isinstance(markets, list):
        return []
    return [
        m["symbol"]
        for m in markets
        if m.get("symbol")
        and m.get("exchange") == exchange
        and m.get("is_perpetual")
        and m.get("quote_asset") == "USDT"
        and m.get("margined") == "STABLE"
    ]


_spot_to_perp_cache: dict[str, str] | None = None


def _build_spot_to_perp_map(exchange: str = BINANCE_EXCHANGE_CODE) -> dict[str, str]:
    """Build {spot_symbol: perp_symbol} mapping from API metadata."""
    global _spot_to_perp_cache
    if _spot_to_perp_cache is not None:
        return _spot_to_perp_cache

    spot_markets = _get("spot-markets", {"exchange": exchange})
    future_markets = _get("future-markets", {"exchange": exchange})

    perp_map: dict[str, str] = {}
    for m in future_markets:
        if (isinstance(m, dict) and m.get("exchange") == exchange
                and m.get("is_perpetual") and m.get("margined") == "STABLE"
                and m.get("quote_asset") == "USDT"):
            perp_map[m.get("base_asset", "")] = m["symbol"]

    mapping: dict[str, str] = {}
    for m in spot_markets:
        if (isinstance(m, dict) and m.get("exchange") == exchange
                and m.get("quote_asset") == "USDT"):
            base = m.get("base_asset", "")
            sym = m["symbol"]
            if base in perp_map:
                mapping[sym] = perp_map[base]
            elif f"1000{base}" in perp_map:
                mapping[sym] = perp_map[f"1000{base}"]

    _spot_to_perp_cache = mapping
    return mapping


def spot_to_perp(spot_symbol: str) -> str:
    """
    Convert spot symbol (e.g. PEPEUSD.A) to perp symbol (e.g. 1000PEPEUSDT_PERP.A).
    Uses metadata-based mapping with fallback.
    """
    mapping = _build_spot_to_perp_map()
    if spot_symbol in mapping:
        return mapping[spot_symbol]
    base = spot_symbol.replace("USD.A", "").replace(".A", "")
    return f"{base}USDT_PERP.A"


def perp_to_spot(perp_symbol: str) -> str:
    """Convert perp symbol back to spot."""
    base = perp_symbol.replace("USDT_PERP.A", "").replace("_PERP.A", "")
    return f"{base}USD.A"


def get_funding_rate(symbol: str) -> float | None:
    """
    Get current funding rate for a _PERP symbol.
    Returns funding rate as float (e.g. 0.005822 = 0.5822%).
    """
    data = _get("funding-rate", {"symbols": symbol})
    if isinstance(data, list) and data:
        val = data[0].get("value")
        return float(val) if val is not None else None
    return None


def get_funding_rate_history(
    symbol: str,
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    interval: str = "4hour",
) -> list[dict]:
    """
    Get funding rate history for a _PERP symbol.
    Returns list of OHLC candles: {t, o, h, l, c}.
    """
    params = {
        "symbols": symbol,
        "interval": interval,
        "from": _dt_to_ts(from_dt) if from_dt else _now_ts() - 86400 * 90,
        "to": _dt_to_ts(to_dt) if to_dt else _now_ts(),
    }
    return _unwrap_history(_get("funding-rate-history", params))


def get_ohlcv_history(
    symbol: str,  # spot or perp symbol
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    interval: str = "4hour",
) -> list[dict]:
    """
    Get OHLCV history for a symbol.
    Returns list of candles: {t, o, h, l, c, v, bv, tx, btx}.
    """
    params = {
        "symbols": symbol,
        "interval": interval,
        "from": _dt_to_ts(from_dt) if from_dt else _now_ts() - 86400 * 90,
        "to": _dt_to_ts(to_dt) if to_dt else _now_ts(),
    }
    return _unwrap_history(_get("ohlcv-history", params))


def get_open_interest(symbol: str) -> float | None:
    """Get current OI for a _PERP symbol."""
    data = _get("open-interest", {"symbols": symbol})
    if isinstance(data, list) and data:
        val = data[0].get("value")
        return float(val) if val is not None else None
    return None


def get_open_interest_history(
    symbol: str,
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    interval: str = "4hour",
) -> list[dict]:
    params = {
        "symbols": symbol,
        "interval": interval,
        "from": _dt_to_ts(from_dt) if from_dt else _now_ts() - 86400 * 90,
        "to": _dt_to_ts(to_dt) if to_dt else _now_ts(),
    }
    return _unwrap_history(_get("open-interest-history", params))


def get_liquidation_history(
    symbol: str,
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
) -> list[dict]:
    params = {
        "symbols": symbol,
        "from": _dt_to_ts(from_dt) if from_dt else _now_ts() - 86400 * 90,
        "to": _dt_to_ts(to_dt) if to_dt else _now_ts(),
    }
    return _unwrap_history(_get("liquidation-history", params))


def get_long_short_ratio_history(
    symbol: str,
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    interval: str = "4hour",
) -> list[dict]:
    params = {
        "symbols": symbol,
        "interval": interval,
        "from": _dt_to_ts(from_dt) if from_dt else _now_ts() - 86400 * 90,
        "to": _dt_to_ts(to_dt) if to_dt else _now_ts(),
    }
    return _unwrap_history(_get("long-short-ratio-history", params))
