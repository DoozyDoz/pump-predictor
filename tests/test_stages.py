"""Unit tests for src/stages.py stage state machine."""

import os
import tempfile

# Override DB_PATH before importing stages
os.environ["TELEGRAM_BOT_TOKEN"] = "test:token"
os.environ["TELEGRAM_CHAT_ID"] = "12345"

import pytest
from src.stages import Stage, StageManager


@pytest.fixture
def stage_mgr():
    """Provide a StageManager with a clean in-memory DB."""
    import src.config as cfg
    # Use temp DB for tests
    orig = cfg.DB_PATH
    cfg.DB_PATH = os.path.join(tempfile.gettempdir(), "test_stages_pump.db")
    # Clean up any existing test DB
    if os.path.exists(cfg.DB_PATH):
        os.remove(cfg.DB_PATH)
    from src.db import init_db, db_session
    init_db()
    # Seed token records so FOREIGN KEY constraints pass
    with db_session() as conn:
        for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
            conn.execute(
                "INSERT OR IGNORE INTO tokens (symbol, exchange, market) VALUES (?, 'B', 'spot')",
                (sym,),
            )
    mgr = StageManager()
    yield mgr
    cfg.DB_PATH = orig
    if os.path.exists(cfg.DB_PATH):
        os.remove(cfg.DB_PATH)


class TestStageEnum:
    def test_enum_values(self):
        assert Stage.WATCHLIST.value == "watchlist"
        assert Stage.CONFIRMATION.value == "confirmation"
        assert Stage.ENTRY.value == "entry"
        assert Stage.EXPIRED.value == "expired"

    def test_enum_members(self):
        assert len(Stage) == 4


