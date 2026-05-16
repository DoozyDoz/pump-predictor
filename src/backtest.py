"""4-signal backtest: funding, OI divergence, LS ratio, taker ratio. Order book is live-only."""

from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass
import numpy as np
from bisect import bisect_left
from collections import defaultdict
from src.config import (
    PUMP_THRESHOLD_PCT, PUMP_WINDOW_HOURS, LOOKAHEAD_HOURS,
    BACKTEST_YEARS, BACKTEST_TRAIN_MONTHS, BACKTEST_TEST_MONTHS,
    STOP_LOSS_PCT, TAKE_PROFIT_1_PCT, TAKE_PROFIT_1_PCT_SHARE,
    TAKE_PROFIT_2_PCT, TAKE_PROFIT_2_PCT_SHARE, TRAILING_STOP_PCT,
    DB_PATH, GO_PRECISION, GO_PROFIT_FACTOR, ALERT_THRESHOLD,
    FUNDING_PERCENTILE, FUNDING_CROSS_SECTIONAL_PCT,
    OI_DIVERGENCE_PERCENTILE, OI_DIVERGENCE_CROSS_SECTIONAL_PCT,
    OI_PRICE_MAX_RISE_PCT,
    LS_RATIO_PERCENTILE, LS_RATIO_CROSS_SECTIONAL_PCT,
    TAKER_RATIO_PERCENTILE, TAKER_RATIO_CROSS_SECTIONAL_PCT,
    TAKER_RATIO_HISTORY_MS,
)
from src.db import db_session
from src.binance import (
    get_klines, get_funding_rate_history,
    get_open_interest_history, get_global_ls_ratio_history,
)
from src.binance import TakerHistory, get_taker_ratio_history


@dataclass
class BacktestWindow:
    train_start: str; train_end: str; test_start: str; test_end: str
    total_alerts: int = 0; pumps_caught: int = 0; precision: float = 0.0
    total_trades: int = 0; winning_trades: int = 0
    gross_profit_pct: float = 0.0; gross_loss_pct: float = 0.0; profit_factor: float = 0.0


class SortedHistory:
    """Pre-sorted history for fast binary-search lookups."""
    def __init__(self, candles: list[dict], key: str = "c"):
        pairs = [(c["t"], c.get(key)) for c in candles if c.get("t") is not None and c.get(key) is not None]
        pairs.sort(key=lambda x: x[0])
        self.ts = np.array([p[0] for p in pairs], dtype=np.int64)
        self.vals = np.array([p[1] for p in pairs], dtype=np.float64)

    def __len__(self):
        return len(self.ts)

    def at(self, dt: datetime, window_hours: int = 24) -> Optional[float]:
        """Value closest to dt within a time window (seconds)."""
        target = int(dt.timestamp())
        idx = bisect_left(self.ts, target)
        best, best_diff = None, window_hours * 3600 + 1
        for i in (idx, idx - 1, idx + 1, idx - 2, idx + 2):
            if 0 <= i < len(self.ts):
                diff = abs(int(self.ts[i]) - target)
                if diff < best_diff:
                    best_diff = diff
                    best = float(self.vals[i])
        return best if best_diff <= window_hours * 3600 else None

    def percentile(self, value: float, before_dt: datetime, lookback_days: int = 90) -> Optional[float]:
        """Percentile of value within lookback_days before before_dt."""
        cutoff = int((before_dt - timedelta(days=lookback_days)).timestamp())
        target = int(before_dt.timestamp())
        # Get values in [cutoff, target)
        past = self.vals[(self.ts >= cutoff) & (self.ts < target)]
        if len(past) == 0:
            return None
        return (np.sum(past <= value) / len(past)) * 100


def detect_pump(ohlcv: list[dict], entry_price: float, entry_dt: datetime) -> tuple[bool, float]:
    window_end = entry_dt + timedelta(hours=PUMP_WINDOW_HOURS)
    max_price = entry_price
    for c in ohlcv:
        t = c.get("t")
        if t is None: continue
        cd = datetime.utcfromtimestamp(t)
        if cd <= entry_dt: continue
        if cd > window_end: break
        h = c.get("h", 0)
        if h and h > max_price: max_price = h
    pct = ((max_price - entry_price) / entry_price) * 100
    return pct >= PUMP_THRESHOLD_PCT, pct


