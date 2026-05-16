"""Unit tests for catalyst-specific Telegram formatting."""

import os
os.environ["TELEGRAM_BOT_TOKEN"] = "test:token"
os.environ["TELEGRAM_CHAT_ID"] = "12345"

from src.notify import TelegramNotifier


class TestNotifyCatalyst:
    def test_format_catalyst_watchlist_contains_no_entry_yet(self):
        notifier = TelegramNotifier("test:token", "12345")
        candidates = [
            {
                "symbol": "BTCUSDT",
                "score": 2,
                "fired_signals": "funding_extreme",
                "catalyst_score": 0.91,
                "catalyst_title": "Binance listing",
                "catalyst_published_at": "2026-05-16T10:00:00",
                "priority": "URGENT_CATALYST",
                "price_change_1h": 2.0,
                "price_change_4h": 5.0,
                "price_change_24h": 8.0,
            }
        ]
        msg = notifier.format_catalyst_watchlist(candidates)
        assert "No entry yet" in msg
        assert "BUY" not in msg
        assert "URGENT CATALYST WATCH: BTC" in msg

    def test_format_catalyst_watchlist_empty(self):
        notifier = TelegramNotifier("test:token", "12345")
        msg = notifier.format_catalyst_watchlist([])
        assert "No candidates today" in msg

    def test_format_catalyst_entry_contains_no_buy_language(self):
        notifier = TelegramNotifier("test:token", "12345")
        entries = [
            {
                "symbol": "BTCUSDT",
                "atr_pct": "3.00%",
                "position_size_usd": "50.00",
                "catalyst_score": 0.80,
                "catalyst_event_type": "major_exchange_listing",
                "catalyst_title": "Binance listing",
            }
        ]
        msg = notifier.format_catalyst_entry(entries, 1000.0)
        assert "CATALYST CONFIRMED ENTRY" in msg
        assert "BUY" not in msg
        assert "paper-only" in msg.lower() or "PAPER" in msg

    def test_format_catalyst_entry_empty(self):
        notifier = TelegramNotifier("test:token", "12345")
        msg = notifier.format_catalyst_entry([], 1000.0)
        assert "No entry signals today" in msg

    def test_format_catalyst_watchlist_shows_real_price_changes(self):
        notifier = TelegramNotifier("test:token", "12345")
        candidates = [
            {
                "symbol": "BTCUSDT",
                "score": 2,
                "fired_signals": "funding_extreme",
                "catalyst_score": 0.91,
                "catalyst_title": "Binance listing",
                "catalyst_published_at": "2026-05-16T10:00:00",
                "priority": "URGENT_CATALYST",
                "price_change_1h": 2.5,
                "price_change_4h": 5.0,
                "price_change_24h": -1.0,
            }
        ]
        msg = notifier.format_catalyst_watchlist(candidates)
        assert "1h +2.5%" in msg
        assert "4h +5.0%" in msg
        assert "24h -1.0%" in msg
        assert "unavailable" not in msg.lower()

    def test_format_catalyst_watchlist_shows_unavailable_for_none(self):
        notifier = TelegramNotifier("test:token", "12345")
        candidates = [
            {
                "symbol": "BTCUSDT",
                "score": 2,
                "fired_signals": "funding_extreme",
                "catalyst_score": 0.91,
                "catalyst_title": "Binance listing",
                "catalyst_published_at": "2026-05-16T10:00:00",
                "priority": "URGENT_CATALYST",
                "price_change_1h": None,
                "price_change_4h": None,
                "price_change_24h": None,
            }
        ]
        msg = notifier.format_catalyst_watchlist(candidates)
        assert "unavailable" in msg.lower()
        assert "0.0%" not in msg

    def test_format_catalyst_watchlist_shows_unavailable_when_missing_keys(self):
        notifier = TelegramNotifier("test:token", "12345")
        candidates = [
            {
                "symbol": "BTCUSDT",
                "score": 2,
                "fired_signals": "funding_extreme",
                "catalyst_score": 0.91,
                "catalyst_title": "Binance listing",
                "catalyst_published_at": "2026-05-16T10:00:00",
                "priority": "URGENT_CATALYST",
            }
        ]
        msg = notifier.format_catalyst_watchlist(candidates)
        assert "unavailable" in msg.lower()
        assert "0.0%" not in msg
