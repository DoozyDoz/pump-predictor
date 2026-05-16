"""
Market regime detection — gates watchlist promotions.

Checks BTC dominance trend, overall market volatility, and BTC price action
to determine whether altcoin signals should be promoted.
"""

from enum import Enum

from src.config import REGIME_BTC_DOM_UPPER, REGIME_VOLATILITY_UPPER


class MarketRegime(Enum):
    FAVORABLE = "favorable"
    NEUTRAL = "neutral"
    UNFAVORABLE = "unfavorable"


def detect_regime() -> MarketRegime:
    """
    Detect current market regime from Binance tickers.

    Factors:
    1. BTC dominance trend — altcoins suffer when BTC dominance is high
    2. Overall market volatility — high volatility suppresses reliable setups
    3. BTC price action — dumping BTC suppresses altcoins

    Returns FAVORABLE, NEUTRAL, or UNFAVORABLE.
    """
    try:
        from src.binance import get_24h_tickers

        tickers = get_24h_tickers()
        if not tickers:
            return MarketRegime.NEUTRAL

        # Get BTC ticker
        btc_ticker = None
        top_tickers = []
        for t in tickers:
            sym = t.get("symbol", "")
            if sym == "BTCUSDT":
                btc_ticker = t
            if sym.endswith("USDT"):
                try:
                    vol = float(t.get("quoteVolume", 0) or 0)
                    if vol > 0:
                        top_tickers.append(t)
                except (ValueError, TypeError):
                    pass

        if not top_tickers:
            return MarketRegime.NEUTRAL

        # Sort by volume and take top 10
        top_tickers.sort(key=lambda t: float(t.get("quoteVolume", 0) or 0), reverse=True)
        top10 = top_tickers[:10]

        unfavorable_count = 0

        # Factor 1: BTC dominance
        if btc_ticker:
            btc_vol = float(btc_ticker.get("quoteVolume", 0) or 0)
            total_vol = sum(float(t.get("quoteVolume", 0) or 0) for t in top_tickers + [btc_ticker])
            btc_dominance = (btc_vol / total_vol * 100) if total_vol > 0 else 0
            if btc_dominance > REGIME_BTC_DOM_UPPER:
                unfavorable_count += 1

        # Factor 2: Overall market volatility
        ranges = []
        for t in top10:
            try:
                high = float(t.get("highPrice", 0) or 0)
                low = float(t.get("lowPrice", 0) or 0)
                last = float(t.get("lastPrice", 0) or 0)
                if last > 0 and high > low:
                    rng = (high - low) / last * 100
                    ranges.append(rng)
            except (ValueError, TypeError):
                pass

        if ranges:
            avg_range = sum(ranges) / len(ranges)
            if avg_range > REGIME_VOLATILITY_UPPER:
                unfavorable_count += 1

        # Factor 3: BTC itself dumping
        if btc_ticker:
            try:
                btc_change = float(btc_ticker.get("priceChangePercent", 0) or 0)
                if btc_change < -5:
                    unfavorable_count += 1
            except (ValueError, TypeError):
                pass

        if unfavorable_count >= 2:
            return MarketRegime.UNFAVORABLE
        elif unfavorable_count >= 1:
            return MarketRegime.NEUTRAL
        return MarketRegime.FAVORABLE

    except Exception:
        return MarketRegime.NEUTRAL


def is_suppressed(regime: MarketRegime) -> bool:
    """Return True if alerts should be suppressed in this regime."""
    return regime == MarketRegime.UNFAVORABLE
