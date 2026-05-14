"""
CoinGlass API v4 client — historical derivatives data for backtesting.

Endpoints (all return OHLCV-style data):
  /futures/funding-rate/history            → {time, open, high, low, close}
  /futures/open-interest/history           → {time, open, high, low, close}
  /futures/global-long-short-account-ratio/history
    → {time, global_account_long_short_ratio, ...}
"""

import time as _time
from datetime import datetime, timedelta
import requests
from src.config import COINGLASS_API_KEY

BASE = "https://open-api-v4.coinglass.com/api"
_MIN_DELAY = 2.0  # Hobbyist: 30 req/min → ~2s between calls
_last_call = 0.0


def _rate_limit():
    global _last_call
    elapsed = _time.time() - _last_call
    if elapsed < _MIN_DELAY:
        _time.sleep(_MIN_DELAY - elapsed)
    _last_call = _time.time()


def _get(endpoint: str, params: dict) -> list[dict]:
    """Call CoinGlass API, return data list or raise on error."""
    _rate_limit()
    headers = {"CG-API-KEY": COINGLASS_API_KEY}
    for attempt in range(3):
        resp = requests.get(f"{BASE}{endpoint}", headers=headers,
                            params=params, timeout=30)
        data = resp.json() if resp.text else {}
        if data.get("code") == "0":
            return data.get("data", [])
        if data.get("code") == "401":
            msg = data.get("msg", "")
            if "Upgrade" in msg:
                raise RuntimeError(f"CoinGlass plan does not include {endpoint}")
            if "key" in msg.lower():
                raise RuntimeError("CoinGlass API key invalid")
        if resp.status_code == 429:
            _time.sleep(min(2 ** attempt, 10))
            continue
        raise RuntimeError(f"CoinGlass {endpoint}: {data.get('msg', resp.text[:100])}")
    raise RuntimeError(f"CoinGlass {endpoint}: rate limited after retries")


# ---------------------------------------------------------------------------
# Public API — returns normalized {t, c/r} format matching Binance module
# ---------------------------------------------------------------------------

def get_funding_history(symbol: str, exchange: str = "Binance",
                        interval: str = "4h", months: int = 12) -> list[dict]:
    """
    Fetch funding rate history from CoinGlass.
    Returns [{t, c}, ...] where c = funding rate (close of OHLC candle).
    """
    candles = _paginate("/futures/funding-rate/history",
                        symbol, exchange, interval, months)
    return [{"t": int(c["time"]) // 1000, "c": float(c["close"])}
            for c in candles if c.get("close") is not None]


def get_open_interest_history(symbol: str, exchange: str = "Binance",
                               interval: str = "4h", months: int = 12) -> list[dict]:
    """
    Fetch OI history from CoinGlass.
    Returns [{t, c}, ...] where c = OI in USDT (close of OHLC candle).
    """
    candles = _paginate("/futures/open-interest/history",
                        symbol, exchange, interval, months)
    return [{"t": int(c["time"]) // 1000, "c": float(c["close"])}
            for c in candles if c.get("close") is not None]


def get_ls_ratio_history(symbol: str, exchange: str = "Binance",
                          interval: str = "4h", months: int = 12) -> list[dict]:
    """
    Fetch global long/short account ratio history from CoinGlass.
    Returns [{t, r}, ...] where r = longShortRatio.
    """
    candles = _paginate("/futures/global-long-short-account-ratio/history",
                        symbol, exchange, interval, months)
    result = []
    for c in candles:
        ratio = c.get("global_account_long_short_ratio")
        if ratio is not None and c.get("time"):
            result.append({"t": int(c["time"]) // 1000, "r": float(ratio)})
    return result


# ---------------------------------------------------------------------------
# Pagination helper
# ---------------------------------------------------------------------------

def _paginate(endpoint: str, symbol: str, exchange: str,
              interval: str, months: int) -> list[dict]:
    """Fetch all data across multiple pages using startTime/endTime."""
    end_ms = int(datetime.utcnow().timestamp() * 1000)
    start_ms = int((datetime.utcnow() - timedelta(days=months * 30)).timestamp() * 1000)

    all_data = []
    while start_ms < end_ms:
        params = {
            "symbol": symbol.upper(),
            "exchange": exchange,
            "interval": interval,
            "startTime": start_ms,
            "limit": 1000,
        }
        page = _get(endpoint, params)
        if not page:
            break
        all_data.extend(page)
        # Advance past the last candle
        last_time = max(int(c.get("time", 0)) for c in page)
        if last_time <= start_ms:
            break  # no progress
        start_ms = last_time + 1

    return all_data
