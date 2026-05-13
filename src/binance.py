"""Binance public API client — taker ratio, order book, ticker. No auth needed."""

import requests
from typing import Optional
from datetime import datetime
from bisect import bisect_left
import numpy as np
from src.config import API_DELAY
import time as _time

BINANCE_FAPI = "https://fapi.binance.com"
BINANCE_SPOT = "https://api.binance.com"
_last_call = 0.0


def _rate_limit():
    global _last_call
    elapsed = _time.time() - _last_call
    if elapsed < API_DELAY:
        _time.sleep(API_DELAY - elapsed)
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


def get_binance_symbol(coin_spot_symbol: str) -> str:
    """Convert CoinAnalyze spot symbol (PEPEUSD.A) to Binance symbol (PEPEUSDT)."""
    return coin_spot_symbol.replace("USD.A", "USDT").replace(".A", "")


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
