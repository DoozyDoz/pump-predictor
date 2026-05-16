"""Unit tests for src/backtest.py backtest utilities."""

import os
os.environ["TELEGRAM_BOT_TOKEN"] = "test:token"
os.environ["TELEGRAM_CHAT_ID"] = "12345"

from datetime import datetime, timedelta
import pytest


class TestSortedHistory:
    """Test the SortedHistory helper from src/backtest.py."""

    @pytest.fixture
    def history(self):
        from src.backtest import SortedHistory

        # Build synthetic candles with timestamps (unix seconds)
        base_ts = int(datetime(2024, 1, 1).timestamp())
        candles = [
            {"t": base_ts + i * 3600, "c": 100.0 + i * 0.1}
            for i in range(100)
        ]
        return SortedHistory(candles, key="c")

    def test_len(self, history):
        assert len(history) > 0

    def test_at_exact_time(self, history):
        """at() should return the value closest to the given time."""
        dt = datetime(2024, 1, 1) + timedelta(hours=5)
        val = history.at(dt, window_hours=24)
        assert val is not None
        # At hour 5, value should be 100.5
        assert abs(val - 100.5) < 0.01

    def test_at_outside_window_returns_none(self, history):
        """at() should return None if closest candle is outside window."""
        dt = datetime(2024, 1, 10)  # far outside the data
        val = history.at(dt, window_hours=1)
        assert val is None

    def test_percentile_returns_expected(self, history):
        """percentile should return reasonable values."""
        dt = datetime(2024, 1, 3)
        # Value 105 should be at some percentile of data before Jan 3
        pct = history.percentile(105.0, dt, lookback_days=5)
        assert pct is not None
        assert 0 <= pct <= 100


class TestBacktestHelpers:
    def test_cross_pct(self):
        from src.backtest import _cross_pct

        values = [1, 2, 3, 4, 5]
        assert _cross_pct(values, 3) == 60.0  # 3 of 5 <= 3
        assert _cross_pct(values, 1) == 20.0  # 1 of 5 <= 1
        assert _cross_pct(values, 5) == 100.0
        assert _cross_pct([], 3) == 100.0

    def test_price_at(self):
        from src.backtest import _price_at

        base_ts = int(datetime(2024, 1, 1).timestamp())
        candles = [
            {"t": base_ts + 3600, "c": 101.0},
            {"t": base_ts + 7200, "c": 102.0},
        ]
        dt = datetime(2024, 1, 1) + timedelta(hours=1, minutes=30)
        price = _price_at(candles, dt)
        assert price is not None
        # Closest candle within 24h: 30 min to candle at 1:00 (101.0) and 30 min to candle at 2:00 (102.0)
        # Last iterated wins on tie, so 102.0
        assert price == 102.0

    def test_price_at_too_far_returns_none(self):
        from src.backtest import _price_at

        candles = [{"t": 1000000, "c": 100.0}]
        dt = datetime(2025, 1, 1)
        price = _price_at(candles, dt)
        assert price is None

    def test_gen_windows(self):
        from src.backtest import _gen_windows

        start = datetime(2024, 1, 1)
        end = datetime(2024, 6, 1)
        windows = _gen_windows(start, end)
        assert len(windows) > 0
        for w in windows:
            assert w.train_start
            assert w.test_end
