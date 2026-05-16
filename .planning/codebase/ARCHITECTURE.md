# Architecture

**Analysis Date:** 2026-05-16

## Pattern Overview

**Overall:** Modular monolith — single Python codebase with clear layer separation, no microservices or serverless functions.

**Key Characteristics:**
- CLI-driven batch pipeline with optional long-lived Telegram bot daemon
- Three-phase staged workflow (watchlist -> confirmation -> entry) with legacy immediate-alert fallback
- SQLite local database with WAL mode for concurrency
- All external data fetched via public REST APIs (no authenticated trading APIs)
- Paper-only trading mode enforced by default

## Layers

**CLI / Entry Layer:**
- Purpose: Command parsing and dispatch
- Location: `src/main.py`
- Contains: `argparse` subcommands, thin wrappers around pipeline and bot functions
- Depends on: Pipeline, DB init, universe refresh, backtest modules
- Used by: Cron jobs (`run_daily.sh`), systemd service (`run_bot.sh`), manual execution

**Pipeline Layer:**
- Purpose: Orchestrate daily signal computation and staged workflow progression
- Location: `src/pipeline.py`
- Contains: `run_daily()`, `run_phase1_watchlist()`, `run_phase2_confirmation()`, `run_phase3_entry()`
- Depends on: signals, qualitative, watchlist, stages, regime, risk, notify, catalysts, db, binance
- Used by: CLI, Telegram bot scan command, backtest engine

**Signal Computation Layer:**
- Purpose: Compute 5 quantitative signals per token with percentile and cross-sectional ranking
- Location: `src/signals.py`
- Contains: `compute_all_funding_signals()`, `compute_oi_divergence_signal()`, `compute_ls_ratio_signal()`, `compute_taker_ratio_signal()`, `compute_order_book_signal()`
- Depends on: `src/binance.py` for raw data, `src/snapshots.py` for local historical percentile history
- Used by: Pipeline, backtest engine

**Data Access Layer:**
- Purpose: External API clients and local DB access
- Location: `src/binance.py`, `src/db.py`, `src/snapshots.py`, `src/coinglass.py`, `src/dune_client.py`
- Contains: Rate-limited HTTP wrappers, SQLite schema and session context manager, signal snapshot storage
- Depends on: `requests`, `sqlite3`, `numpy`
- Used by: All upstream layers

**State Management Layer:**
- Purpose: Persist and transition workflow stages (watchlist -> confirmation -> entry)
- Location: `src/stages.py`
- Contains: `StageManager` class, `Stage` enum
- Depends on: `src/db.py`
- Used by: Pipeline, confirmation, watchlist generation, backtest

**Qualitative / Catalyst Layer:**
- Purpose: Enrich quantitative signals with external event data and news scoring
- Location: `src/qualitative.py`, `src/catalysts.py`
- Contains: `TokenQualitativeProfile`, `CatalystScorer`, `CatalystEvent`, `fetch_catalyst_data()`
- Depends on: Public APIs (DeFiLlama, Snapshot, GitHub, CryptoPanic, CoinMarketCal)
- Used by: Pipeline Phase 1 and Phase 2

**Risk & Regime Layer:**
- Purpose: ATR-based position sizing and market regime gating
- Location: `src/risk.py`, `src/regime.py`
- Contains: `compute_atr()`, `position_size()`, `detect_regime()`, `is_suppressed()`
- Depends on: `src/binance.py`
- Used by: Pipeline Phase 3 entry, backtest

**Notification Layer:**
- Purpose: Format and send Telegram messages for each workflow stage
- Location: `src/notify.py`
- Contains: `TelegramNotifier` with stage-specific formatters
- Depends on: `requests`, config constants
- Used by: Pipeline, bot

**Bot Layer:**
- Purpose: Long-lived polling daemon for paper trade tracking and on-demand scans
- Location: `src/bot.py`
- Contains: `run_bot()`, position open/close, price checking, command handlers
- Depends on: DB, config, pipeline phases (lazy-imported)
- Used by: systemd service

**Backtest Layer:**
- Purpose: Historical simulation of immediate-alert and staged workflows
- Location: `src/backtest.py`
- Contains: `run_backtest()`, `run_staged_backtest()`, `simulate_trade()`, `SortedHistory`
- Depends on: DB, signals, binance, snapshots, config
- Used by: CLI `backtest` and `backtest-confirmation` commands

## Data Flow

**Daily Batch Pipeline (Staged Mode):**

1. `run_phase1_watchlist()` loads universe via `refresh_universe()` and `daily_volume_check()`
2. `_compute_signals()` fetches 5 quantitative signals per token from Binance public APIs
3. `_build_qualitative()` enriches each token with tags from DeFiLlama, Snapshot, GitHub, and Binance 24h tickers
4. `fetch_catalyst_data()` pulls news/events from CryptoPanic and CoinMarketCal
5. `CatalystScorer.aggregate()` computes a 0-1 catalyst score per token
6. `generate_watchlist()` scores tokens with weighted alpha formula and persists candidates to `watchlist` table via `StageManager`
7. `run_phase2_confirmation()` (polling every 30 min or on-demand) loads active watchlist items, runs `ConfirmationChecker` for price action, volume, order book, and taker flip checks
8. Confirmed candidates are promoted to `confirmation` stage; those meeting entry checks promoted to `entry` stage
9. `run_phase3_entry()` computes ATR and position size, then sends final entry alerts via `TelegramNotifier`

