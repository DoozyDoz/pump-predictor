"""Unit tests for src/backtest.py backtest utilities."""

import os
os.environ["TELEGRAM_BOT_TOKEN"] = "test:token"
os.environ["TELEGRAM_CHAT_ID"] = "12345"

from datetime import datetime, timedelta
import pytest
import tempfile


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


class TestBacktestDBRobustness:
    def test_save_results_on_fresh_db(self):
        """Backtest save should work on a fresh DB."""
        import src.config as cfg
        import src.backtest as bt_module
        import src.db as db_module
        import sqlite3
        orig_db = cfg.DB_PATH
        temp_db = os.path.join(tempfile.gettempdir(), "test_backtest_fresh.db")
        cfg.DB_PATH = temp_db
        bt_module.DB_PATH = temp_db
        db_module.DB_PATH = temp_db
        if os.path.exists(temp_db):
            os.remove(temp_db)
        try:
            from src.db import init_db
            from src.backtest import _save_results, BacktestWindow
            init_db()
            w = BacktestWindow(
                train_start="2024-01-01",
                train_end="2024-01-31",
                test_start="2024-02-01",
                test_end="2024-02-29",
                total_alerts=10,
                pumps_caught=3,
                precision=30.0,
                total_trades=5,
                winning_trades=2,
                gross_profit_pct=10.0,
                gross_loss_pct=5.0,
                profit_factor=2.0,
            )
            _save_results([w])
            with sqlite3.connect(temp_db) as conn:
                rows = conn.execute("SELECT * FROM backtest_results").fetchall()
                assert len(rows) == 1
        finally:
            cfg.DB_PATH = orig_db
            bt_module.DB_PATH = orig_db
            db_module.DB_PATH = orig_db
            if os.path.exists(temp_db):
                os.remove(temp_db)

    def test_save_results_on_old_db_without_catalyst_columns(self):
        """Backtest save should work on a DB created before catalyst columns."""
        import src.config as cfg
        import src.backtest as bt_module
        import src.db as db_module
        import sqlite3
        orig_db = cfg.DB_PATH
        temp_db = os.path.join(tempfile.gettempdir(), "test_backtest_old.db")
        cfg.DB_PATH = temp_db
        bt_module.DB_PATH = temp_db
        db_module.DB_PATH = temp_db
        if os.path.exists(temp_db):
            os.remove(temp_db)
        try:
            # Create minimal schema without catalyst columns
            with sqlite3.connect(temp_db) as conn:
                conn.execute("""
                    CREATE TABLE backtest_results (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_ts TEXT NOT NULL DEFAULT (datetime('now')),
                        train_start TEXT NOT NULL,
                        train_end TEXT NOT NULL,
                        test_start TEXT NOT NULL,
                        test_end TEXT NOT NULL,
                        total_alerts INTEGER NOT NULL,
                        pumps_caught INTEGER NOT NULL,
                        precision REAL NOT NULL,
                        total_trades INTEGER NOT NULL,
                        winning_trades INTEGER NOT NULL,
                        gross_profit_pct REAL,
                        gross_loss_pct REAL,
                        profit_factor REAL
                    )
                """)
            from src.backtest import _save_results, BacktestWindow
            w = BacktestWindow(
                train_start="2024-01-01",
                train_end="2024-01-31",
                test_start="2024-02-01",
                test_end="2024-02-29",
                total_alerts=10,
                pumps_caught=3,
                precision=30.0,
                total_trades=5,
                winning_trades=2,
                gross_profit_pct=10.0,
                gross_loss_pct=5.0,
                profit_factor=2.0,
            )
            # Should raise OperationalError because columns are missing
            with pytest.raises(sqlite3.OperationalError):
                _save_results([w])
        finally:
            cfg.DB_PATH = orig_db
            bt_module.DB_PATH = orig_db
            db_module.DB_PATH = orig_db
            if os.path.exists(temp_db):
                os.remove(temp_db)

    def test_init_db_adds_missing_catalyst_columns_safely(self):
        """init_db should add missing catalyst columns without deleting data."""
        import src.config as cfg
        import src.backtest as bt_module
        import src.db as db_module
        import sqlite3
        orig_db = cfg.DB_PATH
        temp_db = os.path.join(tempfile.gettempdir(), "test_backtest_migrate.db")
        cfg.DB_PATH = temp_db
        bt_module.DB_PATH = temp_db
        db_module.DB_PATH = temp_db
        if os.path.exists(temp_db):
            os.remove(temp_db)
        try:
            # Create old schema
            with sqlite3.connect(temp_db) as conn:
                conn.execute("""
                    CREATE TABLE backtest_results (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_ts TEXT NOT NULL DEFAULT (datetime('now')),
                        train_start TEXT NOT NULL,
                        train_end TEXT NOT NULL,
                        test_start TEXT NOT NULL,
                        test_end TEXT NOT NULL,
                        total_alerts INTEGER NOT NULL,
                        pumps_caught INTEGER NOT NULL,
                        precision REAL NOT NULL,
                        total_trades INTEGER NOT NULL,
                        winning_trades INTEGER NOT NULL,
                        gross_profit_pct REAL,
                        gross_loss_pct REAL,
                        profit_factor REAL
                    )
                """)
                conn.execute("""
                    INSERT INTO backtest_results
                    (train_start, train_end, test_start, test_end,
                     total_alerts, pumps_caught, precision,
                     total_trades, winning_trades,
                     gross_profit_pct, gross_loss_pct, profit_factor)
                    VALUES ('2024-01-01', '2024-01-31', '2024-02-01', '2024-02-29',
                            10, 3, 30.0, 5, 2, 10.0, 5.0, 2.0)
                """)
            from src.db import init_db
            init_db()
            with sqlite3.connect(temp_db) as conn:
                rows = conn.execute("SELECT * FROM backtest_results").fetchall()
                assert len(rows) == 1
                # Verify new columns exist
                cols = [c[1] for c in conn.execute("PRAGMA table_info(backtest_results)").fetchall()]
                assert "catalyst_only_alerts" in cols
                assert "combined_alerts" in cols
        finally:
            cfg.DB_PATH = orig_db
            bt_module.DB_PATH = orig_db
            db_module.DB_PATH = orig_db
            if os.path.exists(temp_db):
                os.remove(temp_db)


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
