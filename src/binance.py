"""Binance public API client — spot, futures, derivatives. No auth needed."""

import requests
from typing import Optional
from datetime import datetime
from bisect import bisect_left
import numpy as np
import time as _time

BINANCE_FAPI = "https://fapi.binance.com"
BINANCE_SPOT = "https://api.binance.com"
BINANCE_FUTURES_DATA = "https://fapi.binance.com/futures/data"
_last_call = 0.0
_MIN_DELAY = 0.05  # 20 req/s — well within Binance limits


def _rate_limit():
    global _last_call
    elapsed = _time.time() - _last_call
    if elapsed < _MIN_DELAY:
        _time.sleep(_MIN_DELAY - elapsed)
    _last_call = _time.time()


def _get(url: str, params: dict | None = None, retries: int = 3) -> dict | list:
    _rate_limit()
    for attempt in range(retries):
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 429:
            _time.sleep(min(2 ** attempt, 10))
            continue
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code != 429:
            raise Exception(f"HTTP {resp.status_code}: {resp.text[:200]}")
    raise Exception("Rate limited after retries")


def get_taker_ratio_history(symbol: str, period: str = "1h", limit: int = 500) -> list[dict]:
    """
    Get taker buy/sell volume ratio history.
    symbol: e.g. 'WLDUSDT'
    period: 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d
    Returns [{buySellRatio, sellVol, buyVol, timestamp}, ...]
    """
    data = _get(f"{BINANCE_FAPI}/futures/data/takerlongshortRatio", {
        "symbol": symbol.upper(),
        "period": period,
        "limit": limit,
    })
    return data if isinstance(data, list) else []


def get_order_book(symbol: str, limit: int = 100) -> dict:
    """Get spot order book depth. Returns {bids: [[price, qty], ...], asks: [[price, qty], ...]}."""
    return _get(f"{BINANCE_SPOT}/api/v3/depth", {
        "symbol": symbol.upper(),
        "limit": limit,
    })


def get_24h_ticker(symbol: str) -> dict:
    """Get 24h price ticker with bid/ask."""
    return _get(f"{BINANCE_SPOT}/api/v3/ticker/24hr", {
        "symbol": symbol.upper(),
    })


def get_24h_tickers() -> list[dict]:
    """Get all 24h tickers at once — much faster than individual calls."""
    return _get(f"{BINANCE_SPOT}/api/v3/ticker/24hr")


def get_open_interest(symbol: str) -> float | None:
    """Get current futures open interest."""
    data = _get(f"{BINANCE_FAPI}/fapi/v1/openInterest", {"symbol": symbol.upper()})
    if isinstance(data, dict):
        oi = data.get("openInterest")
        return float(oi) if oi else None
    return None


def compute_order_book_imbalance(depth: dict, levels: int = 10) -> float:
    """
    Compute bid dominance from order book depth.
    Returns 0–1: >0.5 = more bids (support), <0.5 = more asks (resistance).
    """
    bids = depth.get("bids", [])[:levels]
    asks = depth.get("asks", [])[:levels]
    bid_vol = sum(float(b[1]) for b in bids)
    ask_vol = sum(float(a[1]) for a in asks)
    total = bid_vol + ask_vol
    return bid_vol / total if total > 0 else 0.5


def get_binance_symbol(symbol: str) -> str:
    """Normalize to Binance USDT symbol. No-op for already-native symbols."""
    return symbol.replace("USD.A", "USDT").replace(".A", "").replace("_PERP", "")


# ---------------------------------------------------------------------------
# Spot universe
# ---------------------------------------------------------------------------

def get_spot_usdt_symbols() -> list[str]:
    """Return all USDT-quoted spot symbols from Binance exchangeInfo."""
    data = _get(f"{BINANCE_SPOT}/api/v3/exchangeInfo")
    symbols = []
    for s in (data.get("symbols", []) if isinstance(data, dict) else []):
        if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING":
            symbols.append(s["symbol"])
    return sorted(symbols)


# ---------------------------------------------------------------------------
# Funding rate (fapi)
# ---------------------------------------------------------------------------

def get_funding_rate(symbol: str) -> float | None:
    """Current funding rate for a USDT-margined perpetual."""
    data = _get(f"{BINANCE_FAPI}/fapi/v1/fundingRate", {
        "symbol": symbol.upper(),
        "limit": 1,
    })
    if isinstance(data, list) and data:
        rate = data[0].get("fundingRate")
        return float(rate) if rate else None
    return None