**Telegram Bot Loop:**

1. Polls Telegram `getUpdates` every 2 seconds
2. Handles commands: `buy`, `close`, `scan`, `watchlist`, `positions`, `menu`, `help`
3. Checks active paper trades every 30 minutes for TP/SL/trailing stop hits
4. Sends P&L updates every 2 hours
5. Runs Phase 2 confirmation polling every `CONFIRMATION_POLL_MINUTES` in staged mode

**Backtest Flow:**

1. Fetches historical funding, OI, LS, taker, and OHLCV data for universe tokens
2. Merges Binance history with local `signal_snapshots` to extend lookback
3. `SortedHistory` and `TakerHistory` provide fast binary-search percentile lookups
4. Simulates trade outcomes with TP1/TP2/trailing-stop logic
5. Saves results to `backtest_results` table

## State Management

**Database:** SQLite at `data/pump.db` with WAL mode (`PRAGMA journal_mode=WAL`) and foreign keys enabled.

**Key Tables:**
- `tokens` — universe membership and metadata
- `watchlist` — Phase 1 candidates with scores, signals, and catalyst fields
- `stage_progression` — state machine transitions (watchlist -> confirmation -> entry -> expired)
- `alerts` — historical alert records
- `trades` — backtest trade records
- `paper_trades` — live paper trading positions with partial fill tracking
- `signal_snapshots` — daily signal values for local percentile history
- `catalyst_events` — raw catalyst events from external sources
- `backtest_results` — aggregate backtest window metrics

**State Machine:**
- Managed by `StageManager` in `src/stages.py`
- TTL-based expiration: `WATCHLIST_TTL_HOURS=72`, `CONFIRMATION_TTL_HOURS=24`
- Promotions are explicit; expirations are explicit or TTL-driven

## Concurrency Model

**Single-threaded with sequential API calls.**
- No asyncio, threading, or multiprocessing in production code
- Binance API calls are globally rate-limited via `_rate_limit()` in `src/binance.py` (0.05s minimum delay)
- SQLite WAL mode supports one writer and multiple readers, but the codebase uses a single writer per process
- Telegram bot loop is a single `while True` thread with blocking `requests.get` long-polling

## Key Abstractions

**Signal Dataclasses:**
- Purpose: Encapsulate per-token signal state with firing logic
- Examples: `FundingSignal`, `OIDivergenceSignal`, `LSRatioSignal`, `TakerRatioSignal`, `OrderBookSignal` in `src/signals.py`
- Pattern: Immutable-ish dataclasses with `finalize_*()` functions that compute cross-sectional percentiles and set `fired` boolean

**CatalystResult / CatalystEvent:**
- Purpose: Model qualitative events with multi-dimensional scoring
- Examples: `CatalystEvent`, `CatalystResult` in `src/catalysts.py`
- Pattern: Dataclass with computed `final_score` from base weight, quality dimensions, freshness, proximity, pre-move penalty, and negative risk

**SortedHistory / TakerHistory:**
- Purpose: Fast binary-search lookups for backtest historical percentile queries
- Examples: `SortedHistory` in `src/backtest.py`, `TakerHistory` in `src/binance.py`
- Pattern: Pre-sorted `numpy` arrays with `bisect_left` for `at()` and `percentile()` methods

**DB Session Context Manager:**
- Purpose: Encapsulate connection lifecycle with automatic commit/rollback
- Example: `@contextmanager def db_session()` in `src/db.py`
- Pattern: `with db_session() as conn:` used throughout the codebase

## Entry Points

**CLI Entry:**
- Location: `src/main.py`
- Triggers: `python -m src.main {init|universe|daily|confirm|monitor|backtest|backtest-confirmation|import-coinglass}`
- Responsibilities: Argument parsing, thin delegation to domain functions

**Bot Daemon:**
- Location: `src/bot.py` -> `run_bot()`
- Triggers: `python -m src.bot` or systemd service
- Responsibilities: Telegram polling, paper trade lifecycle, on-demand pipeline runs

**Cron Job:**
- Location: `run_daily.sh`
- Triggers: Scheduled via cron at 08:07 UTC
- Responsibilities: Log rotation, execute `python -m src.main daily`

## Error Handling

**Strategy:** Defensive with broad exception catching at API boundaries.

**Patterns:**
- API calls wrapped in `try/except` with silent fallback to empty lists or `None`
- Pipeline `_compute_signals()` returns `None` on fatal fetch failure, aborting the run
- `db_session()` context manager rolls back on exception and re-raises
- Schema migrations in `init_db()` use `try/except` to silently ignore "column already exists" errors
- Backtest helpers catch and swallow exceptions to continue processing other symbols

## Cross-Cutting Concerns

**Logging:** Mix of `print()` statements for operational visibility and `logging` module for catalyst source errors. No centralized structured logging.

**Validation:** No dedicated validation framework. Config constants in `src/config.py` serve as the source of truth. Input validation is ad-hoc (e.g., price parsing in `src/bot.py`).

**Authentication:** No trading API authentication. Only read-only Binance public APIs. Telegram bot token and optional CryptoPanic / CoinMarketCal / Dune / CoinGlass keys loaded from environment via `python-dotenv`.

**Rate Limiting:** Global `_rate_limit()` in `src/binance.py` with 0.05s delay between calls. Exponential backoff on HTTP 429.

---

*Architecture analysis: 2026-05-16*
