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

CREATE TABLE IF NOT EXISTS catalyst_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    source_url TEXT DEFAULT '',
    title TEXT DEFAULT '',
    event_type TEXT DEFAULT '',
    published_at TEXT DEFAULT '',
    event_time TEXT,
    final_score REAL DEFAULT 0,
    metadata_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_catalyst_symbol_type_pub ON catalyst_events(symbol, event_type, published_at);
CREATE INDEX IF NOT EXISTS idx_catalyst_symbol_created ON catalyst_events(symbol, created_at);

CREATE TABLE IF NOT EXISTS scan_status (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phase TEXT NOT NULL,  -- 'phase1' or 'phase2'
    status TEXT NOT NULL,  -- 'alerts_found', 'no_setups', 'suppressed', 'api_failure', 'no_watchlist', 'no_confirmations', 'error'
    detail TEXT DEFAULT '',
    candidate_symbols TEXT DEFAULT '[]',
    alert_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_scan_status_phase_ts ON scan_status(phase, created_at DESC);
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

        # Watchlist catalyst columns (safe migration)
        for col, col_def in [
            ("catalyst_score", "REAL DEFAULT 0"),
            ("catalyst_event_type", "TEXT DEFAULT ''"),
            ("catalyst_title", "TEXT DEFAULT ''"),
            ("catalyst_source", "TEXT DEFAULT ''"),
            ("catalyst_published_at", "TEXT DEFAULT ''"),
            ("final_alpha_score", "REAL DEFAULT 0"),
            ("priority", "TEXT DEFAULT ''"),
            ("setup_type", "TEXT DEFAULT ''"),
            # Two-tier negative catalyst fields
            ("is_negative_catalyst", "INTEGER DEFAULT 0"),
            ("has_blocking_negative_catalyst", "INTEGER DEFAULT 0"),
            ("negative_catalyst_types", "TEXT DEFAULT '[]'"),
            ("negative_catalyst_severities", "TEXT DEFAULT '[]'"),
            ("negative_catalyst_reasons", "TEXT DEFAULT '[]'"),
            ("catalyst_event_ids", "TEXT DEFAULT '[]'"),
            # Price reaction fields
            ("price_change_1h", "REAL"),
            ("price_change_4h", "REAL"),
            ("price_change_24h", "REAL"),
        ]:
            try:
                conn.execute(f"ALTER TABLE watchlist ADD COLUMN {col} {col_def}")
            except Exception:
                pass  # column already exists

        # Backtest results catalyst columns (safe migration)
        for col, col_def in [
            ("catalyst_only_alerts", "INTEGER DEFAULT 0"),
            ("combined_alerts", "INTEGER DEFAULT 0"),
            ("confirmed_after_strong_catalyst", "INTEGER DEFAULT 0"),
            ("catalyst_precision", "REAL DEFAULT 0"),
            ("avg_r_by_catalyst_bucket", "TEXT DEFAULT '{}'"),
        ]:
            try:
                conn.execute(f"ALTER TABLE backtest_results ADD COLUMN {col} {col_def}")
            except Exception:
                pass  # column already exists


def get_last_scan_status(phase: str) -> dict | None:
    """Return the most recent scan_status row for a given phase, or None."""
    with db_session() as conn:
        row = conn.execute(
            "SELECT status, detail, candidate_symbols, alert_count FROM scan_status "
            "WHERE phase = ? ORDER BY created_at DESC LIMIT 1",
            (phase,),
        ).fetchone()
        if row:
            return {
                "status": row["status"],
                "detail": row["detail"],
                "candidate_symbols": json.loads(row["candidate_symbols"]) if row["candidate_symbols"] else [],
                "alert_count": row["alert_count"],
            }
        return None


def write_scan_status(phase: str, status: str, detail: str = "",
                      candidate_symbols: list[str] | None = None,
                      alert_count: int = 0):
    """Persist a scan status row."""
    import json
    with db_session() as conn:
        conn.execute(
            "INSERT INTO scan_status (phase, status, detail, candidate_symbols, alert_count) "
            "VALUES (?, ?, ?, ?, ?)",
            (phase, status, detail,
             json.dumps(candidate_symbols or []),
             alert_count),
        )