def get_funding_rate_history(symbol: str, limit: int = 500) -> list[dict]:
    """
    Paginated funding rate history — normalized to {t, c} format.
    c = funding rate (float), t = funding time (unix seconds).
    """
    data = _get(f"{BINANCE_FAPI}/fapi/v1/fundingRate", {
        "symbol": symbol.upper(),
        "limit": min(limit, 1000),
    })
    if not isinstance(data, list):
        return []
    candles = []
    for entry in data:
        ts = entry.get("fundingTime")
        rate = entry.get("fundingRate")
        if ts and rate:
            candles.append({"t": int(ts) // 1000, "c": float(rate)})
    return candles


def get_bulk_funding_rates(symbols: list[str]) -> dict[str, float]:
    """Batch current funding rates using fapi/v1/premiumIndex (lighter than fundingRate)."""
    result = {}
    try:
        data = _get(f"{BINANCE_FAPI}/fapi/v1/premiumIndex")
        if isinstance(data, list):
            sym_set = set(s.upper() for s in symbols)
            for entry in data:
                sym = entry.get("symbol", "")
                if sym in sym_set:
                    rate = entry.get("lastFundingRate")
                    if rate:
                        result[sym] = float(rate)
    except Exception:
        pass
    return result


# ---------------------------------------------------------------------------
# Open interest (fapi + futures/data)
# ---------------------------------------------------------------------------

def get_open_interest(symbol: str) -> float | None:
    """Current futures open interest in USDT."""
    data = _get(f"{BINANCE_FAPI}/fapi/v1/openInterest", {"symbol": symbol.upper()})
    if isinstance(data, dict):
        oi = data.get("openInterest")
        return float(oi) if oi else None
    return None


def get_open_interest_history(symbol: str, period: str = "4h",
                               limit: int = 500) -> list[dict]:
    """
    OI history — normalized to {t, c} format.
    c = OI in USDT, t = timestamp (unix seconds). ~1 month of data.
    """
    data = _get(f"{BINANCE_FUTURES_DATA}/openInterestHist", {
        "symbol": symbol.upper(),
        "period": period,
        "limit": min(limit, 500),
    })
    if not isinstance(data, list):
        return []
    candles = []
    for entry in data:
        ts = entry.get("timestamp")
        oi = entry.get("sumOpenInterestValue") or entry.get("sumOpenInterest")
        if ts and oi:
            candles.append({"t": int(ts) // 1000, "c": float(oi)})
    return candles


# ---------------------------------------------------------------------------
# Long/short ratio (futures/data)
# ---------------------------------------------------------------------------

def get_global_ls_ratio_history(symbol: str, period: str = "4h",
                                 limit: int = 500) -> list[dict]:
    """
    Global long/short account ratio — normalized to {t, r} format.
    r = longShortRatio, t = timestamp (unix seconds). ~30 days.
    """
    data = _get(f"{BINANCE_FUTURES_DATA}/globalLongShortAccountRatio", {
        "symbol": symbol.upper(),
        "period": period,
        "limit": min(limit, 500),
    })
    if not isinstance(data, list):
        return []
    candles = []
    for entry in data:
        ts = entry.get("timestamp")
        ratio = entry.get("longShortRatio")
        if ts and ratio:
            candles.append({"t": int(ts) // 1000, "r": float(ratio)})
    return candles


# ---------------------------------------------------------------------------
# Klines / OHLCV (spot or futures)
# ---------------------------------------------------------------------------

def get_klines(symbol: str, interval: str = "4h", limit: int = 500,
               market: str = "spot") -> list[dict]:
    """
    OHLCV klines from spot or futures.
    Returns [{t, o, h, l, c, v, ...}, ...].
    """
    base = BINANCE_SPOT if market == "spot" else BINANCE_FAPI
    endpoint = "/api/v3/klines" if market == "spot" else "/fapi/v1/klines"
    raw = _get(f"{base}{endpoint}", {
        "symbol": symbol.upper(),
        "interval": interval,
        "limit": min(limit, 1000),
    })
    if not isinstance(raw, list):
        return []
    # Binance klines are arrays; convert to dicts for compatibility
    keys = ["t", "o", "h", "l", "c", "v", "T", "q", "n", "V", "Q", "B"]
    candles = []
    for row in raw:
        candle = {}
        for i, k in enumerate(keys):
            if i < len(row):
                val = row[i]
                if k == "t" or k == "T":
                    candle[k] = int(float(val)) // 1000  # Binance returns ms
                elif k == "n":
                    candle[k] = int(float(val))
                else:
                    candle[k] = float(val) if val else 0.0
        candles.append(candle)
    return candles


class TakerHistory:
    """Pre-sorted taker ratio history for fast percentile lookups."""
    def __init__(self, candles: list[dict]):
        pairs = [(c["timestamp"], float(c["buySellRatio"])) for c in candles
                 if c.get("timestamp") and c.get("buySellRatio")]
        pairs.sort(key=lambda x: x[0])
        self.ts = np.array([p[0] for p in pairs], dtype=np.int64)
        self.vals = np.array([p[1] for p in pairs], dtype=np.float64)

    def __len__(self): return len(self.ts)

    def at(self, dt: datetime, window_ms: int = 3600_000) -> Optional[float]:
        """Value closest to dt within window (milliseconds)."""
        target = int(dt.timestamp() * 1000)
        idx = bisect_left(self.ts, target)
        best, best_diff = None, window_ms + 1
        for i in (idx, idx - 1, idx + 1):
            if 0 <= i < len(self.ts):
                diff = abs(int(self.ts[i]) - target)
                if diff < best_diff:
                    best_diff = diff
                    best = float(self.vals[i])
        return best if best_diff <= window_ms else None

    def percentile(self, value: float, before_dt: datetime, lookback_ms: int) -> Optional[float]:
        """Percentile of value within lookback before before_dt (timestamps in ms)."""
        cutoff = int(before_dt.timestamp() * 1000) - lookback_ms
        target = int(before_dt.timestamp() * 1000)
        past = self.vals[(self.ts >= cutoff) & (self.ts < target)]
        if len(past) == 0:
            return None
        return (np.sum(past <= value) / len(past)) * 100
