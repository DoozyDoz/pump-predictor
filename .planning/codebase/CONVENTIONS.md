# Coding Conventions

**Analysis Date:** 2026-05-16

## Naming Patterns

**Files:**
- Modules use `snake_case`: `backtest.py`, `confirmation.py`, `pipeline.py`
- Test modules mirror source names: `test_backtest.py`, `test_confirmation.py`

**Functions:**
- All functions use `snake_case`: `run_phase1_watchlist()`, `get_active_positions()`, `build_main_menu()`
- Private helpers prefixed with underscore: `_compute_signals()`, `_store_snapshots()`, `_check_price_action()`

**Variables:**
- Local variables use `snake_case`: `candidates`, `entry_price`, `funding_rates`
- Module-level constants use `UPPER_CASE`: `TELEGRAM_BOT_TOKEN`, `DB_PATH`, `WATCHLIST_THRESHOLD`
- Config constants with numeric separators: `1_000_000`, `86400_000`

**Classes:**
- All classes use `PascalCase`: `StageManager`, `ConfirmationChecker`, `FundingSignal`
- Enum members use `UPPER_CASE`: `Stage.WATCHLIST`, `Stage.CONFIRMATION`

**Types:**
- Type hints use `PascalCase` for classes and built-in generics: `Optional[int]`, `list[dict]`, `dict | None`
- Python 3.10+ union syntax is preferred: `int | None` instead of `Optional[int]`

## Code Style

**Formatting:**
- No `pyproject.toml`, `setup.cfg`, `.flake8`, or `.black` config files detected
- `.ruff_cache` directory present, indicating `ruff` is used for linting/formatting
- Indentation: 4 spaces
- Line length: approximately 100–120 characters (observed in source)

**Linting:**
- `ruff` is the likely linter (cache directory exists)
- No explicit rule configuration found in repository

## Import Organization

**Order:**
1. Standard library (`os`, `json`, `sqlite3`, `datetime`, `tempfile`)
2. Third-party (`requests`, `pandas`, `numpy`, `pytest`)
3. Local project modules (`from src.config import ...`, `from src.db import ...`)

**Path Aliases:**
- No `src` path aliasing beyond direct module imports
- Imports use absolute paths: `from src.binance import get_klines`

**Patterns:**
- No `from __future__ import annotations` usage
- `typing` imports used sparingly: `Optional`, `List`, `Dict` not heavily used due to 3.10+ builtins

## Type Hints

**Usage:**
- Present on most public functions but not exhaustive
- Return types annotated on entry points and utilities: `def build_main_menu() -> dict`, `def send_message(...) -> bool`
- Parameter types used for config/data classes: `symbol: str`, `entry_price: float`
- Union types with `|`: `def open_position(...) -> int | None`

**Dataclasses:**
- `@dataclass` used for signal structures: `FundingSignal`, `OIDivergenceSignal`, `LSRatioSignal`
- Type hints on dataclass fields are standard practice

## Error Handling

**Patterns:**
- Broad `except Exception:` is common, often swallowing errors:
  ```python
  try:
      resp = requests.get(...)
  except Exception:
      pass
  return None
  ```
- Some functions return `None` on failure rather than raising
- Database transactions use `@contextmanager` with rollback on exception in `db_session()`

**Anti-patterns observed:**
- Silent failures in API wrappers (e.g., `get_price()`, `get_updates()`)
- `pass` in empty `except` blocks without logging

## Logging

**Framework:** `print()` statements only. Python `logging` module is not used.

**Patterns:**
- Startup messages: `print("Bot starting...")`
- Progress indicators: `print(f"  [{pct:.0f}%] {sym}: {added} snapshots")`
- Error output: `print(f"Error: {e}")`

**Note:** No structured logging, log levels, or rotation configured.

## Comments

**When to Comment:**
- Section dividers are extensive and standardized:
  ```python
  # ---------------------------------------------------------------------------
  # Price checker — called on timer
  # ---------------------------------------------------------------------------
  ```
- Module-level docstrings describe purpose and signal definitions
- Sparse inline comments for complex math or thresholds

**Docstrings:**
- Module docstrings present on most files
- Function docstrings present on public functions but minimal
- No Google-style or NumPy-style docstrings observed

## Function Design

**Size:**
- Functions tend to be medium-to-long. Several modules exceed 500 lines:
  - `src/pipeline.py`: ~852 lines
  - `src/backtest.py`: ~700 lines
  - `src/signals.py`: ~628 lines

**Parameters:**
- Keyword arguments used for optional config overrides
- Default values sourced from `src.config` constants

**Return Values:**
- Dictionaries used for ad-hoc result objects in many functions
- Dataclasses preferred for structured signal data

## Module Design

**Exports:**
- No `__all__` definitions observed
- Modules expose functions at module level

**Barrel Files:**
- Not used

## SQL Conventions

**Patterns:**
- Raw SQL inline in Python using `sqlite3`
- Parameterized queries with `?` placeholders
- DDL stored as multiline strings (`SCHEMA` in `db.py`)
- Migration logic in `init_db()` uses `try/except` around `ALTER TABLE`

---

*Convention analysis: 2026-05-16*
