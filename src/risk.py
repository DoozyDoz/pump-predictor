"""
ATR-based risk model for position sizing and stop-loss calculation.

Used in Phase 3 (entry) to size positions based on volatility.
"""

from typing import Optional

from src.config import ATR_PERIOD, ATR_STOP_MULTIPLIER, ATR_RISK_PER_TRADE_PCT, POSITION_SIZE_PCT


def compute_atr(symbol: str, period: int = ATR_PERIOD, interval: str = "1h") -> Optional[float]:
    """
    Fetch klines and compute Average True Range.
    Returns ATR as a percentage of current price, or None if data unavailable.
    """
    try:
        from src.binance import get_klines

        candles = get_klines(symbol, interval=interval, limit=period * 2, market="spot")
    except Exception:
        return None

    if not candles or len(candles) < period + 1:
        return None

    # Compute True Range for each candle
    tr_values = []
    prev_close = None
    for c in candles:
        high = c.get("h", 0)
        low = c.get("l", 0)
        close = c.get("c", 0)
        if not high or not low or not close:
            continue
        if prev_close is None:
            tr_values.append(high - low)
        else:
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            tr_values.append(tr)
        prev_close = close

    if len(tr_values) < period:
        return None

    # Simple moving average of TR
    atr = sum(tr_values[-period:]) / period

    current_price = candles[-1].get("c", 0)
    if current_price <= 0:
        return None

    return (atr / current_price) * 100


def position_size(
    atr_pct: float,
    portfolio_usd: float,
    risk_per_trade_pct: float = ATR_RISK_PER_TRADE_PCT,
) -> float:
    """
    Position size based on ATR volatility.

    Position size = (portfolio_usd * risk_per_trade_pct) / (atr_stop_multiplier * atr_pct / 100)
    Smaller ATR = larger position, but risk is capped.

    Result is capped at POSITION_SIZE_PCT * portfolio_usd.
    """
    if atr_pct <= 0:
        return portfolio_usd * POSITION_SIZE_PCT

    raw_size = (portfolio_usd * risk_per_trade_pct) / (ATR_STOP_MULTIPLIER * atr_pct / 100)
    max_size = portfolio_usd * POSITION_SIZE_PCT
    return min(raw_size, max_size)


def stop_loss_pct(atr_pct: float, atr_multiplier: float = ATR_STOP_MULTIPLIER) -> float:
    """
    Return stop loss as a negative percentage of entry price.
    e.g., ATR = 3%, multiplier = 2.0 -> stop_loss = -6%
    """
    return -(atr_multiplier * atr_pct)