def simulate_trade(entry_price: float, ohlcv: list[dict], entry_dt: datetime) -> float:
    tp1 = entry_price * (1 + TAKE_PROFIT_1_PCT)
    tp2 = entry_price * (1 + TAKE_PROFIT_2_PCT)
    stop = entry_price * (1 + STOP_LOSS_PCT)
    s1, s2 = TAKE_PROFIT_1_PCT_SHARE, TAKE_PROFIT_2_PCT_SHARE
    st = 1.0 - s1 - s2
    realized, h1, h2, peak = 0.0, False, False, entry_price
    window_end = entry_dt + timedelta(hours=LOOKAHEAD_HOURS)
    for c in ohlcv:
        t = c.get("t")
        if t is None: continue
        cd = datetime.utcfromtimestamp(t)
        if cd <= entry_dt: continue
        if cd > window_end: break
        hi, lo = c.get("h", 0), c.get("l", 0)
        if not hi or not lo: continue
        if not h1 and hi >= tp1: h1 = True; realized += s1 * TAKE_PROFIT_1_PCT
        if h1 and not h2 and hi >= tp2: h2 = True; realized += s2 * TAKE_PROFIT_2_PCT
        if h1:
            peak = max(peak, hi)
            if lo <= peak * (1 - TRAILING_STOP_PCT):
                realized += st * ((peak * (1 - TRAILING_STOP_PCT) - entry_price) / entry_price)
                return realized * 100
        if lo <= stop:
            rem = 1.0 - (s1 if h1 else 0) - (s2 if h2 else 0)
            realized += rem * STOP_LOSS_PCT
            return realized * 100
    rem = 1.0 - (s1 if h1 else 0) - (s2 if h2 else 0)
    if rem > 0 and ohlcv:
        lc = ohlcv[-1].get("c", entry_price)
        realized += rem * ((lc - entry_price) / entry_price)
    return realized * 100


