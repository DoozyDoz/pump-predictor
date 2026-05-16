import sqlite3
from contextlib import contextmanager
from src.config import DB_PATH
import os


SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL DEFAULT 'binance',
    market TEXT NOT NULL DEFAULT 'spot',
    in_universe BOOLEAN DEFAULT TRUE,
    last_volume_check REAL,
    last_volume_ok BOOLEAN DEFAULT TRUE,
    added_at TEXT DEFAULT (datetime('now')),
    UNIQUE(symbol, exchange, market)
);

CREATE TABLE IF NOT EXISTS funding_rates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id INTEGER NOT NULL REFERENCES tokens(id),
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    funding_rate REAL NOT NULL,
    funding_rate_daily REAL,
    open_interest REAL,
    timestamp TEXT NOT NULL,
    fetched_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_funding_token_ts ON funding_rates(token_id, timestamp);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id INTEGER NOT NULL REFERENCES tokens(id),
    signal_id INTEGER REFERENCES signals(id),
    pump_score INTEGER NOT NULL,
    fired_signals TEXT NOT NULL,
    stage TEXT DEFAULT 'entry',
    alert_ts TEXT NOT NULL DEFAULT (datetime('now')),
    reviewed BOOLEAN DEFAULT FALSE,
    trade_placed BOOLEAN DEFAULT FALSE,
    skip_reason TEXT
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER REFERENCES alerts(id),
    token_id INTEGER NOT NULL REFERENCES tokens(id),
    entry_price REAL NOT NULL,
    entry_ts TEXT NOT NULL,
    exit_price REAL,
    exit_ts TEXT,
    exit_reason TEXT,  -- 'tp_15', 'tp_25', 'trailing', 'stop_loss', 'manual'
    pnl_pct REAL,
    position_size_usd REAL NOT NULL,
    fired_signals TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ohlcv (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    interval TEXT NOT NULL,  -- '4h', '1d'
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    timestamp TEXT NOT NULL,
    UNIQUE(symbol, exchange, interval, timestamp)
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id INTEGER REFERENCES tokens(id),
    symbol TEXT NOT NULL,
    entry_price REAL NOT NULL,
    entry_ts TEXT NOT NULL,
    exit_price REAL,
    exit_ts TEXT,
    exit_reason TEXT,  -- 'manual', 'tp1', 'tp2', 'trailing', 'stop_loss', 'timeout'
    pnl_pct REAL,
    position_size_usd REAL NOT NULL DEFAULT 100.0,
    status TEXT NOT NULL DEFAULT 'active',  -- 'active', 'tp1_hit', 'closed'
    tp1 REAL, tp2 REAL, stop REAL, trail_peak REAL,
    tp1_filled REAL DEFAULT 0,  -- fraction filled at TP1 (0 or 0.5)
    realized_pnl REAL DEFAULT 0,  -- accumulated P&L from partial fills
    chat_id TEXT NOT NULL,
    alert_triggered_at TEXT
);

CREATE TABLE IF NOT EXISTS signal_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    signal_type TEXT NOT NULL,  -- 'funding_rate', 'oi_value', 'ls_ratio', 'taker_ratio'
    value REAL NOT NULL,
    snapshot_ts TEXT NOT NULL,  -- ISO date (YYYY-MM-DD)
    UNIQUE(symbol, signal_type, snapshot_ts)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_lookup
    ON signal_snapshots(symbol, signal_type, snapshot_ts);

CREATE TABLE IF NOT EXISTS backtest_results (
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
    profit_factor REAL,
    avg_win_pct REAL,
    avg_loss_pct REAL,
    max_drawdown_pct REAL
);

CREATE TABLE IF NOT EXISTS watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id INTEGER NOT NULL REFERENCES tokens(id),
    symbol TEXT NOT NULL,
    score INTEGER NOT NULL,
    signals_fired TEXT NOT NULL,
    catalyst_boost REAL DEFAULT 0.0,
    added_ts TEXT NOT NULL DEFAULT (datetime('now')),
    expired BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS stage_progression (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    watchlist_id INTEGER REFERENCES watchlist(id),
    token_id INTEGER NOT NULL REFERENCES tokens(id),
    stage TEXT NOT NULL,
    entered_ts TEXT NOT NULL DEFAULT (datetime('now')),
    promoted_ts TEXT,
    expired_ts TEXT,
    reason TEXT
);
"""


def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db_session():
    conn = get_db()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db_session() as conn:
        conn.executescript(SCHEMA)
        # Migrations for columns added after initial schema
        for col, col_def in [
            ("tp1_filled", "REAL DEFAULT 0"),
            ("realized_pnl", "REAL DEFAULT 0"),
            ("stage", "TEXT DEFAULT 'entry'"),
        ]:
            try:
                conn.execute(f"ALTER TABLE alerts ADD COLUMN {col} {col_def}")
            except Exception:
                pass  # column already exists
        for col, col_def in [
            ("stage", "TEXT DEFAULT 'entry'"),
        ]:
            try:
                conn.execute(f"ALTER TABLE paper_trades ADD COLUMN {col} {col_def}")
            except Exception:
                pass  # column already exists
