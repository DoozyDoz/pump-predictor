# Codebase Structure

**Analysis Date:** 2026-05-16

## Directory Layout

```
/home/muhammad/Documents/01-09 Apps/01 SaaS/01.02 Alpha/
├── src/                    # All Python source code
├── tests/                  # pytest test suite
├── data/                   # SQLite DB and logs
│   └── logs/               # Cron execution logs
├── docs/                   # Documentation and ADRs
│   └── adr/                # Architecture Decision Records
├── .agentic-harness/       # Agentic harness templates and runs
├── .claude/                # Claude-specific agents and commands
├── .gsd/                   # GSD runtime artifacts
├── .planning/              # Planning documents (this directory)
│   └── codebase/           # Codebase analysis outputs
├── .env                    # Environment secrets (not committed)
├── .env.example            # Environment variable template
├── requirements.txt        # Python dependencies
├── Makefile               # Common CLI shortcuts
├── run_bot.sh             # Bot daemon launcher
├── run_daily.sh           # Daily cron pipeline launcher
├── pump-bot.service       # systemd service definition
├── pump_alerts.csv        # Last-run alert output (generated)
├── install.sh             # Harness installer script
├── phi                    # Harness entrypoint script
└── test-harness           # Test harness script
```

## Directory Purposes

**`src/`:**
- Purpose: All application logic, data access, and orchestration
- Contains: 20+ Python modules, no sub-packages (flat module structure)
- Key files: `main.py`, `bot.py`, `pipeline.py`, `signals.py`, `binance.py`, `db.py`, `config.py`

**`tests/`:**
- Purpose: pytest unit and integration tests
- Contains: One test file per source module (e.g., `test_pipeline.py` for `src/pipeline.py`)
- Key files: `test_pipeline.py`, `test_bot.py`, `test_backtest.py`, `test_confirmation.py`

**`data/`:**
- Purpose: Runtime data storage
- Contains: SQLite database (`pump.db`), execution logs, CoinGecko mapping cache (`coingecko_map.json`)
- Key files: `pump.db` (not committed), `logs/daily_*.log` (generated)
- Generated: Yes (logs and DB)
- Committed: No (ignored by `.gitignore`)

**`docs/`:**
- Purpose: Project documentation and architecture decisions
- Contains: `CONTEXT.md`, `README.md`, ADR files
- Key files: `adr/0001-backtest-phasing-funding-first.md`

**`.agentic-harness/`:**
- Purpose: Agentic harness metadata, templates, and run history
- Contains: Agent definitions, run logs, templates
- Generated: Partially
- Committed: Yes

**`.claude/`:**
- Purpose: Claude Code-specific configurations
- Contains: `agents/`, `commands/`
- Generated: No
- Committed: Yes

**`.gsd/`:**
- Purpose: GSD (Get Stuff Done) runtime state
- Contains: `runtime/` artifacts
- Generated: Yes
- Committed: No

## Key File Locations

**Entry Points:**
- `src/main.py`: CLI argument parser and command dispatcher
- `src/bot.py`: Telegram bot daemon main loop (`run_bot()`)
- `run_daily.sh`: Cron wrapper for `python -m src.main daily`
- `run_bot.sh`: Daemon wrapper for `python -m src.bot`

**Configuration:**
- `src/config.py`: All constants, thresholds, and environment variable loading
- `.env`: Runtime secrets (Telegram tokens, API keys)
- `.env.example`: Template showing required variables
- `Makefile`: Shortcuts for `init`, `daily`, `backtest`, `universe`

**Core Logic:**
- `src/pipeline.py`: Daily batch orchestration and staged workflow phases
- `src/signals.py`: 5-signal quantitative computation engine
- `src/confirmation.py`: Phase 2 intraday confirmation checks
- `src/watchlist.py`: Phase 1 watchlist candidate generation
- `src/stages.py`: State machine for workflow progression
- `src/catalysts.py`: Catalyst scoring and external event fetching
- `src/qualitative.py`: Qualitative tag framework and external API checks

**Data Access:**
- `src/binance.py`: Binance public API client (spot + futures)
- `src/db.py`: SQLite schema, connection factory, and `db_session()` context manager
- `src/snapshots.py`: Local signal history storage for percentile calculations
- `src/coinglass.py`: CoinGlass API client for historical derivatives data
- `src/dune_client.py`: Dune Analytics API client
- `src/dune_queries.py`: Dune query definitions

**Risk & Market:**
- `src/risk.py`: ATR computation and position sizing
- `src/regime.py`: Market regime detection (BTC dominance, volatility)

**Universe & Assets:**
- `src/universe.py`: Token universe refresh and volume filtering

**Notifications:**
- `src/notify.py`: Telegram message formatting and sending

**Backtesting:**
- `src/backtest.py`: Historical simulation with `run_backtest()` and `run_staged_backtest()`

## Naming Conventions

**Files:**
- Modules: `snake_case.py` (e.g., `pipeline.py`, `catalysts.py`)
- Tests: `test_{module_name}.py` (e.g., `test_pipeline.py` for `pipeline.py`)
- Scripts: `run_{purpose}.sh` or `{purpose}.sh`

**Directories:**
- Source directories: flat (no nesting under `src/`)
- Meta directories: prefixed with `.` (e.g., `.claude`, `.gsd`)

**Database Tables:**
- snake_case (e.g., `paper_trades`, `signal_snapshots`, `stage_progression`)

## Where to Add New Code

**New Signal:**
- Implementation: `src/signals.py` (add dataclass + compute/finalize functions)
- Integration: `src/pipeline.py` in `_compute_signals()` and `_build_alerts()`
- Tests: `tests/test_signals_*.py`

**New External Data Source:**
- Client module: Create `src/{source}.py` (follow `src/coinglass.py` pattern)
- Integration: Import in `src/pipeline.py` or `src/catalysts.py` as needed
- Tests: `tests/test_{source}.py`

**New Pipeline Phase:**
- Implementation: Add to `src/pipeline.py` or create `src/{phase}.py`
- State management: Extend `src/stages.py` if new stage needed
- Notification: Add formatter in `src/notify.py`

**New DB Table:**
- Schema: Add `CREATE TABLE` to `SCHEMA` string in `src/db.py`
- Migration: Add `ALTER TABLE` block in `init_db()` for safe upgrades
- Access: Use `db_session()` context manager in consuming modules

**New Config Parameter:**
- Add to `src/config.py` with descriptive comment block
- Default value and env var loading if secret

## Special Directories

**`.planning/codebase/`:**
- Purpose: Codebase analysis documents consumed by GSD orchestrator
- Generated: Yes (by `/gsd-map-codebase` command)
- Committed: Yes

**`data/logs/`:**
- Purpose: Daily cron execution logs
- Generated: Yes (by `run_daily.sh`)
- Committed: No
- Retention: 30 days (deleted by `find ... -mtime +30 -delete` in `run_daily.sh`)

**`__pycache__/`:**
- Purpose: Python bytecode cache
- Generated: Yes
- Committed: No

---

*Structure analysis: 2026-05-16*
