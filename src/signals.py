"""
Signal computation for Phase 1 quantitative indicators.

Signal 1: Funding-rate extreme (contrarian — low funding = bullish)
Signal 2: Open Interest / Price divergence (rising OI + flat price = accumulation)
Signal 3: Long/Short ratio extreme (contrarian — low ratio = too bearish = bullish)
Signal 4: Taker buy/sell ratio extreme (contrarian — low ratio = too many sellers = bullish)
Signal 5: Order book imbalance (high bid dominance = support = bullish)

All data from Binance public APIs (spot + fapi + futures/data).
"""

from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field as _dc_field
from src.config import (
    FUNDING_PERCENTILE, FUNDING_CROSS_SECTIONAL_PCT, FUNDING_HISTORY_DAYS,
    OI_DIVERGENCE_LOOKBACK_DAYS, OI_DIVERGENCE_HISTORY_DAYS,
    OI_DIVERGENCE_PERCENTILE, OI_DIVERGENCE_CROSS_SECTIONAL_PCT,
    OI_PRICE_MAX_RISE_PCT,
    LS_RATIO_HISTORY_DAYS, LS_RATIO_PERCENTILE, LS_RATIO_CROSS_SECTIONAL_PCT,
)
from src.binance import (
    get_funding_rate, get_funding_rate_history,
    get_open_interest_history, get_klines,
    get_global_ls_ratio_history,
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
    """Binance: spot and perp symbols are the same (e.g. BTCUSDT)."""
    try:
        current_rate = get_funding_rate(spot_symbol)
    except Exception:
        return None
    if current_rate is None:
        return None

    try:
        candles = get_funding_rate_history(spot_symbol, limit=500)
    except Exception:
        return None

    # Merge local snapshot history (grows over time, fills gaps beyond Binance window)
    from src.snapshots import get_snapshot_history
    local = get_snapshot_history(spot_symbol, "funding_rate", since_days=90)
    merged = _merge_histories(candles or [], local, key="c")

    if not merged:
        return None

    rates = [c["c"] for c in merged if "c" in c]
    if not rates:
        return None

    percentile = (sum(1 for r in rates if r <= current_rate) / len(rates)) * 100
    return FundingSignal(
        symbol=spot_symbol, perp_symbol=spot_symbol,
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
    to_dt = datetime.utcnow()
    from_dt = to_dt - timedelta(days=OI_DIVERGENCE_HISTORY_DAYS)

    # OI from Binance futures/data + local snapshots
    try:
        oi_candles = get_open_interest_history(spot_symbol, period="4h", limit=500)
    except Exception:
        oi_candles = []
    from src.snapshots import get_snapshot_history
    oi_local = get_snapshot_history(spot_symbol, "oi_value", since_days=90)
    oi_candles = _merge_histories(oi_candles or [], oi_local, key="c")
    if not oi_candles:
        return None

    # Price from Binance spot klines
    try:
        price_candles = get_klines(spot_symbol, interval="4h", limit=500, market="spot")
    except Exception:
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
        symbol=spot_symbol, perp_symbol=spot_symbol,
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
    try:
        candles = get_global_ls_ratio_history(spot_symbol, period="4h", limit=500)
    except Exception:
        candles = []
    from src.snapshots import get_snapshot_history
    local = get_snapshot_history(spot_symbol, "ls_ratio", since_days=90)
    candles = _merge_histories(candles or [], local, key="r")
    if not candles:
        return None

    ratios = [c["r"] for c in candles if "r" in c and c.get("r") is not None]
    if not ratios:
        return None

    current_ratio = ratios[-1]
    percentile = (sum(1 for r in ratios[:-1] if r <= current_ratio) / (len(ratios) - 1)) * 100

    return LSRatioSignal(
        symbol=spot_symbol, perp_symbol=spot_symbol,
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
    perp = spot_symbol  # Binance: spot and perp symbols are identical
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
    perp = spot_symbol  # Binance: spot and perp symbols are identical
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
    perp = spot_symbol  # Binance: spot and perp symbols are identical
    return LSRatioSignal(
        symbol=spot_symbol, perp_symbol=perp,
        current_ratio=current, percentile_90d=percentile,
        cross_sectional_pct=cross,
        fired=(percentile <= LS_RATIO_PERCENTILE and cross <= LS_RATIO_CROSS_SECTIONAL_PCT),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _merge_histories(binance: list[dict], local: list[dict],
                     key: str = "c") -> list[dict]:
    """Merge Binance history with local snapshots, dedup by timestamp."""
    if not local:
        return binance
    result = list(binance)
    seen = {c["t"] for c in result if c.get("t")}
    for snap in local:
        if snap.get("t") and snap["t"] not in seen and snap.get(key) is not None:
            result.append(snap)
            seen.add(snap["t"])
    result.sort(key=lambda c: c.get("t", 0))
    return result


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

    dt = datetime.utcnow()
    current = taker_history.at(dt)
    if current is None:
        return None
    pct = taker_history.percentile(current, dt, TAKER_RATIO_HISTORY_MS)
    return TakerRatioSignal(
        symbol=spot_symbol, binance_symbol=spot_symbol,
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
# Signal 5: Order book imbalance (Binance spot) — multi-snapshot persistence
# ============================================================================
ORDER_BOOK_SNAPSHOTS = 3
ORDER_BOOK_SNAPSHOT_INTERVAL = 3  # seconds between snapshots


@_dc
class OrderBookSignal:
    symbol: str
    binance_symbol: str
    bid_dominance: float        # average across snapshots (for display)
    bid_snapshots: list = _dc_field(default_factory=list)  # raw dominances for persistence
    cross_sectional_pct: float = 0.0
    fired: bool = False
    persistence_count: int = 0  # how many snapshots passed cross-sectional


def compute_order_book_signal(spot_symbol: str) -> Optional[OrderBookSignal]:
    """Fetch order book 3x at 5s intervals to catch spoofed walls."""
    import time
    from src.binance import get_order_book, compute_order_book_imbalance
    from src.config import ORDER_BOOK_LEVELS

    snapshots = []
    for i in range(ORDER_BOOK_SNAPSHOTS):
        try:
            depth = get_order_book(spot_symbol, limit=100)
            dom = compute_order_book_imbalance(depth, ORDER_BOOK_LEVELS)
            snapshots.append(dom)
        except Exception:
            return None
        if i < ORDER_BOOK_SNAPSHOTS - 1:
            time.sleep(ORDER_BOOK_SNAPSHOT_INTERVAL)

    if not snapshots:
        return None
    return OrderBookSignal(
        symbol=spot_symbol, binance_symbol=spot_symbol,
        bid_dominance=sum(snapshots) / len(snapshots),
        bid_snapshots=snapshots,
        cross_sectional_pct=0.0,
    )


def finalize_order_book_signals(signals: list[OrderBookSignal]) -> list[OrderBookSignal]:
    from src.config import ORDER_BOOK_CROSS_SECTIONAL_PCT
    if not signals:
        return signals

    n_snapshots = ORDER_BOOK_SNAPSHOTS
    majority_needed = n_snapshots // 2 + 1  # 2 of 3

    # For each snapshot round, compute cross-sectional percentile and check fire
    for round_idx in range(n_snapshots):
        # Gather dominances for this round across all tokens
        round_doms = []
        for s in signals:
            if len(s.bid_snapshots) > round_idx:
                round_doms.append(s.bid_snapshots[round_idx])
        if not round_doms:
            continue

        n = len(round_doms)
        threshold_pct = 100 - ORDER_BOOK_CROSS_SECTIONAL_PCT
        for s in signals:
            if len(s.bid_snapshots) > round_idx:
                dom = s.bid_snapshots[round_idx]
                pct = (sum(1 for d in round_doms if d <= dom) / n) * 100
                if pct >= threshold_pct:
                    s.persistence_count += 1

    # Fired if majority of snapshots passed cross-sectional check
    # AND absolute bid dominance meets the minimum floor
    from src.config import ORDER_BOOK_MIN_BID_DOM
    for s in signals:
        s.fired = (
            s.persistence_count >= majority_needed
            and s.bid_dominance >= ORDER_BOOK_MIN_BID_DOM
        )

    # Set cross_sectional_pct on the average for display
    avg_doms = [s.bid_dominance for s in signals]
    for s in signals:
        s.cross_sectional_pct = (
            sum(1 for d in avg_doms if d <= s.bid_dominance) / len(avg_doms)
        ) * 100

    return signals
