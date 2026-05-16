"""DB roundtrip tests for catalyst fields."""

import json
import os
import tempfile

os.environ["TELEGRAM_BOT_TOKEN"] = "test:token"
os.environ["TELEGRAM_CHAT_ID"] = "12345"

import src.config as cfg
from src.db import init_db, db_session
from src.stages import StageManager
from src.watchlist import _persist_watchlist_candidate


class TestDBRoundtrip:
    def test_catalyst_fields_survive_roundtrip(self):
        """All catalyst fields must survive save and reload from DB."""
        orig_db = cfg.DB_PATH
        cfg.DB_PATH = os.path.join(tempfile.gettempdir(), "test_roundtrip.db")
        if os.path.exists(cfg.DB_PATH):
            os.remove(cfg.DB_PATH)
        try:
            init_db()
            stage_mgr = StageManager()
            candidate = {
                "symbol": "BTCUSDT",
                "score": 3,
                "fired_signals": "funding_extreme|oi_divergence",
                "catalyst_boost": 0.6,
                "adjusted_score": 3,
                "catalyst_score": 0.85,
                "setup_type": "CATALYST_WATCH",
                "priority": "URGENT_CATALYST",
                "final_alpha_score": 0.75,
                "catalyst_event_type": "major_exchange_listing",
                "catalyst_title": "Binance Listing",
                "catalyst_source": "binance",
                "catalyst_published_at": "2024-01-01T00:00:00",
                "is_negative_catalyst": True,
                "has_blocking_negative_catalyst": False,
                "negative_catalyst_types": ["token_unlock_large"],
                "negative_catalyst_severities": ["warning"],
                "negative_catalyst_reasons": ["negative event type"],
                "catalyst_event_ids": [],
                "price_change_1h": 2.5,
                "price_change_4h": 5.0,
                "price_change_24h": -1.0,
            }
            _persist_watchlist_candidate(candidate, stage_mgr)

            # Reload via StageManager
            rows = stage_mgr.get_watchlist_candidates()
            assert len(rows) == 1
            row = rows[0]
            assert row["symbol"] == "BTCUSDT"
            assert abs(row["catalyst_score"] - 0.85) < 0.01
            assert row["catalyst_event_type"] == "major_exchange_listing"
            assert row["catalyst_title"] == "Binance Listing"
            assert row["catalyst_source"] == "binance"
            assert row["catalyst_published_at"] == "2024-01-01T00:00:00"
            assert abs(row["final_alpha_score"] - 0.75) < 0.01
            assert row["priority"] == "URGENT_CATALYST"
            assert bool(row["is_negative_catalyst"]) is True
            assert bool(row["has_blocking_negative_catalyst"]) is False
            assert json.loads(row["negative_catalyst_types"]) == ["token_unlock_large"]
            assert json.loads(row["negative_catalyst_severities"]) == ["warning"]
            assert json.loads(row["negative_catalyst_reasons"]) == ["negative event type"]
            assert abs(row["price_change_1h"] - 2.5) < 0.01
            assert abs(row["price_change_4h"] - 5.0) < 0.01
            assert abs(row["price_change_24h"] - (-1.0)) < 0.01
        finally:
            cfg.DB_PATH = orig_db
            if os.path.exists(cfg.DB_PATH):
                os.remove(cfg.DB_PATH)

    def test_null_price_changes_survive(self):
        """None price-change values must survive as NULL in DB."""
        orig_db = cfg.DB_PATH
        cfg.DB_PATH = os.path.join(tempfile.gettempdir(), "test_roundtrip_null.db")
        if os.path.exists(cfg.DB_PATH):
            os.remove(cfg.DB_PATH)
        try:
            init_db()
            stage_mgr = StageManager()
            candidate = {
                "symbol": "ETHUSDT",
                "score": 2,
                "fired_signals": "funding_extreme",
                "catalyst_boost": 0.0,
                "adjusted_score": 2,
                "catalyst_score": 0.0,
                "setup_type": "TECHNICAL",
                "priority": "",
                "final_alpha_score": 0.3,
                "catalyst_event_type": "",
                "catalyst_title": "",
                "catalyst_source": "",
                "catalyst_published_at": "",
                "is_negative_catalyst": False,
                "has_blocking_negative_catalyst": False,
                "negative_catalyst_types": [],
                "negative_catalyst_severities": [],
                "negative_catalyst_reasons": [],
                "catalyst_event_ids": [],
                "price_change_1h": None,
                "price_change_4h": None,
                "price_change_24h": None,
            }
            _persist_watchlist_candidate(candidate, stage_mgr)

            rows = stage_mgr.get_watchlist_candidates()
            assert len(rows) == 1
            row = rows[0]
            assert row["price_change_1h"] is None
            assert row["price_change_4h"] is None
            assert row["price_change_24h"] is None
        finally:
            cfg.DB_PATH = orig_db
            if os.path.exists(cfg.DB_PATH):
                os.remove(cfg.DB_PATH)
