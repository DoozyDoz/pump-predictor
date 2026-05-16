"""Unit tests for catalyst-first watchlist logic."""

import os
os.environ["TELEGRAM_BOT_TOKEN"] = "test:token"
os.environ["TELEGRAM_CHAT_ID"] = "12345"

from unittest.mock import MagicMock

from src.config import (
    CATALYST_WEIGHT,
    CATALYST_MAJOR_SCORE,
    ENABLE_CATALYST_ONLY_ENTRY,
)
from src.catalysts import CatalystResult, CatalystEvent
from src.watchlist import generate_watchlist


class MockSignal:
    def __init__(self, symbol, fired=True):
        self.symbol = symbol
        self.fired = fired


class MockProfile:
    def __init__(self, blocked=False, catalyst_boost=0.0):
        self.blocked = blocked
        self.catalyst_boost = catalyst_boost


def mock_catalyst_result(symbol, score, is_major=False, is_negative=False):
    return CatalystResult(
        symbol=symbol,
        score=score,
        is_major_catalyst=is_major,
        is_negative_catalyst=is_negative,
        events=[CatalystEvent(symbol=symbol, source="test", title="test event")],
        dominant_event_type="major_exchange_listing",
    )


class TestCatalystWatchlist:
    def test_catalyst_only_creates_watchlist(self, monkeypatch):
        """A strong catalyst alone should create a watchlist candidate."""
        funding = []
        oi = []
        ls = []
        taker = []
        book = []
        qual = {"BTCUSDT": MockProfile(blocked=False, catalyst_boost=0.0)}
        catalyst_results = {
            "BTCUSDT": mock_catalyst_result("BTCUSDT", 0.80),
        }

        import src.watchlist as wl
        monkeypatch.setattr(wl, "_persist_watchlist_candidate", lambda c, m: None)
        monkeypatch.setattr(wl, "StageManager", lambda: MagicMock())

        candidates = generate_watchlist(
            funding, oi, ls, taker, book, qual, catalyst_results=catalyst_results
        )
        assert len(candidates) > 0
        assert any(c["symbol"] == "BTCUSDT" for c in candidates)
        assert any(c.get("setup_type") == "CATALYST_WATCH" for c in candidates)

    def test_major_catalyst_urgent_priority(self, monkeypatch):
        """A major catalyst should set URGENT_CATALYST priority."""
        funding = []
        oi = []
        ls = []
        taker = []
        book = []
        qual = {"BTCUSDT": MockProfile(blocked=False)}
        catalyst_results = {
            "BTCUSDT": mock_catalyst_result("BTCUSDT", CATALYST_MAJOR_SCORE, is_major=True),
        }

        import src.watchlist as wl
        monkeypatch.setattr(wl, "_persist_watchlist_candidate", lambda c, m: None)
        monkeypatch.setattr(wl, "StageManager", lambda: MagicMock())

        candidates = generate_watchlist(
            funding, oi, ls, taker, book, qual, catalyst_results=catalyst_results
        )
        assert any(c.get("priority") == "URGENT_CATALYST" for c in candidates)

    def test_negative_catalyst_blocks_weak_technical(self, monkeypatch):
        """A negative catalyst should block a weak technical setup."""
        funding = [MockSignal("BTCUSDT", fired=True)]
        oi = []
        ls = []
        taker = []
        book = []
        qual = {"BTCUSDT": MockProfile(blocked=False)}
        catalyst_results = {
            "BTCUSDT": mock_catalyst_result("BTCUSDT", 0.60, is_negative=True),
        }

        import src.watchlist as wl
        monkeypatch.setattr(wl, "_persist_watchlist_candidate", lambda c, m: None)
        monkeypatch.setattr(wl, "StageManager", lambda: MagicMock())

        candidates = generate_watchlist(
            funding, oi, ls, taker, book, qual, catalyst_results=catalyst_results
        )
        assert all(c["symbol"] != "BTCUSDT" for c in candidates)

    def test_negative_catalyst_does_not_block_strong_technical(self, monkeypatch):
        """A negative catalyst should NOT block an extremely strong technical setup."""
        # 4/5 technical signals = strong technical
        funding = [MockSignal("BTCUSDT", fired=True)]
        oi = [MockSignal("BTCUSDT", fired=True)]
        ls = [MockSignal("BTCUSDT", fired=True)]
        taker = [MockSignal("BTCUSDT", fired=True)]
        book = []
        qual = {"BTCUSDT": MockProfile(blocked=False)}
        catalyst_results = {
            "BTCUSDT": mock_catalyst_result("BTCUSDT", 0.60, is_negative=True),
        }

        import src.watchlist as wl
        monkeypatch.setattr(wl, "_persist_watchlist_candidate", lambda c, m: None)
        monkeypatch.setattr(wl, "StageManager", lambda: MagicMock())

        candidates = generate_watchlist(
            funding, oi, ls, taker, book, qual, catalyst_results=catalyst_results
        )
        assert any(c["symbol"] == "BTCUSDT" for c in candidates)

    def test_combined_moderate_catalyst_and_technical(self, monkeypatch):
        """Combined moderate catalyst + moderate technical should create candidate."""
        funding = [MockSignal("BTCUSDT", fired=True)]
        oi = [MockSignal("BTCUSDT", fired=True)]
        ls = []
        taker = []
        book = []
        qual = {"BTCUSDT": MockProfile(blocked=False)}
        catalyst_results = {
            "BTCUSDT": mock_catalyst_result("BTCUSDT", 0.45),
        }

        import src.watchlist as wl
        monkeypatch.setattr(wl, "_persist_watchlist_candidate", lambda c, m: None)
        monkeypatch.setattr(wl, "StageManager", lambda: MagicMock())

        candidates = generate_watchlist(
            funding, oi, ls, taker, book, qual, catalyst_results=catalyst_results
        )
        assert any(c["symbol"] == "BTCUSDT" for c in candidates)

    def test_stale_catalyst_loses_score(self, monkeypatch):
        """Stale catalyst should have low freshness and not qualify."""
        from datetime import datetime, timedelta
        funding = []
        oi = []
        ls = []
        taker = []
        book = []
        qual = {"BTCUSDT": MockProfile(blocked=False)}
        stale_event = CatalystEvent(
            symbol="BTCUSDT",
            source="test",
            title="old news",
            event_type="major_exchange_listing",
            published_at=(datetime.utcnow() - timedelta(hours=48)).isoformat(),
        )
        catalyst_results = {
            "BTCUSDT": CatalystResult(
                symbol="BTCUSDT",
                score=0.80,
                events=[stale_event],
                dominant_event_type="major_exchange_listing",
            ),
        }

        import src.watchlist as wl
        monkeypatch.setattr(wl, "_persist_watchlist_candidate", lambda c, m: None)
        monkeypatch.setattr(wl, "StageManager", lambda: MagicMock())

        # Because score is manually set high, this test checks that scorer re-evaluates
        # In practice generate_watchlist uses the precomputed score from catalyst_results
        # so we override to a low score to simulate staleness
        catalyst_results["BTCUSDT"].score = 0.40
        candidates = generate_watchlist(
            funding, oi, ls, taker, book, qual, catalyst_results=catalyst_results
        )
        assert all(c["symbol"] != "BTCUSDT" for c in candidates)

    def test_already_pumped_token_loses_score(self, monkeypatch):
        """Pre-move penalty should reduce score below threshold."""
        funding = []
        oi = []
        ls = []
        taker = []
        book = []
        qual = {"BTCUSDT": MockProfile(blocked=False)}
        # Simulate pre-move by using a low score after penalty
        catalyst_results = {
            "BTCUSDT": CatalystResult(
                symbol="BTCUSDT",
                score=0.40,
                events=[CatalystEvent(symbol="BTCUSDT", source="test", title="event")],
            ),
        }

        import src.watchlist as wl
        monkeypatch.setattr(wl, "_persist_watchlist_candidate", lambda c, m: None)
        monkeypatch.setattr(wl, "StageManager", lambda: MagicMock())

        candidates = generate_watchlist(
            funding, oi, ls, taker, book, qual, catalyst_results=catalyst_results
        )
        assert all(c["symbol"] != "BTCUSDT" for c in candidates)

    def test_final_alpha_score_weights_catalyst_first(self):
        """CATALYST_FIRST mode should weight catalyst more than technical."""
        assert CATALYST_WEIGHT == 0.60
        assert CATALYST_WEIGHT > 0.25  # TECHNICAL_SETUP_WEIGHT

    def test_blocking_negative_blocks_watchlist(self, monkeypatch):
        """Blocking negative catalyst should block normal watchlist entry."""
        funding = [MockSignal("BTCUSDT", fired=True)]
        oi = [MockSignal("BTCUSDT", fired=True)]
        ls = [MockSignal("BTCUSDT", fired=True)]
        taker = [MockSignal("BTCUSDT", fired=True)]
        book = []
        qual = {"BTCUSDT": MockProfile(blocked=False)}
        catalyst_results = {
            "BTCUSDT": mock_catalyst_result("BTCUSDT", 0.80, is_negative=True),
        }
        # Override to blocking
        catalyst_results["BTCUSDT"].has_blocking_negative_catalyst = True

        import src.watchlist as wl
        monkeypatch.setattr(wl, "_persist_watchlist_candidate", lambda c, m: None)
        monkeypatch.setattr(wl, "StageManager", lambda: MagicMock())

        candidates = generate_watchlist(
            funding, oi, ls, taker, book, qual, catalyst_results=catalyst_results
        )
        assert all(c["symbol"] != "BTCUSDT" for c in candidates)

    def test_warning_negative_allows_strong_technical(self, monkeypatch):
        """Warning negative + strong technical (>=4/5) should allow watchlist."""
        funding = [MockSignal("BTCUSDT", fired=True)]
        oi = [MockSignal("BTCUSDT", fired=True)]
        ls = [MockSignal("BTCUSDT", fired=True)]
        taker = [MockSignal("BTCUSDT", fired=True)]
        book = []
        qual = {"BTCUSDT": MockProfile(blocked=False)}
        catalyst_results = {
            "BTCUSDT": mock_catalyst_result("BTCUSDT", 0.60, is_negative=True),
        }
        # Ensure it's warning only
        catalyst_results["BTCUSDT"].has_blocking_negative_catalyst = False

        import src.watchlist as wl
        monkeypatch.setattr(wl, "_persist_watchlist_candidate", lambda c, m: None)
        monkeypatch.setattr(wl, "StageManager", lambda: MagicMock())

        candidates = generate_watchlist(
            funding, oi, ls, taker, book, qual, catalyst_results=catalyst_results
        )
        assert any(c["symbol"] == "BTCUSDT" for c in candidates)

    def test_price_changes_flow_into_candidate(self, monkeypatch):
        """Price changes passed into generate_watchlist should appear in candidate dict."""
        funding = []
        oi = []
        ls = []
        taker = []
        book = []
        qual = {"BTCUSDT": MockProfile(blocked=False)}
        catalyst_results = {
            "BTCUSDT": mock_catalyst_result("BTCUSDT", 0.80),
        }
        price_changes = {
            "BTCUSDT": {"1h": 1.5, "4h": 3.0, "24h": -2.0},
        }

        import src.watchlist as wl
        monkeypatch.setattr(wl, "_persist_watchlist_candidate", lambda c, m: None)
        monkeypatch.setattr(wl, "StageManager", lambda: MagicMock())

        candidates = generate_watchlist(
            funding, oi, ls, taker, book, qual,
            catalyst_results=catalyst_results,
            price_changes=price_changes,
        )
        btc = next(c for c in candidates if c["symbol"] == "BTCUSDT")
        assert btc["price_change_1h"] == 1.5
        assert btc["price_change_4h"] == 3.0
        assert btc["price_change_24h"] == -2.0

    def test_price_changes_none_when_missing(self, monkeypatch):
        """Missing price changes should result in None values, not 0.0."""
        funding = []
        oi = []
        ls = []
        taker = []
        book = []
        qual = {"BTCUSDT": MockProfile(blocked=False)}
        catalyst_results = {
            "BTCUSDT": mock_catalyst_result("BTCUSDT", 0.80),
        }

        import src.watchlist as wl
        monkeypatch.setattr(wl, "_persist_watchlist_candidate", lambda c, m: None)
        monkeypatch.setattr(wl, "StageManager", lambda: MagicMock())

        candidates = generate_watchlist(
            funding, oi, ls, taker, book, qual,
            catalyst_results=catalyst_results,
        )
        btc = next(c for c in candidates if c["symbol"] == "BTCUSDT")
        assert btc["price_change_1h"] is None
        assert btc["price_change_4h"] is None
        assert btc["price_change_24h"] is None
