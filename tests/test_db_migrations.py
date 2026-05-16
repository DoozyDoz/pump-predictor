"""Unit tests for DB catalyst-related migrations."""

import os
import tempfile

os.environ["TELEGRAM_BOT_TOKEN"] = "test:token"
os.environ["TELEGRAM_CHAT_ID"] = "12345"

import src.config as cfg


class TestDBMigrations:
    def test_catalyst_events_table_exists(self):
        """After init_db(), catalyst_events table should exist."""
        orig = cfg.DB_PATH
        cfg.DB_PATH = os.path.join(tempfile.gettempdir(), "test_db_catalyst.db")
        if os.path.exists(cfg.DB_PATH):
            os.remove(cfg.DB_PATH)
        try:
            from src.db import init_db, db_session

            init_db()
            with db_session() as conn:
                conn.execute("SELECT 1 FROM catalyst_events LIMIT 1")
        finally:
            cfg.DB_PATH = orig
            if os.path.exists(cfg.DB_PATH):
                os.remove(cfg.DB_PATH)

    def test_watchlist_new_columns_exist(self):
        """After init_db(), watchlist should have catalyst columns."""
        orig = cfg.DB_PATH
        cfg.DB_PATH = os.path.join(tempfile.gettempdir(), "test_db_watchlist_cols.db")
        if os.path.exists(cfg.DB_PATH):
            os.remove(cfg.DB_PATH)
        try:
            from src.db import init_db, db_session

            init_db()
            with db_session() as conn:
                cols = conn.execute("PRAGMA table_info(watchlist)").fetchall()
                col_names = [c[1] for c in cols]
                for expected in [
                    "catalyst_score",
                    "catalyst_event_type",
                    "catalyst_title",
                    "catalyst_source",
                    "catalyst_published_at",
                    "final_alpha_score",
                    "priority",
                    "setup_type",
                ]:
                    assert expected in col_names, (
                        f"Expected column {expected} not found in watchlist"
                    )
        finally:
            cfg.DB_PATH = orig
            if os.path.exists(cfg.DB_PATH):
                os.remove(cfg.DB_PATH)

    def test_migration_does_not_drop_data(self):
        """Calling init_db on an existing DB should preserve data."""
        orig = cfg.DB_PATH
        cfg.DB_PATH = os.path.join(tempfile.gettempdir(), "test_db_preserve.db")
        if os.path.exists(cfg.DB_PATH):
            os.remove(cfg.DB_PATH)
        try:
            from src.db import init_db, db_session

            init_db()
            with db_session() as conn:
                conn.execute(
                    "INSERT INTO tokens (symbol, exchange, market) VALUES (?, 'B', 'spot')",
                    ("TESTTOKEN",),
                )
                row = conn.execute(
                    "SELECT id FROM tokens WHERE symbol = ?", ("TESTTOKEN",)
                ).fetchone()
                token_id = row[0]
                conn.execute(
                    "INSERT INTO watchlist (token_id, symbol, score, signals_fired) VALUES (?, ?, 2, 'funding_extreme')",
                    (token_id, "TESTTOKEN"),
                )

            # Re-run init_db (migration)
            init_db()

            with db_session() as conn:
                row = conn.execute(
                    "SELECT symbol, score FROM watchlist WHERE symbol = ?",
                    ("TESTTOKEN",),
                ).fetchone()
                assert row is not None
                assert row["symbol"] == "TESTTOKEN"
                assert row["score"] == 2
        finally:
            cfg.DB_PATH = orig
            if os.path.exists(cfg.DB_PATH):
                os.remove(cfg.DB_PATH)