class TestStageManager:
    def test_add_to_watchlist(self, stage_mgr):
        wl_id = stage_mgr.add_to_watchlist(
            token_id=1, symbol="BTCUSDT", score=2, signals="funding_extreme|oi_divergence"
        )
        assert wl_id is not None
        assert isinstance(wl_id, int)

    def test_add_to_watchlist_twice_updates(self, stage_mgr):
        wl_id1 = stage_mgr.add_to_watchlist(
            token_id=1, symbol="BTCUSDT", score=2, signals="funding_extreme"
        )
        wl_id2 = stage_mgr.add_to_watchlist(
            token_id=1, symbol="BTCUSDT", score=3, signals="oi_divergence"
        )
        # Should return the same ID (update, not insert)
        assert wl_id1 == wl_id2

    def test_promote_to_confirmation(self, stage_mgr):
        wl_id = stage_mgr.add_to_watchlist(
            token_id=1, symbol="BTCUSDT", score=2, signals="funding_extreme"
        )
        stage_mgr.promote_to_confirmation(wl_id, "price action confirmed")
        items = stage_mgr.get_by_stage("confirmation")
        assert len(items) >= 1
        assert items[0]["symbol"] == "BTCUSDT"

    def test_promote_to_entry(self, stage_mgr):
        wl_id = stage_mgr.add_to_watchlist(
            token_id=1, symbol="ETHUSDT", score=3, signals="all"
        )
        stage_mgr.promote_to_confirmation(wl_id, "confirmed")
        stage_mgr.promote_to_entry(wl_id, "entry conditions met")
        items = stage_mgr.get_by_stage("entry")
        assert len(items) >= 1
        assert items[0]["symbol"] == "ETHUSDT"

    def test_expire(self, stage_mgr):
        wl_id = stage_mgr.add_to_watchlist(
            token_id=1, symbol="BTCUSDT", score=1, signals="funding_extreme"
        )
        stage_mgr.expire(wl_id, "no confirmation")
        active = stage_mgr.get_all_active()
        # Expired items should not show in active
        for a in active:
            assert a.get("stage") != "expired"

    def test_get_watchlist_candidates(self, stage_mgr):
        stage_mgr.add_to_watchlist(
            token_id=1, symbol="BTCUSDT", score=2, signals="funding_extreme"
        )
        stage_mgr.add_to_watchlist(
            token_id=2, symbol="ETHUSDT", score=1, signals="oi_divergence"
        )
        candidates = stage_mgr.get_watchlist_candidates()
        assert len(candidates) >= 1

    def test_get_all_active(self, stage_mgr):
        stage_mgr.add_to_watchlist(
            token_id=1, symbol="BTCUSDT", score=2, signals="funding_extreme"
        )
        stage_mgr.add_to_watchlist(
            token_id=2, symbol="ETHUSDT", score=3, signals="oi_divergence"
        )
        active = stage_mgr.get_all_active()
        assert len(active) >= 2

    def test_get_confirmation_candidates(self, stage_mgr):
        """get_confirmation_candidates should return items in confirmation stage."""
        wl_id = stage_mgr.add_to_watchlist(
            token_id=1, symbol="BTCUSDT", score=2, signals="funding_extreme"
        )
        stage_mgr.promote_to_confirmation(wl_id, "confirmed")
        candidates = stage_mgr.get_confirmation_candidates()
        assert len(candidates) >= 1
        assert candidates[0]["symbol"] == "BTCUSDT"

    def test_get_confirmation_candidates_excludes_watchlist(self, stage_mgr):
        """Items still in watchlist should not appear in confirmation results."""
        _ = stage_mgr.add_to_watchlist(
            token_id=1, symbol="BTCUSDT", score=2, signals="funding_extreme"
        )
        # Not promoted to confirmation
        candidates = stage_mgr.get_confirmation_candidates()
        assert all(c["symbol"] != "BTCUSDT" for c in candidates)

    def test_get_confirmation_candidates_excludes_expired(self, stage_mgr):
        """Expired items should not appear in confirmation results."""
        wl_id = stage_mgr.add_to_watchlist(
            token_id=1, symbol="BTCUSDT", score=2, signals="funding_extreme"
        )
        stage_mgr.promote_to_confirmation(wl_id, "confirmed")
        stage_mgr.expire(wl_id, "expired")
        candidates = stage_mgr.get_confirmation_candidates()
        assert all(c["symbol"] != "BTCUSDT" for c in candidates)

    def test_promote_non_existent_watchlist_no_error(self, stage_mgr):
        """Promoting a non-existent watchlist ID should not raise."""
        # Should silently no-op
        stage_mgr.promote_to_confirmation(99999, "no such id")
        stage_mgr.promote_to_entry(99999, "no such id")

    def test_promote_to_entry_non_confirmation_stage(self, stage_mgr):
        """Promoting to entry from watchlist (not confirmation) should silently skip."""
        wl_id = stage_mgr.add_to_watchlist(
            token_id=1, symbol="BTCUSDT", score=2, signals="funding_extreme"
        )
        # Try to promote directly to entry from watchlist (skipping confirmation)
        stage_mgr.promote_to_entry(wl_id, "direct entry")
        # Should NOT be in entry stage
        entries = stage_mgr.get_by_stage("entry")
        assert all(e["symbol"] != "BTCUSDT" for e in entries)

    def test_expire_stale_watchlist(self, stage_mgr, monkeypatch):
        """expire_stale should expire watchlist items past WATCHLIST_TTL_HOURS."""
        # WATCHLIST_TTL_HOURS is imported at module level in stages.py as
        # 'from src.config import WATCHLIST_TTL_HOURS', so we must patch
        # the attribute on stages module, not on config.
        monkeypatch.setattr("src.stages.WATCHLIST_TTL_HOURS", 0)
        _ = stage_mgr.add_to_watchlist(
            token_id=1, symbol="BTCUSDT", score=2, signals="funding_extreme"
        )
        stage_mgr.expire_stale()
        active = stage_mgr.get_all_active()
        assert all(a["symbol"] != "BTCUSDT" for a in active)

    def test_expire_stale_confirmation(self, stage_mgr, monkeypatch):
        """expire_stale should expire confirmation items past CONFIRMATION_TTL_HOURS."""
        monkeypatch.setattr("src.stages.CONFIRMATION_TTL_HOURS", 0)
        wl_id = stage_mgr.add_to_watchlist(
            token_id=1, symbol="BTCUSDT", score=2, signals="funding_extreme"
        )
        stage_mgr.promote_to_confirmation(wl_id, "confirmed")
        stage_mgr.expire_stale()
        conf_items = stage_mgr.get_by_stage("confirmation")
        assert all(c["symbol"] != "BTCUSDT" for c in conf_items)

    def test_expire_stale_no_effect_on_recent(self, stage_mgr):
        """Items within TTL should not be expired."""
        _ = stage_mgr.add_to_watchlist(
            token_id=1, symbol="BTCUSDT", score=2, signals="funding_extreme"
        )
        stage_mgr.expire_stale()
        # Item was just added, should still be active
        active = stage_mgr.get_all_active()
        assert any(a["symbol"] == "BTCUSDT" for a in active)

    def test_expire_stale_empty_db_no_error(self, stage_mgr):
        """expire_stale on empty database should not raise."""
        stage_mgr.expire_stale()
