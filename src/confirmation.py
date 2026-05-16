"""
Intraday confirmation layer — Phase 2 of the staged workflow.

Checks watchlist candidates for price action confirmation, volume pickup,
order book improvement, and taker ratio flip detection.
"""

from src.config import (
    CONFIRMATION_PRICE_MOVE_PCT,
    CONFIRMATION_VOLUME_SURGE_PCT,
)
from src.stages import StageManager
from src.binance import get_klines, get_taker_ratio_history


class ConfirmationChecker:
    """Checks watchlist candidates for confirmation signals."""

    def __init__(self, stage_mgr: StageManager):
        self.stage_mgr = stage_mgr

    def run_confirmation(self, symbols: list[str] | None = None) -> list[dict]:
        """Run confirmation checks on all active watchlist candidates.
        Returns list of result dicts with keys: symbol, confirmed, denied, reason.
        """
        candidates = self.stage_mgr.get_watchlist_candidates()
        if symbols:
            candidates = [c for c in candidates if c["symbol"] in symbols]

        if not candidates:
            return []

        results = []
        for c in candidates:
            sym = c["symbol"]
            result = self._check_single(sym)
            if result["confirmed"]:
                # Promote to confirmation stage
                self.stage_mgr.promote_to_confirmation(c["id"], result.get("reason", ""))
                # Then check if it qualifies for entry
                entry_check = self._check_entry(sym)
                if entry_check["confirmed"]:
                    self.stage_mgr.promote_to_entry(c["id"], entry_check.get("reason", ""))
                    result["promoted_to_entry"] = True
                results.append(result)
            elif result["denied"]:
                self.stage_mgr.expire(c["id"], result.get("reason", ""))
                results.append(result)
            else:
                # Still pending — no action
                results.append(result)

        # Expire stale items
        self.stage_mgr.expire_stale()

        return results

    def _check_single(self, symbol: str) -> dict:
        """Run all confirmation checks for a single symbol."""
        confirmed_count = 0
        total_checks = 0
        reasons = []

        # Check 1: Price action
        pa = self._check_price_action(symbol)
        total_checks += 1
        if pa["confirmed"]:
            confirmed_count += 1
            reasons.append(pa.get("reason", "price action OK"))

        # Check 2: Volume confirmation
        vc = self._check_volume_confirmation(symbol)
        total_checks += 1
        if vc["confirmed"]:
            confirmed_count += 1
            reasons.append(vc.get("reason", "volume surge OK"))

        # Check 3: Order book improvement
        ob = self._check_order_book(symbol)
        total_checks += 1
        if ob["confirmed"]:
            confirmed_count += 1
            reasons.append(ob.get("reason", "order book improving"))

        # Check 4: Taker ratio flip
        tf = self._check_taker_flip(symbol)
        total_checks += 1
        if tf["confirmed"]:
            confirmed_count += 1
            reasons.append(tf.get("reason", "taker flip detected"))

        # Decision: at least 2 of 4 checks must pass for confirmation
        min_confirmations = max(1, total_checks // 2)
        if confirmed_count >= min_confirmations:
            return {
                "symbol": symbol,
                "confirmed": True,
                "denied": False,
                "reason": "; ".join(reasons),
                "checks_passed": confirmed_count,
                "checks_total": total_checks,
            }
        elif confirmed_count == 0:
            return {
                "symbol": symbol,
                "confirmed": False,
                "denied": True,
                "reason": "no confirmation checks passed",
                "checks_passed": 0,
                "checks_total": total_checks,
            }
        else:
            return {
                "symbol": symbol,
                "confirmed": False,
                "denied": False,
                "reason": "partial confirmation, still pending",
                "checks_passed": confirmed_count,
                "checks_total": total_checks,
            }

    def _check_price_action(self, symbol: str) -> dict:
        """Check if price is bouncing from recent low."""
        try:
            candles = get_klines(symbol, interval="1h", limit=48, market="spot")
        except Exception:
            return {"confirmed": False, "symbol": symbol, "reason": "no klines"}

        if not candles or len(candles) < 12:
            return {"confirmed": False, "symbol": symbol, "reason": "insufficient data"}

        closes = [c["c"] for c in candles if c.get("c")]
        if not closes:
            return {"confirmed": False, "symbol": symbol, "reason": "no close prices"}

        recent_low = min(closes[-24:])  # lowest close in last 24h
        current = closes[-1]
        bounce_pct = ((current - recent_low) / recent_low) * 100

        if bounce_pct >= CONFIRMATION_PRICE_MOVE_PCT:
            return {
                "confirmed": True,
                "symbol": symbol,
                "reason": f"price bounced {bounce_pct:.2f}% from recent low",
                "bounce_pct": bounce_pct,
            }
        return {
            "confirmed": False,
            "symbol": symbol,
            "reason": f"price bounce {bounce_pct:.2f}% below {CONFIRMATION_PRICE_MOVE_PCT}% threshold",
            "bounce_pct": bounce_pct,
        }

    def _check_volume_confirmation(self, symbol: str) -> dict:
        """Check if 1h volume is above recent average."""
        try:
            candles = get_klines(symbol, interval="1h", limit=48, market="spot")
        except Exception:
            return {"confirmed": False, "symbol": symbol, "reason": "no klines"}

        if not candles or len(candles) < 24:
            return {"confirmed": False, "symbol": symbol, "reason": "insufficient data"}

        volumes = [c["v"] for c in candles if c.get("v")]
        if not volumes or len(volumes) < 24:
            return {"confirmed": False, "symbol": symbol, "reason": "no volume data"}

        recent_3h_avg = sum(volumes[-3:]) / 3
        full_24h_avg = sum(volumes[-24:]) / 24
        surge_pct = ((recent_3h_avg - full_24h_avg) / full_24h_avg) * 100 if full_24h_avg > 0 else 0

        if surge_pct >= CONFIRMATION_VOLUME_SURGE_PCT:
            return {
                "confirmed": True,
                "symbol": symbol,
                "reason": f"volume surge {surge_pct:.1f}% above 24h avg",
                "surge_pct": surge_pct,
            }
        return {
            "confirmed": False,
            "symbol": symbol,
            "reason": f"volume surge {surge_pct:.1f}% below {CONFIRMATION_VOLUME_SURGE_PCT}% threshold",
            "surge_pct": surge_pct,
        }

    def _check_taker_flip(self, symbol: str) -> dict:
        """Check if taker ratio flipped from extreme low toward normal.
        Fetches historical taker ratios, computes rolling z-score,
        checks if z-score was below -1.5 in last 24h and is now above -0.5.
        """
        try:
            candles = get_taker_ratio_history(symbol, period="1h", limit=500)
        except Exception:
            return {"confirmed": False, "symbol": symbol, "reason": "no taker data"}

        if not candles or len(candles) < 48:
            return {"confirmed": False, "symbol": symbol, "reason": "insufficient taker data"}

        # Build sorted history
        pairs = [(c["timestamp"], float(c["buySellRatio"])) for c in candles
                 if c.get("timestamp") and c.get("buySellRatio")]
        pairs.sort(key=lambda x: x[0])

        if len(pairs) < 48:
            return {"confirmed": False, "symbol": symbol, "reason": "insufficient taker pairs"}

        ratios = [p[1] for p in pairs]
        z_scores = _rolling_zscore(ratios, window=24)

        if not z_scores:
            return {"confirmed": False, "symbol": symbol, "reason": "no z-scores"}

        # Check: was z-score below -1.5 in recent history AND now above -0.5?
        recent_z = z_scores[-24:]  # last 24 readings
        current_z = z_scores[-1] if z_scores else 0

        was_extreme = any(z < -1.5 for z in recent_z)
        is_improving = current_z > -0.5

        if was_extreme and is_improving:
            return {
                "confirmed": True,
                "symbol": symbol,
                "reason": f"taker flip: z-score moved from extreme to {current_z:.2f}",
                "current_z": current_z,
            }
        return {
            "confirmed": False,
            "symbol": symbol,
            "reason": f"taker z-score {current_z:.2f}, no flip detected",
            "current_z": current_z,
        }

    def _check_order_book(self, symbol: str) -> dict:
        """Re-check order book for improving bid dominance."""
        try:
            from src.binance import get_order_book, compute_order_book_imbalance
            from src.config import ORDER_BOOK_MIN_BID_DOM

            depth = get_order_book(symbol, limit=100)
            dom = compute_order_book_imbalance(depth, 10)
            if dom >= ORDER_BOOK_MIN_BID_DOM:
                return {
                    "confirmed": True,
                    "symbol": symbol,
                    "reason": f"bid dominance {dom:.3f} above {ORDER_BOOK_MIN_BID_DOM}",
                    "bid_dominance": dom,
                }
            return {
                "confirmed": False,
                "symbol": symbol,
                "reason": f"bid dominance {dom:.3f} below {ORDER_BOOK_MIN_BID_DOM}",
                "bid_dominance": dom,
            }
        except Exception as e:
            return {"confirmed": False, "symbol": symbol, "reason": f"order book error: {e}"}

    def _check_entry(self, symbol: str) -> dict:
        """After confirmation, check if conditions are right for entry.
        Combines price action + order book + taker flip.
        """
        pa = self._check_price_action(symbol)
        ob = self._check_order_book(symbol)
        tf = self._check_taker_flip(symbol)

        passes = sum([pa["confirmed"], ob["confirmed"], tf["confirmed"]])
        if passes >= 2:
            return {
                "confirmed": True,
                "symbol": symbol,
                "reason": f"entry conditions met ({passes}/3 checks)",
            }
        return {
            "confirmed": False,
            "symbol": symbol,
            "reason": f"entry conditions not met ({passes}/3 checks)",
        }


def _rolling_zscore(values: list[float], window: int = 24) -> list[float]:
    """Compute rolling z-score for a list of values using a centered window.
    Each point's z-score is computed relative to the mean and std of
    the surrounding window of values."""
    if len(values) < window:
        return []
    half = window // 2
    z_scores = []
    for i in range(len(values)):
        start = max(0, i - half)
        end = min(len(values), i + half + 1)
        segment = values[start:end]
        if len(segment) < 2:
            z_scores.append(0.0)
            continue
        mean = sum(segment) / len(segment)
        var = sum((x - mean) ** 2 for x in segment) / len(segment)
        std = var ** 0.5
        if std == 0:
            z_scores.append(0.0)
        else:
            z_scores.append((values[i] - mean) / std)
    return z_scores