def run_backtest(spot_symbols: list[str], max_symbols: int = 0) -> list[BacktestWindow]:
    if max_symbols and max_symbols < len(spot_symbols):
        spot_symbols = spot_symbols[:max_symbols]

    end = datetime.utcnow()
    rq_start = end - timedelta(days=BACKTEST_YEARS * 365)

    print(f"Fetching Binance data for {len(spot_symbols)} symbols...")
    from src.snapshots import get_snapshot_history
    fund_h, oi_h, ls_h, ohlcv_h = {}, {}, {}, {}
    for sym in spot_symbols:
        # Merge Binance history with local CoinGlass snapshots
        fund_candles = _merge_backtest(_fetch_bn(sym, "funding"),
                                       get_snapshot_history(sym, "funding_rate", 365), "c")
        oi_candles = _merge_backtest(_fetch_bn(sym, "oi"),
                                     get_snapshot_history(sym, "oi_value", 365), "c")
        ls_candles = _merge_backtest(_fetch_bn(sym, "ls"),
                                     get_snapshot_history(sym, "ls_ratio", 365), "r")
        fund_h[sym] = SortedHistory(fund_candles, "c")
        oi_h[sym] = SortedHistory(oi_candles, "c")
        ls_h[sym] = SortedHistory(ls_candles, "r")
        ohlcv_h[sym] = _fetch_bn(sym, "ohlcv")

    print(f"Fetching Binance taker ratio data...")
    taker_h = {}
    for sym in spot_symbols:
        try:
            candles = get_taker_ratio_history(sym, period="1h", limit=500)
            taker_h[sym] = TakerHistory(candles) if candles else None
        except Exception:
            taker_h[sym] = None
    has_taker = sum(1 for v in taker_h.values() if v is not None and len(v) > 0)
    print(f"  Taker data available for {has_taker}/{len(spot_symbols)} symbols")

    # Determine data range
    actual_start = end
    for sym in spot_symbols:
        for h in [fund_h.get(sym), oi_h.get(sym), ls_h.get(sym)]:
            if h and len(h) > 0:
                ts = datetime.utcfromtimestamp(int(h.ts[0]))
                if ts < actual_start:
                    actual_start = ts
    actual_start += timedelta(days=7)  # minimal warmup — Binance data is shallow
    if actual_start >= end:
        print("ERROR: Not enough data"); return []

    print(f"Backtest: {actual_start.date()} -> {end.date()} | {len(spot_symbols)} tokens")

    # Generate test dates (every 3rd day)
    all_dates = set()
    for sym in spot_symbols:
        for c in ohlcv_h.get(sym, []):
            t = c.get("t")
            if t: all_dates.add(datetime.utcfromtimestamp(t).date())
    dates = sorted(all_dates)
    test_dates = [d for d in dates if d >= actual_start.date() and d <= end.date()]

    windows = _gen_windows(datetime.combine(dates[0], datetime.min.time()),
                           datetime.combine(dates[-1], datetime.min.time()))
    results = []

    for w in windows:
        ws = datetime.fromisoformat(w.test_start).date()
        we = datetime.fromisoformat(w.test_end).date()
        w_dates = [d for d in test_dates if ws <= d <= we][::5]  # every 5th day
        if len(w_dates) < 3: continue

        print(f"Window: {ws} -> {we} ({len(w_dates)} checkpoints)", end=" ", flush=True)

        for d in w_dates:
            dt = datetime.combine(d, datetime.min.time())

            # --- Pass 1: build cross-sectional snapshots (fast) ---
            fund_snap = {}
            oi_snap = {}
            ls_snap = {}
            taker_snap = {}
            price_snap = {}
            price_chg_snap = {}
            for sym in spot_symbols:
                fr = fund_h[sym].at(dt) if sym in fund_h else None
                if fr is not None: fund_snap[sym] = fr
                oi_now = oi_h[sym].at(dt) if sym in oi_h else None
                oi_then = oi_h[sym].at(dt - timedelta(days=7)) if sym in oi_h else None
                px_now = _price_at(ohlcv_h.get(sym, []), dt)
                px_then = _price_at(ohlcv_h.get(sym, []), dt - timedelta(days=7))
                if all([oi_now, oi_then, px_now, px_then]) and oi_then > 0 and px_then > 0:
                    oi_snap[sym] = ((oi_now - oi_then) / oi_then) * 100 - ((px_now - px_then) / px_then) * 100
                    price_chg_snap[sym] = ((px_now - px_then) / px_then) * 100
                if px_now is not None: price_snap[sym] = px_now
                lr = ls_h[sym].at(dt) if sym in ls_h else None
                if lr is not None: ls_snap[sym] = lr
                # Taker ratio snapshot
                if sym in taker_h and taker_h[sym] is not None:
                    tr = taker_h[sym].at(dt, window_ms=3600_000)
                    if tr is not None:
                        taker_snap[sym] = tr

            # --- Pass 2: score each token ---
            for sym in spot_symbols:
                score = 0
                entry_price = price_snap.get(sym)
                if entry_price is None: continue

                # Signal 1: Funding extreme (full token-specific + cross-sectional)
                fr = fund_snap.get(sym)
                if fr is not None and fr < 0 and sym in fund_h:
                    cs = _cross_pct(fund_snap.values(), fr)
                    if cs <= FUNDING_CROSS_SECTIONAL_PCT:
                        pct = fund_h[sym].percentile(fr, dt, 90)
                        if pct is not None and pct <= FUNDING_PERCENTILE:
                            score += 1

                # Signal 2: OI divergence (cross-sectional only — token-specific too slow)
                div = oi_snap.get(sym)
                if div is not None and len(oi_snap) >= 20:
                    cs = _cross_pct(oi_snap.values(), div)
                    pc = price_chg_snap.get(sym, 0)
                    if cs >= (100 - OI_DIVERGENCE_CROSS_SECTIONAL_PCT) and pc is not None and pc < OI_PRICE_MAX_RISE_PCT:
                        score += 1

                # Signal 3: LS ratio extreme (token-specific + cross-sectional)
                lr = ls_snap.get(sym)
                if lr is not None and sym in ls_h and len(ls_snap) >= 20:
                    cs = _cross_pct(ls_snap.values(), lr)
                    if cs <= LS_RATIO_CROSS_SECTIONAL_PCT:
                        pct = ls_h[sym].percentile(lr, dt, 90)
                        if pct is not None and pct <= LS_RATIO_PERCENTILE:
                            score += 1

                # Signal 4: Taker ratio extreme (token-specific + cross-sectional)
                tr = taker_snap.get(sym)
                if tr is not None and sym in taker_h and taker_h[sym] is not None and len(taker_snap) >= 20:
                    cs = _cross_pct(taker_snap.values(), tr)
                    if cs <= TAKER_RATIO_CROSS_SECTIONAL_PCT:
                        pct = taker_h[sym].percentile(tr, dt, TAKER_RATIO_HISTORY_MS)
                        if pct is not None and pct <= TAKER_RATIO_PERCENTILE:
                            score += 1

                if score < ALERT_THRESHOLD: continue

                w.total_alerts += 1
                pumped, _ = detect_pump(ohlcv_h.get(sym, []), entry_price, dt)
                if pumped: w.pumps_caught += 1

                future = _after(ohlcv_h.get(sym, []), dt)
                if future:
                    pnl = simulate_trade(entry_price, future, dt)
                    w.total_trades += 1
                    if pnl > 0:
                        w.winning_trades += 1; w.gross_profit_pct += pnl
                    else:
                        w.gross_loss_pct += abs(pnl)

        w.precision = (w.pumps_caught / w.total_alerts * 100) if w.total_alerts else 0
        w.profit_factor = (w.gross_profit_pct / w.gross_loss_pct) if w.gross_loss_pct > 0 else (999 if w.gross_profit_pct > 0 else 0)
        results.append(w)
        print(f"A:{w.total_alerts} P:{w.precision:.0f}% PF:{w.profit_factor:.2f}")

    _save_results(results)
    return results


