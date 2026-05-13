"""
Signal computation for Phase 1 quantitative indicators.

Signal 1: Funding-rate extreme (contrarian — low funding = bullish)
Signal 2: Open Interest / Price divergence (rising OI + flat price = accumulation)
Signal 3: Long/Short ratio extreme (contrarian — low ratio = too bearish = bullish)
Signal 4: Taker buy/sell ratio extreme (contrarian — low ratio = too many sellers = bullish)
Signal 5: Order book imbalance (high bid dominance = support = bullish)
"""

from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field
from src.config import (
    FUNDING_PERCENTILE, FUNDING_CROSS_SECTIONAL_PCT, FUNDING_HISTORY_DAYS,
    OI_DIVERGENCE_LOOKBACK_DAYS, OI_DIVERGENCE_HISTORY_DAYS,
    OI_DIVERGENCE_PERCENTILE, OI_DIVERGENCE_CROSS_SECTIONAL_PCT,
    OI_PRICE_MAX_RISE_PCT,
    LS_RATIO_HISTORY_DAYS, LS_RATIO_PERCENTILE, LS_RATIO_CROSS_SECTIONAL_PCT,
)
from src.coinalyze import (
    get_funding_rate, get_funding_rate_history,
    get_open_interest_history, get_ohlcv_history,
    get_long_short_ratio_history,
    spot_to_perp, CoinAnalyzeError,
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class FundingSignal:
    symbol: str
    perp_symbol: str
    current_rate: float
    percentile_90d: float
    cross_sectional_pct: float
    fired: bool = False


@dataclass
class OIDivergenceSignal:
    symbol: str
    perp_symbol: str
    oi_change_pct: float      # OI % change over lookback
    price_change_pct: float   # price % change over lookback
    divergence: float          # oi_change - price_change
    percentile_90d: float
    cross_sectional_pct: float
    fired: bool = False


@dataclass
class LSRatioSignal:
    symbol: str
    perp_symbol: str
    current_ratio: float
    percentile_90d: float
    cross_sectional_pct: float
    fired: bool = False


# ---------------------------------------------------------------------------
# Signal 1: Funding-rate extreme
# ---------------------------------------------------------------------------
def compute_funding_signal(spot_symbol: str) -> Optional[FundingSignal]:
    perp_sym = spot_to_perp(spot_symbol)
    try:
        current_rate = get_funding_rate(perp_sym)
    except CoinAnalyzeError:
        return None
    if current_rate is None:
        return None

    to_dt = datetime.utcnow()
    from_dt = to_dt - timedelta(days=FUNDING_HISTORY_DAYS)
    try:
        candles = get_funding_rate_history(perp_sym, from_dt=from_dt, to_dt=to_dt)
    except CoinAnalyzeError:
        return None
    if not candles:
        return None

    rates = [c["c"] for c in candles if "c" in c]
    if not rates:
        return None

    percentile = (sum(1 for r in rates if r <= current_rate) / len(rates)) * 100
    return FundingSignal(
        symbol=spot_symbol, perp_symbol=perp_sym,
        current_rate=current_rate, percentile_90d=percentile,
        cross_sectional_pct=0.0,
    )


def finalize_funding_signals(signals: list[FundingSignal]) -> list[FundingSignal]:
    if not signals:
        return signals
    rates = [s.current_rate for s in signals]
    for s in signals:
        s.cross_sectional_pct = (sum(1 for r in rates if r <= s.current_rate) / len(rates)) * 100
        s.fired = (
            s.current_rate < 0
            and s.percentile_90d <= FUNDING_PERCENTILE
            and s.cross_sectional_pct <= FUNDING_CROSS_SECTIONAL_PCT
        )
    return signals


def compute_all_funding_signals(spot_symbols: list[str]) -> list[FundingSignal]:
    results = []
    for sym in spot_symbols:
        sig = compute_funding_signal(sym)
        if sig is not None:
            results.append(sig)
    return finalize_funding_signals(results)


# ---------------------------------------------------------------------------
# Signal 2: Open Interest / Price Divergence
# ---------------------------------------------------------------------------
def compute_oi_divergence_signal(spot_symbol: str) -> Optional[OIDivergenceSignal]:
    """
    Compute OI/price divergence.
    Bullish when OI rises while price stays flat or falls (accumulation).
    """
    perp_sym = spot_to_perp(spot_symbol)
    to_dt = datetime.utcnow()
    from_dt = to_dt - timedelta(days=OI_DIVERGENCE_HISTORY_DAYS)

    # Get OI history
    try:
        oi_candles = get_open_interest_history(perp_sym, from_dt=from_dt, to_dt=to_dt)
    except CoinAnalyzeError:
        return None
    if not oi_candles:
        return None

    # Get spot price history
    try:
        price_candles = get_ohlcv_history(spot_symbol, from_dt=from_dt, to_dt=to_dt, interval="4hour")
    except CoinAnalyzeError:
        return None
    if not price_candles:
        return None

    oi_change, price_change = _compute_divergence(oi_candles, price_candles, OI_DIVERGENCE_LOOKBACK_DAYS)
    if oi_change is None or price_change is None:
        return None

    divergence = oi_change - price_change

    # Build distribution of divergences from rolling windows in the history
    div_history = _build_divergence_history(oi_candles, price_candles, OI_DIVERGENCE_LOOKBACK_DAYS)
    if not div_history:
        return None

    percentile = (sum(1 for d in div_history if d <= divergence) / len(div_history)) * 100

    return OIDivergenceSignal(
        symbol=spot_symbol, perp_symbol=perp_sym,
        oi_change_pct=oi_change, price_change_pct=price_change,
        divergence=divergence, percentile_90d=percentile,
        cross_sectional_pct=0.0,
    )


def finalize_oi_divergence_signals(signals: list[OIDivergenceSignal]) -> list[OIDivergenceSignal]:
    if not signals:
        return signals
    divs = [s.divergence for s in signals]
    for s in signals:
        s.cross_sectional_pct = (sum(1 for d in divs if d <= s.divergence) / len(divs)) * 100
        s.fired = (
            s.percentile_90d >= OI_DIVERGENCE_PERCENTILE
            and s.cross_sectional_pct >= (100 - OI_DIVERGENCE_CROSS_SECTIONAL_PCT)
            and s.price_change_pct < OI_PRICE_MAX_RISE_PCT
        )
    return signals


def _compute_divergence(
    oi_candles: list[dict], price_candles: list[dict], lookback_days: int,
) -> tuple[Optional[float], Optional[float]]:
    """Compute OI % change and price % change over lookback period."""
    cutoff = datetime.utcnow() - timedelta(days=lookback_days)

    oi_now = _last_close(oi_candles)
    oi_then = _close_at_time(oi_candles, cutoff)
    price_now = _last_close(price_candles)
    price_then = _close_at_time(price_candles, cutoff)

    if not all([oi_now, oi_then, price_now, price_then]):
        return None, None
    if oi_then == 0 or price_then == 0:
        return None, None

    oi_change = ((oi_now - oi_then) / oi_then) * 100
    price_change = ((price_now - price_then) / price_then) * 100
    return oi_change, price_change


def _build_divergence_history(
    oi_candles: list[dict], price_candles: list[dict], lookback_days: int,
) -> list[float]:
    """Build historical divergence values by walking through the data."""
    results = []
    window = timedelta(days=lookback_days)

    # Get timestamps from OI candles
    for oi_c in oi_candles:
        t = oi_c.get("t")
        if t is None:
            continue
        now_dt = datetime.utcfromtimestamp(t)
        then_dt = now_dt - window

        oi_now = oi_c.get("c", 0)
        oi_then = _close_at_time(oi_candles, then_dt)
        price_now = _close_at_time(price_candles, now_dt)
        price_then = _close_at_time(price_candles, then_dt)

        if not all([oi_now, oi_then, price_now, price_then]):
            continue
        if oi_then == 0 or price_then == 0:
            continue

        oi_change = ((oi_now - oi_then) / oi_then) * 100
        price_change = ((price_now - price_then) / price_then) * 100
        results.append(oi_change - price_change)

    return results


# ---------------------------------------------------------------------------
# Signal 3: Long/Short Ratio Extreme
# ---------------------------------------------------------------------------
def compute_ls_ratio_signal(spot_symbol: str) -> Optional[LSRatioSignal]:
    """
    Compute long/short ratio extreme signal.
    Extremely low ratio = crowd is bearish = contrarian bullish.
    """
    perp_sym = spot_to_perp(spot_symbol)
    to_dt = datetime.utcnow()
    from_dt = to_dt - timedelta(days=LS_RATIO_HISTORY_DAYS)

    try:
        candles = get_long_short_ratio_history(perp_sym, from_dt=from_dt, to_dt=to_dt, interval="4hour")
    except CoinAnalyzeError:
        return None
    if not candles:
        return None

    ratios = [c["r"] for c in candles if "r" in c and c.get("r") is not None]
    if not ratios:
        return None

    current_ratio = ratios[-1]
    percentile = (sum(1 for r in ratios[:-1] if r <= current_ratio) / (len(ratios) - 1)) * 100

    return LSRatioSignal(
        symbol=spot_symbol, perp_symbol=perp_sym,
        current_ratio=current_ratio, percentile_90d=percentile,
        cross_sectional_pct=0.0,
    )


def finalize_ls_ratio_signals(signals: list[LSRatioSignal]) -> list[LSRatioSignal]:
    if not signals:
        return signals
    ratios = [s.current_ratio for s in signals]
    for s in signals:
        s.cross_sectional_pct = (sum(1 for r in ratios if r <= s.current_ratio) / len(ratios)) * 100
        s.fired = (
            s.percentile_90d <= LS_RATIO_PERCENTILE
            and s.cross_sectional_pct <= LS_RATIO_CROSS_SECTIONAL_PCT
        )
    return signals


# ---------------------------------------------------------------------------
# Backtest variants (use pre-fetched historical data)
# ---------------------------------------------------------------------------
def compute_funding_signal_backtest(
    spot_symbol: str, target_dt: datetime,
    rates_history: list[dict], all_rates_snapshot: dict[str, float],
) -> Optional[FundingSignal]:
    current_rate = _find_rate_at_time(rates_history, target_dt)
    if current_rate is None:
        return None
    past = [c["c"] for c in rates_history if "c" in c
            and c.get("t") and datetime.utcfromtimestamp(c["t"]) < target_dt]
    if not past:
        return None
    percentile = (sum(1 for r in past if r <= current_rate) / len(past)) * 100
    universe = list(all_rates_snapshot.values())
    cross = (sum(1 for r in universe if r <= current_rate) / len(universe)) * 100 if universe else 100.0
    perp = spot_to_perp(spot_symbol)
    return FundingSignal(
        symbol=spot_symbol, perp_symbol=perp,
        current_rate=current_rate, percentile_90d=percentile,
        cross_sectional_pct=cross,
        fired=(current_rate < 0 and percentile <= FUNDING_PERCENTILE and cross <= FUNDING_CROSS_SECTIONAL_PCT),
    )


def compute_oi_divergence_backtest(
    spot_symbol: str, target_dt: datetime,
    oi_history: list[dict], price_history: list[dict],
    all_divergences: dict[str, float],
) -> Optional[OIDivergenceSignal]:
    oi_change, price_change = _compute_divergence_at(oi_history, price_history, target_dt, OI_DIVERGENCE_LOOKBACK_DAYS)
    if oi_change is None or price_change is None:
        return None
    divergence = oi_change - price_change
    div_history = _build_divergence_history_before(oi_history, price_history, target_dt, OI_DIVERGENCE_LOOKBACK_DAYS)
    if not div_history:
        return None
    percentile = (sum(1 for d in div_history if d <= divergence) / len(div_history)) * 100
    all_divs = list(all_divergences.values())
    cross = (sum(1 for d in all_divs if d <= divergence) / len(all_divs)) * 100 if all_divs else 100.0
    perp = spot_to_perp(spot_symbol)
    return OIDivergenceSignal(
        symbol=spot_symbol, perp_symbol=perp,
        oi_change_pct=oi_change, price_change_pct=price_change,
        divergence=divergence, percentile_90d=percentile,
        cross_sectional_pct=cross,
        fired=(percentile >= OI_DIVERGENCE_PERCENTILE
               and cross >= (100 - OI_DIVERGENCE_CROSS_SECTIONAL_PCT)
               and price_change < OI_PRICE_MAX_RISE_PCT),
    )


def compute_ls_ratio_backtest(
    spot_symbol: str, target_dt: datetime,
    ls_history: list[dict], all_ratios: dict[str, float],
) -> Optional[LSRatioSignal]:
    current = _find_ls_ratio_at_time(ls_history, target_dt)
    if current is None:
        return None
    past = [c["r"] for c in ls_history if "r" in c
            and c.get("t") and datetime.utcfromtimestamp(c["t"]) < target_dt]
    if not past:
        return None
    percentile = (sum(1 for r in past if r <= current) / len(past)) * 100
    universe = list(all_ratios.values())
    cross = (sum(1 for r in universe if r <= current) / len(universe)) * 100 if universe else 100.0
    perp = spot_to_perp(spot_symbol)
    return LSRatioSignal(
        symbol=spot_symbol, perp_symbol=perp,
        current_ratio=current, percentile_90d=percentile,
        cross_sectional_pct=cross,
        fired=(percentile <= LS_RATIO_PERCENTILE and cross <= LS_RATIO_CROSS_SECTIONAL_PCT),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _last_close(candles: list[dict]) -> Optional[float]:
    for c in reversed(candles):
        val = c.get("c")
        if val is not None:
            return float(val)
    return None


def _close_at_time(candles: list[dict], dt: datetime) -> Optional[float]:
    best, best_diff = None, timedelta.max
    for c in candles:
        t = c.get("t")
        if t is None:
            continue
        cd = datetime.utcfromtimestamp(t)
        diff = abs(cd - dt)
        if diff < best_diff:
            best_diff = diff
            best = c.get("c")
    return float(best) if best is not None else None


def _find_rate_at_time(candles: list[dict], dt: datetime) -> Optional[float]:
    best, best_diff = None, timedelta.max
    for c in candles:
        t = c.get("t")
        if t is None:
            continue
        cd = datetime.utcfromtimestamp(t)
        diff = abs(cd - dt)
        if diff < best_diff:
            best_diff = diff
            best = c.get("c")
    return best


def _find_ls_ratio_at_time(candles: list[dict], dt: datetime) -> Optional[float]:
    best, best_diff = None, timedelta.max
    for c in candles:
        t = c.get("t")
        if t is None:
            continue
        cd = datetime.utcfromtimestamp(t)
        diff = abs(cd - dt)
        if diff < best_diff:
            best_diff = diff
            best = c.get("r")  # LS ratio uses 'r' field
    return best


def _compute_divergence_at(
    oi_candles: list[dict], price_candles: list[dict],
    target_dt: datetime, lookback_days: int,
) -> tuple[Optional[float], Optional[float]]:
    then_dt = target_dt - timedelta(days=lookback_days)
    oi_now = _close_at_time(oi_candles, target_dt)
    oi_then = _close_at_time(oi_candles, then_dt)
    price_now = _close_at_time(price_candles, target_dt)
    price_then = _close_at_time(price_candles, then_dt)
    if not all([oi_now, oi_then, price_now, price_then]):
        return None, None
    if oi_then == 0 or price_then == 0:
        return None, None
    return ((oi_now - oi_then) / oi_then) * 100, ((price_now - price_then) / price_then) * 100


def _build_divergence_history_before(
    oi_candles: list[dict], price_candles: list[dict],
    before_dt: datetime, lookback_days: int,
) -> list[float]:
    results = []
    window = timedelta(days=lookback_days)
    for oi_c in oi_candles:
        t = oi_c.get("t")
        if t is None:
            continue
        now_dt = datetime.utcfromtimestamp(t)
        if now_dt >= before_dt:
            continue
        then_dt = now_dt - window
        oi_now = oi_c.get("c", 0)
        oi_then = _close_at_time(oi_candles, then_dt)
        price_now = _close_at_time(price_candles, now_dt)
        price_then = _close_at_time(price_candles, then_dt)
        if not all([oi_now, oi_then, price_now, price_then]):
            continue
        if oi_then == 0 or price_then == 0:
            continue
        oi_change = ((oi_now - oi_then) / oi_then) * 100
        price_change = ((price_now - price_then) / price_then) * 100
        results.append(oi_change - price_change)
    return results


# ============================================================================
# Signal 4: Taker buy/sell ratio extreme (Binance)
# ============================================================================
from dataclasses import dataclass as _dc

@_dc
class TakerRatioSignal:
    symbol: str
    binance_symbol: str
    current_ratio: float
    percentile_21d: float
    cross_sectional_pct: float
    fired: bool = False


def compute_taker_ratio_signal(
    spot_symbol: str,
    taker_history: 'TakerHistory',
) -> Optional[TakerRatioSignal]:
    """Compute taker ratio extreme from pre-fetched history."""
    from src.config import TAKER_RATIO_HISTORY_MS, TAKER_RATIO_PERCENTILE
    from src.binance import get_binance_symbol

    bin_sym = get_binance_symbol(spot_symbol)
    dt = datetime.utcnow()
    current = taker_history.at(dt)
    if current is None:
        return None
    pct = taker_history.percentile(current, dt, TAKER_RATIO_HISTORY_MS)
    return TakerRatioSignal(
        symbol=spot_symbol, binance_symbol=bin_sym,
        current_ratio=current, percentile_21d=pct or 100.0,
        cross_sectional_pct=0.0,
    )


def finalize_taker_signals(signals: list[TakerRatioSignal]) -> list[TakerRatioSignal]:
    from src.config import TAKER_RATIO_PERCENTILE, TAKER_RATIO_CROSS_SECTIONAL_PCT
    if not signals:
        return signals
    ratios = [s.current_ratio for s in signals]
    for s in signals:
        s.cross_sectional_pct = (sum(1 for r in ratios if r <= s.current_ratio) / len(ratios)) * 100
        s.fired = (
            s.percentile_21d is not None
            and s.percentile_21d <= TAKER_RATIO_PERCENTILE
            and s.cross_sectional_pct <= TAKER_RATIO_CROSS_SECTIONAL_PCT
        )
    return signals


# ============================================================================
# Signal 5: Order book imbalance (Binance spot)
# ============================================================================
@_dc
class OrderBookSignal:
    symbol: str
    binance_symbol: str
    bid_dominance: float   # 0-1, >0.5 = more bids
    cross_sectional_pct: float
    fired: bool = False


def compute_order_book_signal(spot_symbol: str) -> Optional[OrderBookSignal]:
    """Fetch current order book depth and compute bid dominance."""
    from src.binance import get_binance_symbol, get_order_book, compute_order_book_imbalance
    from src.config import ORDER_BOOK_LEVELS

    bin_sym = get_binance_symbol(spot_symbol)
    try:
        depth = get_order_book(bin_sym, limit=100)
    except Exception:
        return None
    dominance = compute_order_book_imbalance(depth, ORDER_BOOK_LEVELS)
    return OrderBookSignal(
        symbol=spot_symbol, binance_symbol=bin_sym,
        bid_dominance=dominance, cross_sectional_pct=0.0,
    )


def finalize_order_book_signals(signals: list[OrderBookSignal]) -> list[OrderBookSignal]:
    from src.config import ORDER_BOOK_CROSS_SECTIONAL_PCT
    if not signals:
        return signals
    doms = [s.bid_dominance for s in signals]
    for s in signals:
        s.cross_sectional_pct = (sum(1 for d in doms if d <= s.bid_dominance) / len(doms)) * 100
        s.fired = s.cross_sectional_pct >= (100 - ORDER_BOOK_CROSS_SECTIONAL_PCT)
    return signals
