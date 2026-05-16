"""Unit tests for src/watchlist.py watchlist generation."""

import os
os.environ["TELEGRAM_BOT_TOKEN"] = "test:token"
os.environ["TELEGRAM_CHAT_ID"] = "12345"

from src.config import WATCHLIST_THRESHOLD


# Mock signal classes
class MockSignal:
    def __init__(self, symbol, fired=True):
        self.symbol = symbol
        self.fired = fired


class MockProfile:
    def __init__(self, blocked=False, catalyst_boost=0.0):
        self.blocked = blocked
        self.catalyst_boost = catalyst_boost


class TestGenerateWatchlist:
    def test_lower_threshold(self):
        """Verify watchlist uses lower threshold (1 instead of 2)."""
        assert WATCHLIST_THRESHOLD == 1, (
            f"Expected WATCHLIST_THRESHOLD=1, got {WATCHLIST_THRESHOLD}"
        )

    def test_basic_candidate(self, monkeypatch):
        """A symbol with >= WATCHLIST_THRESHOLD signals should become a candidate."""
        from src.watchlist import generate_watchlist

        funding = [MockSignal("BTCUSDT", fired=True)]
        oi = []
        ls = []
        taker = []
        book = []
        # Must have catalyst_boost >= 0.5 to satisfy min quant signal rule
        qual = {"BTCUSDT": MockProfile(blocked=False, catalyst_boost=0.5)}

        # Mock DB operations to avoid actual DB writes
        import src.watchlist as wl
        monkeypatch.setattr(wl, "_persist_watchlist_candidate", lambda c, m: None)

        # Mock StageManager to avoid DB
        class MockStageMgr:
            def add_to_watchlist(self, **kwargs):
                return 1

        monkeypatch.setattr(wl, "StageManager", lambda: MockStageMgr())

        candidates = generate_watchlist(funding, oi, ls, taker, book, qual)
        assert len(candidates) > 0
        assert any(c["symbol"] == "BTCUSDT" for c in candidates)

    def test_blocked_filter(self, monkeypatch):
        """Blocked profiles should be excluded."""
        from src.watchlist import generate_watchlist

        funding = [MockSignal("BTCUSDT", fired=True)]
        oi = []
        ls = []
        taker = []
        book = []
        qual = {"BTCUSDT": MockProfile(blocked=True)}

        import src.watchlist as wl

        class MockStageMgr:
            def add_to_watchlist(self, **kwargs):
                return 1

        monkeypatch.setattr(wl, "StageManager", lambda: MockStageMgr())
        monkeypatch.setattr(wl, "_persist_watchlist_candidate", lambda c, m: None)

        candidates = generate_watchlist(funding, oi, ls, taker, book, qual)
        assert all(c["symbol"] != "BTCUSDT" for c in candidates)

    def test_score_below_threshold_excluded(self, monkeypatch):
        """Score == WATCHLIST_THRESHOLD - 1 should be excluded."""
        from src.watchlist import generate_watchlist

        # No fired signals -> score 0, below threshold 1
        funding = [MockSignal("BTCUSDT", fired=False)]
        oi = []
        ls = []
        taker = []
        book = []
        qual = {"BTCUSDT": MockProfile(blocked=False)}

        import src.watchlist as wl

        class MockStageMgr:
            def add_to_watchlist(self, **kwargs):
                return 1

        monkeypatch.setattr(wl, "StageManager", lambda: MockStageMgr())
        monkeypatch.setattr(wl, "_persist_watchlist_candidate", lambda c, m: None)

        candidates = generate_watchlist(funding, oi, ls, taker, book, qual)
        assert all(c["symbol"] != "BTCUSDT" for c in candidates)

    def test_needs_strong_signal(self, monkeypatch):
        """Must have at least one strong derivative signal."""
        from src.watchlist import generate_watchlist

        # Only taker and book fired (not strong signals)
        funding = []
        oi = []
        ls = []
        taker = [MockSignal("BTCUSDT", fired=True)]
        book = [MockSignal("BTCUSDT", fired=True)]
        qual = {"BTCUSDT": MockProfile(blocked=False)}

        import src.watchlist as wl

        class MockStageMgr:
            def add_to_watchlist(self, **kwargs):
                return 1

        monkeypatch.setattr(wl, "StageManager", lambda: MockStageMgr())
        monkeypatch.setattr(wl, "_persist_watchlist_candidate", lambda c, m: None)

        candidates = generate_watchlist(funding, oi, ls, taker, book, qual)
        assert all(c["symbol"] != "BTCUSDT" for c in candidates)
