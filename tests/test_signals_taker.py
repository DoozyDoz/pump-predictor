"""Unit tests for taker ratio signal logic in src/signals.py."""

import os
os.environ["TELEGRAM_BOT_TOKEN"] = "test:token"
os.environ["TELEGRAM_CHAT_ID"] = "12345"

from datetime import datetime, timedelta
import pytest


class TestTakerRatioSignal:
    """Tests for TakerRatioSignal dataclass and taker signal finalization."""

    def test_taker_ratio_signal_creation(self):
        from src.signals import TakerRatioSignal

        sig = TakerRatioSignal(
            symbol="BTCUSDT", binance_symbol="BTCUSDT",
            current_ratio=0.3, percentile_21d=1.5,
            cross_sectional_pct=2.0,
        )
        assert sig.symbol == "BTCUSDT"
        assert sig.current_ratio == 0.3
        assert not sig.fired  # Not fired until finalize

    def test_finalize_taker_signals_marks_fired(self, monkeypatch):
        from src.signals import TakerRatioSignal, finalize_taker_signals

        # finalize_taker_signals recalculates cross_sectional_pct from ratios.
        # With only one signal, cross_sectional_pct = 100%, which is > 5%.
        # Monkeypatch CROSS_SECTIONAL_PCT in config to 100 so it passes.
        monkeypatch.setattr("src.config.TAKER_RATIO_CROSS_SECTIONAL_PCT", 100.0)

        sig = TakerRatioSignal(
            symbol="BTCUSDT", binance_symbol="BTCUSDT",
            current_ratio=0.3, percentile_21d=1.0,
            cross_sectional_pct=2.0,
        )
        results = finalize_taker_signals([sig])
        assert len(results) == 1
        # With percentile_21d=1.0 <= TAKER_RATIO_PERCENTILE (2.0)
        # and cross_sectional_pct=100.0 <= 100.0 (monkeypatched)
        assert results[0].fired


class TestTakerHistoryHelper:
    """Tests for TakerHistory (used in taker ratio computation)."""

    @pytest.fixture
    def taker_history(self):
        from src.binance import TakerHistory

        base_ts = int(datetime(2024, 1, 1).timestamp() * 1000)
        candles = [
            {"timestamp": base_ts + i * 3600_000, "buySellRatio": 0.5 + (i % 5) * 0.1}
            for i in range(200)
        ]
        return TakerHistory(candles)

    def test_len(self, taker_history):
        assert len(taker_history) == 200

    def test_at_returns_value(self, taker_history):
        dt = datetime(2024, 1, 1) + timedelta(hours=5)
        val = taker_history.at(dt, window_ms=3600_000)
        assert val is not None
        assert 0.0 < val < 1.0

    def test_at_outside_range_returns_none(self, taker_history):
        dt = datetime(2025, 1, 1)
        val = taker_history.at(dt, window_ms=3600_000)
        assert val is None

    def test_percentile(self, taker_history):
        dt = datetime(2024, 1, 2)
        # Value 0.9 should be high percentile
        pct = taker_history.percentile(0.9, dt, lookback_ms=86400_000)
        assert pct is not None
        assert 0 <= pct <= 100