# --- Helpers ---
def _cross_pct(values, x) -> float:
    vals = list(values)
    if not vals: return 100.0
    return (sum(1 for v in vals if v <= x) / len(vals)) * 100


def _price_at(candles: list[dict], dt: datetime) -> Optional[float]:
    best, best_diff = None, timedelta(hours=999)
    for c in candles:
        t = c.get("t")
        if t is None: continue
        diff = abs(datetime.utcfromtimestamp(t) - dt)
        if diff < best_diff and diff <= timedelta(hours=24):
            best_diff = diff
            best = c.get("c")
    return float(best) if best is not None else None


def _price_snap_func(candles: list[dict]):
    """Return a function that gets price at a timestamp from these candles."""
    def f(dt: datetime) -> Optional[float]:
        return _price_at(candles, dt)
    return f


def _oi_divergence_percentile(oi_hist: 'SortedHistory', price_func, dt: datetime, lookback_days: int) -> Optional[float]:
    """Compute OI divergence token-specific percentile by sampling history."""
    cutoff = dt - timedelta(days=lookback_days)
    target = int(dt.timestamp())
    past_divs = []
    # Sample every 4h in the lookback window
    sample_dt = cutoff
    while sample_dt < dt:
        sample_dt += timedelta(hours=4)
        oi_now = oi_hist.at(sample_dt)
        oi_then = oi_hist.at(sample_dt - timedelta(days=7))
        px_now = price_func(sample_dt)
        px_then = price_func(sample_dt - timedelta(days=7))
        if not all([oi_now, oi_then, px_now, px_then]) or oi_then == 0 or px_then == 0:
            continue
        past_divs.append(((oi_now - oi_then) / oi_then) * 100 - ((px_now - px_then) / px_then) * 100)
    if not past_divs:
        return None
    current_div = None
    oi_now = oi_hist.at(dt)
    oi_then = oi_hist.at(dt - timedelta(days=7))
    px_now = price_func(dt)
    px_then = price_func(dt - timedelta(days=7))
    if all([oi_now, oi_then, px_now, px_then]) and oi_then > 0 and px_then > 0:
        current_div = ((oi_now - oi_then) / oi_then) * 100 - ((px_now - px_then) / px_then) * 100
    if current_div is None:
        return None
    return (sum(1 for d in past_divs if d <= current_div) / len(past_divs)) * 100


def _fetch_bn(sym, kind):
    """Fetch Binance data. History depth: funding=deep, OI=~30d, LS=~30d."""
    try:
        if kind == "funding": return get_funding_rate_history(sym, limit=1000)
        if kind == "oi": return get_open_interest_history(sym, period="4h", limit=500)
        if kind == "ls": return get_global_ls_ratio_history(sym, period="4h", limit=500)
        if kind == "ohlcv": return get_klines(sym, interval="4h", limit=1000, market="spot")
    except Exception:
        return []
    return []


def _merge_backtest(binance: list[dict], local: list[dict], key: str) -> list[dict]:
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


