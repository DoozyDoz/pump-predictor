"""Unit tests for src/db.py database initialization."""

import os
import tempfile

os.environ["TELEGRAM_BOT_TOKEN"] = "test:token"
os.environ["TELEGRAM_CHAT_ID"] = "12345"

import src.config as cfg


class TestDBInit:
    def test_init_db_creates_tables(self):
        """init_db() should create all expected tables without error."""
        orig = cfg.DB_PATH
        cfg.DB_PATH = os.path.join(tempfile.gettempdir(), "test_db_init.db")
        if os.path.exists(cfg.DB_PATH):
            os.remove(cfg.DB_PATH)
        try:
            from src.db import init_db, db_session

            init_db()
            with db_session() as conn:
                # Verify expected tables exist
                tables = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                ).fetchall()
                table_names = [r[0] for r in tables]
                for expected in [
                    "alerts",
                    "backtest_results",
                    "funding_rates",
                    "paper_trades",
                    "signal_snapshots",
                    "stage_progression",
                    "tokens",
                    "trades",
                    "watchlist",
                ]:
                    assert expected in table_names, (
                        f"Expected table {expected} not found"
                    )
        finally:
            cfg.DB_PATH = orig
            if os.path.exists(cfg.DB_PATH):
                os.remove(cfg.DB_PATH)

    def test_init_db_idempotent(self):
        """Calling init_db() twice should not raise (idempotent)."""
        orig = cfg.DB_PATH
        cfg.DB_PATH = os.path.join(tempfile.gettempdir(), "test_db_idempotent.db")
        if os.path.exists(cfg.DB_PATH):
            os.remove(cfg.DB_PATH)
        try:
            from src.db import init_db

            init_db()
            # Second call should not raise
            init_db()
        finally:
            cfg.DB_PATH = orig
            if os.path.exists(cfg.DB_PATH):
                os.remove(cfg.DB_PATH)
