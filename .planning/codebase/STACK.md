# Technology Stack

**Analysis Date:** 2026-05-16

## Languages

**Primary:**
- Python 3.12.3 — sole implementation language, used across `src/`, `tests/`, and orchestration scripts

## Runtime

**Environment:**
- CPython 3.12.3 (system + venv at `.venv`)
- include-system-site-packages = false

**Package Manager:**
- pip (via `requirements.txt`)
- Lockfile: not detected (no `requirements.lock`, `poetry.lock`, or `uv.lock`)

## Frameworks

**Core:**
- No web framework (Flask/FastAPI/Django) — application is a CLI tool + long-polling daemon
- Standard library `argparse` for CLI (`src/main.py`)
- `requests` for all HTTP client needs

**Testing:**
- pytest (inferred from test file imports and `.pytest_cache`)
- `unittest.mock` for mocking in tests

**Build/Dev:**
- `Makefile` with convenience targets (`init`, `daily`, `backtest`, `universe`, `install`)
- No build tools (setuptools, hatch, poetry) detected

## Key Dependencies

**Critical:**
- `requests>=2.31` — HTTP client for all external API calls (Binance, CoinGlass, Dune, Telegram, etc.)
- `pandas>=2.0` — DataFrame manipulation for Dune query results and backtest analytics
- `numpy>=1.24` — Numerical computation for signal percentiles, rolling statistics, and OI divergence
- `python-dotenv>=1.0` — Environment variable loading from `.env`
- `dune-client>=1.2` — Dune Analytics SDK (the codebase bypasses its `execute` method and uses raw `requests` instead)

**Infrastructure:**
- `sqlite3` (stdlib) — Embedded relational database (`data/pump.db`)

## Configuration

**Environment:**
- Loaded via `python-dotenv` from `.env` file in repo root
- Required variables: `COINGLASS_API_KEY`, `DUNE_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `CRYPTOPANIC_API_KEY`, `COINMARKETCAL_API_KEY`
- Reference file: `.env.example`

**Build:**
- `requirements.txt` — pip dependency manifest
- `Makefile` — task automation

**Application Config:**
- `src/config.py` — centralized constants (thresholds, risk params, feature flags, API keys read from env)

## Data Storage

**Primary Database:**
- SQLite 3 (`data/pump.db`)
- WAL mode enabled (`PRAGMA journal_mode=WAL`)
- Foreign keys enforced (`PRAGMA foreign_keys=ON`)
- Schema managed via raw SQL in `src/db.py`
- Tables: `tokens`, `funding_rates`, `alerts`, `trades`, `ohlcv`, `paper_trades`, `signal_snapshots`, `backtest_results`, `watchlist`, `stage_progression`, `catalyst_events`

**Local Files:**
- `data/` directory for SQLite DB and logs (`data/logs/daily_*.log`)
- CSV export: `pump_alerts.csv`

## Platform Requirements

**Development:**
- Python 3.12+
- Linux environment (tested on Linux 6.17)
- venv recommended (`.venv` present)

**Production:**
- Linux host with systemd
- `pump-bot.service` runs Telegram bot as a persistent daemon
- `run_daily.sh` invoked via cron for daily pipeline execution
- Working directory expected at `/home/muhammad/pump-predictor` (per service file)

---

*Stack analysis: 2026-05-16*
