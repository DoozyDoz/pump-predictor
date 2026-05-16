"""Unit tests for src/risk.py ATR computation and position sizing."""

import os
os.environ["TELEGRAM_BOT_TOKEN"] = "test:token"
os.environ["TELEGRAM_CHAT_ID"] = "12345"

from src.risk import compute_atr, position_size, stop_loss_pct
from src.config import ATR_STOP_MULTIPLIER, POSITION_SIZE_PCT


class TestPositionSize:
    def test_high_atr_smaller_position(self):
        """Higher ATR should result in smaller position size."""
        size_low_atr = position_size(atr_pct=2.0, portfolio_usd=1000)
        size_high_atr = position_size(atr_pct=8.0, portfolio_usd=1000)
        assert size_low_atr > size_high_atr, (
            "Lower ATR should give larger position"
        )

    def test_position_capped(self):
        """Position size should be capped at POSITION_SIZE_PCT * portfolio."""
        max_pos = 1000 * POSITION_SIZE_PCT
        # Very small ATR would normally give a huge position
        size = position_size(atr_pct=0.1, portfolio_usd=1000)
        assert size <= max_pos, f"Position {size} exceeds cap {max_pos}"

    def test_atr_zero_uses_default(self):
        """ATR <= 0 should fall back to default position size."""
        size = position_size(atr_pct=0.0, portfolio_usd=1000)
        expected = 1000 * POSITION_SIZE_PCT
        assert size == expected, (
            f"Expected {expected}, got {size}"
        )

    def test_reasonable_position(self):
        """With typical ATR (3%), position should be reasonable."""
        size = position_size(atr_pct=3.0, portfolio_usd=1000)
        assert 0 < size <= 1000 * POSITION_SIZE_PCT


class TestStopLoss:
    def test_stop_loss_negative(self):
        """Stop loss should always be negative."""
        sl = stop_loss_pct(atr_pct=3.0)
        assert sl < 0, f"Stop loss {sl} should be negative"

    def test_stop_loss_scales_with_atr(self):
        """Higher ATR should give wider (more negative) stop."""
        sl_low = stop_loss_pct(atr_pct=2.0, atr_multiplier=2.0)
        sl_high = stop_loss_pct(atr_pct=5.0, atr_multiplier=2.0)
        assert abs(sl_high) > abs(sl_low), (
            "Higher ATR should give wider stop"
        )

    def test_default_multiplier(self):
        """Default multiplier should be ATR_STOP_MULTIPLIER."""
        sl = stop_loss_pct(atr_pct=3.0)
        assert sl == -(ATR_STOP_MULTIPLIER * 3.0)


class TestComputeATR:
    def test_insufficient_data_returns_none(self, monkeypatch):
        """compute_atr should return None with insufficient data."""
        monkeypatch.setattr(
            "src.binance.get_klines",
            lambda *args, **kwargs: []
        )
        result = compute_atr("BTCUSDT")
        assert result is None

    def test_none_on_exception(self, monkeypatch):
        """compute_atr should return None on API error."""
        monkeypatch.setattr(
            "src.binance.get_klines",
            lambda *args, **kwargs: exec('raise Exception("API error")')
        )
        # The lambda above raises, but the monkeypatched function must throw
        def failing_get(*args, **kwargs):
            raise Exception("API error")

        monkeypatch.setattr("src.binance.get_klines", failing_get)
        result = compute_atr("BTCUSDT")
        assert result is None
