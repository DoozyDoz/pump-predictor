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

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id INTEGER NOT NULL REFERENCES tokens(id),
    run_ts TEXT NOT NULL DEFAULT (datetime('now')),
    wallet_growth_pct REAL,
    wallet_growth_fired BOOLEAN,
    funding_percentile REAL,
    funding_cross_sectional_pct REAL,
    funding_fired BOOLEAN,
    cex_net_outflow_std REAL,
    cex_outflow_ratio REAL,
    cex_fired BOOLEAN,
    pump_score INTEGER GENERATED ALWAYS AS (
        COALESCE(wallet_growth_fired, 0) +
        COALESCE(funding_fired, 0) +
        COALESCE(cex_fired, 0)
    ) VIRTUAL,
    alert_triggered BOOLEAN GENERATED ALWAYS AS (
        COALESCE(wallet_growth_fired, 0) +
        COALESCE(funding_fired, 0) +
        COALESCE(cex_fired, 0) >= 2
    ) VIRTUAL
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id INTEGER NOT NULL REFERENCES tokens(id),
    signal_id INTEGER REFERENCES signals(id),
    pump_score INTEGER NOT NULL,
    fired_signals TEXT NOT NULL,  -- JSON array of signal names
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