def _gen_windows(start, end):
    windows, current = [], start
    step = timedelta(days=BACKTEST_TEST_MONTHS * 30)
    train = timedelta(days=BACKTEST_TRAIN_MONTHS * 30)
    while current + train + step <= end:
        te = current + train; ts = te + timedelta(days=1)
        windows.append(BacktestWindow(
            train_start=current.isoformat(), train_end=te.isoformat(),
            test_start=ts.isoformat(), test_end=(ts + step).isoformat()))
        current += step  # advance by test window size, not 1 day
    return windows


def _after(candles, dt):
    return [c for c in candles if c.get("t") and datetime.utcfromtimestamp(c["t"]) > dt]


def _save_results(results):
    with db_session() as conn:
        for r in results:
            conn.execute("""INSERT INTO backtest_results
                (train_start, train_end, test_start, test_end,
                 total_alerts, pumps_caught, precision,
                 total_trades, winning_trades,
                 gross_profit_pct, gross_loss_pct, profit_factor)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (r.train_start, r.train_end, r.test_start, r.test_end,
                 r.total_alerts, r.pumps_caught, r.precision,
                 r.total_trades, r.winning_trades,
                 r.gross_profit_pct, r.gross_loss_pct, r.profit_factor))
    print(f"\nResults saved to {DB_PATH}")


def print_summary(results):
    if not results: print("No results."); return
    ta = sum(r.total_alerts for r in results)
    tp = sum(r.pumps_caught for r in results)
    tt = sum(r.total_trades for r in results)
    tw = sum(r.winning_trades for r in results)
    tpr = sum(r.gross_profit_pct for r in results)
    tl = sum(r.gross_loss_pct for r in results)
    prec = (tp / ta * 100) if ta else 0
    wr = (tw / tt * 100) if tt else 0
    pf = (tpr / tl) if tl > 0 else (999 if tpr > 0 else 0)
    avg_pf = np.mean([r.profit_factor for r in results if r.profit_factor < 999])
    go = prec >= GO_PRECISION * 100 and pf >= GO_PROFIT_FACTOR
    print("\n" + "=" * 60)
    print("BACKTEST SUMMARY (3 signals — token-specific percentiles)")
    print("=" * 60)
    print(f"Windows:          {len(results)}")
    print(f"Total alerts:     {ta}")
    print(f"Pumps caught:     {tp}")
    print(f"Precision:        {prec:.1f}%  (target: >= {GO_PRECISION*100:.0f}%)")
    print(f"Total trades:     {tt}")
    print(f"Winning trades:   {tw}")
    print(f"Win rate:         {wr:.1f}%")
    print(f"Gross profit:     {tpr:.1f}%")
    print(f"Gross loss:       {tl:.1f}%")
    print(f"Profit factor:    {pf:.2f}  (target: >= {GO_PROFIT_FACTOR})")
    print(f"Mean window PF:   {avg_pf:.2f}")
    print(f"GO/NO-GO:         {'*** GO ***' if go else '--- NO-GO ---'}")
    print("=" * 60)


def run_staged_backtest(spot_symbols: list[str], max_symbols: int = 0) -> list[BacktestWindow]:
    """
    Backtest that simulates the 3-stage workflow:
      Phase 1: Signal computation -> watchlist candidates (lower threshold)
      Phase 2: Confirmation checks after N hours
      Phase 3: Entry with ATR-based sizing

    Compares precision/profit factor against current immediate-alert approach.
    """
    if max_symbols and max_symbols < len(spot_symbols):
        spot_symbols = spot_symbols[:max_symbols]

    from src.config import WATCHLIST_THRESHOLD, CONFIRMATION_POLL_MINUTES
    from src.config import CONFIRMATION_PRICE_MOVE_PCT

    end = datetime.utcnow()

    print(f"Fetching Binance data for {len(spot_symbols)} symbols (staged backtest)...")
    from src.snapshots import get_snapshot_history
    fund_h, oi_h, ls_h, ohlcv_h = {}, {}, {}, {}
    for sym in spot_symbols:
        fund_candles = _merge_backtest(_fetch_bn(sym, "funding"),
                                       get_snapshot_history(sym, "funding_rate", 365), "c")
        oi_candles = _merge_backtest(_fetch_bn(sym, "oi"),
                                     get_snapshot_history(sym, "oi_value", 365), "c")
        ls_candles = _merge_backtest(_fetch_bn(sym, "ls"),
                                     get_snapshot_history(sym, "ls_ratio", 365), "r")
        fund_h[sym] = SortedHistory(fund_candles, "c")
        oi_h[sym] = SortedHistory(oi_candles, "c")
        ls_h[sym] = SortedHistory(ls_candles, "r")
        ohlcv_h[sym] = _fetch_bn(sym, "ohlcv")

    print("Fetching Binance taker ratio data...")
    taker_h = {}
    for sym in spot_symbols:
        try:
            candles = get_taker_ratio_history(sym, period="1h", limit=500)
            taker_h[sym] = TakerHistory(candles) if candles else None
        except Exception:
            taker_h[sym] = None

    # Determine data range
    actual_start = end
    for sym in spot_symbols:
        for h in [fund_h.get(sym), oi_h.get(sym), ls_h.get(sym)]:
            if h and len(h) > 0:
                ts = datetime.utcfromtimestamp(int(h.ts[0]))
                if ts < actual_start:
                    actual_start = ts
    actual_start += timedelta(days=7)
    if actual_start >= end:
        print("ERROR: Not enough data")
        return []

    print(f"Staged backtest: {actual_start.date()} -> {end.date()} | {len(spot_symbols)} tokens")

    all_dates = set()
    for sym in spot_symbols:
        for c in ohlcv_h.get(sym, []):
            t = c.get("t")
            if t:
                all_dates.add(datetime.utcfromtimestamp(t).date())
    dates = sorted(all_dates)
    test_dates = [d for d in dates if d >= actual_start.date() and d <= end.date()]

    windows = _gen_windows(datetime.combine(dates[0], datetime.min.time()),
                           datetime.combine(dates[-1], datetime.min.time()))
    results = []

    for w in windows:
        ws = datetime.fromisoformat(w.test_start).date()
        we = datetime.fromisoformat(w.test_end).date()
        w_dates = [d for d in test_dates if ws <= d <= we][::5]
        if len(w_dates) < 3:
            continue

        print(f"Window: {ws} -> {we} ({len(w_dates)} checkpoints)", end=" ", flush=True)

        for d in w_dates:
            dt_phase1 = datetime.combine(d, datetime.min.time())

            # Same cross-sectional snapshots as run_backtest
            fund_snap = {}
            oi_snap = {}
            ls_snap = {}
            taker_snap = {}
            price_snap = {}
            price_chg_snap = {}
            for sym in spot_symbols:
                fr = fund_h[sym].at(dt_phase1) if sym in fund_h else None
                if fr is not None:
                    fund_snap[sym] = fr
                oi_now = oi_h[sym].at(dt_phase1) if sym in oi_h else None
                oi_then = oi_h[sym].at(dt_phase1 - timedelta(days=7)) if sym in oi_h else None
                px_now = _price_at(ohlcv_h.get(sym, []), dt_phase1)
                px_then = _price_at(ohlcv_h.get(sym, []), dt_phase1 - timedelta(days=7))
                if all([oi_now, oi_then, px_now, px_then]) and oi_then > 0 and px_then > 0:
                    oi_snap[sym] = ((oi_now - oi_then) / oi_then) * 100 - ((px_now - px_then) / px_then) * 100
                    price_chg_snap[sym] = ((px_now - px_then) / px_then) * 100
                if px_now is not None:
                    price_snap[sym] = px_now
                lr = ls_h[sym].at(dt_phase1) if sym in ls_h else None
                if lr is not None:
                    ls_snap[sym] = lr
                if sym in taker_h and taker_h[sym] is not None:
                    tr = taker_h[sym].at(dt_phase1, window_ms=3600_000)
                    if tr is not None:
                        taker_snap[sym] = tr

            # Phase 1: Score tokens with watchlist threshold
            phase1_candidates = []
            for sym in spot_symbols:
                score = 0
                entry_price = price_snap.get(sym)
                if entry_price is None:
                    continue

                fr = fund_snap.get(sym)
                if fr is not None and fr < 0 and sym in fund_h:
                    cs = _cross_pct(fund_snap.values(), fr)
                    if cs <= FUNDING_CROSS_SECTIONAL_PCT:
                        pct = fund_h[sym].percentile(fr, dt_phase1, 90)
                        if pct is not None and pct <= FUNDING_PERCENTILE:
                            score += 1

                div = oi_snap.get(sym)
                if div is not None and len(oi_snap) >= 20:
                    cs = _cross_pct(oi_snap.values(), div)
                    pc = price_chg_snap.get(sym, 0)
                    if cs >= (100 - OI_DIVERGENCE_CROSS_SECTIONAL_PCT) and pc is not None and pc < OI_PRICE_MAX_RISE_PCT:
                        score += 1

                lr = ls_snap.get(sym)
                if lr is not None and sym in ls_h and len(ls_snap) >= 20:
                    cs = _cross_pct(ls_snap.values(), lr)
                    if cs <= LS_RATIO_CROSS_SECTIONAL_PCT:
                        pct = ls_h[sym].percentile(lr, dt_phase1, 90)
                        if pct is not None and pct <= LS_RATIO_PERCENTILE:
                            score += 1

                tr = taker_snap.get(sym)
                if tr is not None and sym in taker_h and taker_h[sym] is not None and len(taker_snap) >= 20:
                    cs = _cross_pct(taker_snap.values(), tr)
                    if cs <= TAKER_RATIO_CROSS_SECTIONAL_PCT:
                        pct = taker_h[sym].percentile(tr, dt_phase1, TAKER_RATIO_HISTORY_MS)
                        if pct is not None and pct <= TAKER_RATIO_PERCENTILE:
                            score += 1

                # Use watchlist threshold
                if score >= WATCHLIST_THRESHOLD:
                    phase1_candidates.append(sym)

            if not phase1_candidates:
                continue

            # Phase 2: Simulate confirmation after N hours
            dt_phase2 = dt_phase1 + timedelta(hours=CONFIRMATION_POLL_MINUTES // 60 + 1)
            confirmed_candidates = []
            for sym in phase1_candidates:
                entry_price = price_snap.get(sym)
                if entry_price is None:
                    continue

                # Price action check
                price_at_phase2 = _price_at(ohlcv_h.get(sym, []), dt_phase2)
                if price_at_phase2 and entry_price > 0:
                    price_move = ((price_at_phase2 - entry_price) / entry_price) * 100
                    if price_move >= CONFIRMATION_PRICE_MOVE_PCT:
                        # Volume check (simplified)
                        vol_data = _get_volume_at(ohlcv_h.get(sym, []), dt_phase2)
                        if vol_data:
                            confirmed_candidates.append(sym)
                            continue

                # Not confirmed: price action or volume conditions not met,
                # or data unavailable at confirmation time.
                # Conservative simulation: only confirm when conditions pass.

            if not confirmed_candidates:
                continue

            # Phase 3: Simulate trades
            for sym in confirmed_candidates:
                entry_price = price_snap.get(sym)
                if entry_price is None:
                    continue

                w.total_alerts += 1
                pumped, _ = detect_pump(ohlcv_h.get(sym, []), entry_price, dt_phase1)
                if pumped:
                    w.pumps_caught += 1

                future = _after(ohlcv_h.get(sym, []), dt_phase1)
                if future:
                    pnl = simulate_trade(entry_price, future, dt_phase1)
                    w.total_trades += 1
                    if pnl > 0:
                        w.winning_trades += 1
                        w.gross_profit_pct += pnl
                    else:
                        w.gross_loss_pct += abs(pnl)

        w.precision = (w.pumps_caught / w.total_alerts * 100) if w.total_alerts else 0
        w.profit_factor = (w.gross_profit_pct / w.gross_loss_pct) if w.gross_loss_pct > 0 else (
            999 if w.gross_profit_pct > 0 else 0
        )
        results.append(w)
        print(f"A:{w.total_alerts} P:{w.precision:.0f}% PF:{w.profit_factor:.2f}")

    _save_results(results)
    return results


def _get_volume_at(candles: list[dict], dt: datetime) -> float | None:
    """Get volume from OHLCV candles closest to dt."""
    best, best_diff = None, timedelta(hours=12)
    for c in candles:
        t = c.get("t")
        if t is None:
            continue
        diff = abs(datetime.utcfromtimestamp(t) - dt)
        if diff < best_diff:
            best_diff = diff
            best = c.get("v", 0)
    return float(best) if best is not None else None
