"""Unit tests for OI divergence signal fixes in src/signals.py."""

import os
os.environ["TELEGRAM_BOT_TOKEN"] = "test:token"
os.environ["TELEGRAM_CHAT_ID"] = "12345"

from src.signals import (
    _classify_trend,
    OIDivergenceSignal,
    finalize_oi_divergence_signals,
)


class TestClassifyTrend:
    """Tests for _classify_trend() helper."""

    def test_rising_above_2(self):
        assert _classify_trend(2.0) == "rising"
        assert _classify_trend(5.0) == "rising"

    def test_falling_below_neg2(self):
        assert _classify_trend(-2.0) == "falling"
        assert _classify_trend(-10.0) == "falling"

    def test_flat_in_between(self):
        assert _classify_trend(0.0) == "flat"
        assert _classify_trend(1.9) == "flat"
        assert _classify_trend(-1.9) == "flat"

    def test_boundary_exact(self):
        """Exactly at thresholds should be classified as the boundary direction."""
        assert _classify_trend(2.0, rising_threshold=2.0,
                               falling_threshold=-2.0) == "rising"
        assert _classify_trend(-2.0, rising_threshold=2.0,
                               falling_threshold=-2.0) == "falling"


class TestOIDivergenceSignal:
    def test_signal_creation(self):
        sig = OIDivergenceSignal(
            symbol="BTCUSDT", perp_symbol="BTCUSDT",
            oi_change_pct=10.0, price_change_pct=1.0,
            divergence=9.0, percentile_90d=99.0,
            cross_sectional_pct=1.0,
            oi_trend="rising", price_trend="flat",
        )
        assert sig.symbol == "BTCUSDT"
        assert sig.oi_trend == "rising"
        assert sig.price_trend == "flat"
        assert not sig.fired  # Not fired until finalize

    def test_finalize_divergence_fires_when_oi_rising_price_falling(self):
        """Signal fires only when oi_trend='rising' and price_trend in ('falling','flat')."""
        sig = OIDivergenceSignal(
            symbol="BTCUSDT", perp_symbol="BTCUSDT",
            oi_change_pct=10.0, price_change_pct=-2.0,
            divergence=12.0, percentile_90d=99.0,
            cross_sectional_pct=98.0,
            oi_trend="rising", price_trend="falling",
        )
        sig2 = OIDivergenceSignal(
            symbol="ETHUSDT", perp_symbol="ETHUSDT",
            oi_change_pct=8.0, price_change_pct=0.5,
            divergence=12.0, percentile_90d=97.0,
            cross_sectional_pct=96.0,
            oi_trend="rising", price_trend="flat",
        )
        results = finalize_oi_divergence_signals([sig, sig2])
        assert results[0].fired
        assert results[1].fired

    def test_does_not_fire_when_both_rising(self):
        """When both OI and price are rising, it's trending, not accumulation."""
        sig = OIDivergenceSignal(
            symbol="BTCUSDT", perp_symbol="BTCUSDT",
            oi_change_pct=10.0, price_change_pct=8.0,
            divergence=2.0, percentile_90d=99.0,
            cross_sectional_pct=98.0,
            oi_trend="rising", price_trend="rising",
        )
        results = finalize_oi_divergence_signals([sig])
        assert not results[0].fired

    def test_does_not_fire_when_oi_falling(self):
        """When OI is falling, there is no accumulation regardless of price."""
        sig = OIDivergenceSignal(
            symbol="BTCUSDT", perp_symbol="BTCUSDT",
            oi_change_pct=-5.0, price_change_pct=-3.0,
            divergence=-2.0, percentile_90d=99.0,
            cross_sectional_pct=98.0,
            oi_trend="falling", price_trend="falling",
        )
        results = finalize_oi_divergence_signals([sig])
        assert not results[0].fired

    def test_empty_list_returns_empty(self):
        result = finalize_oi_divergence_signals([])
        assert result == []
