# Testing Patterns

**Analysis Date:** 2026-05-16

## Test Framework

**Runner:**
- pytest (evidenced by `.pytest_cache` and test file structure)
- Config: Not detected (no `pytest.ini`, `pyproject.toml`, or `setup.cfg` found)

**Assertion Library:**
- Built-in `assert` (no `unittest.TestCase` or `pytest` special assertions required)

**Run Commands:**
```bash
pytest                  # Run all tests
pytest -v               # Verbose output
pytest tests/           # Explicit test directory
```

## Test File Organization

**Location:**
- All tests in `tests/` directory at project root
- Named `test_<module>.py` mirroring `src/<module>.py`

**Test Files:**
| Test File | Source Module | Lines |
|-----------|--------------|-------|
| `test_confirmation.py` | `src/confirmation.py` | 913 |
| `test_pipeline.py` | `src/pipeline.py` | 511 |
| `test_catalysts.py` | `src/catalysts.py` | 375 |
| `test_bot.py` | `src/bot.py` | 328 |
| `test_watchlist_catalyst.py` | `src/watchlist.py` / catalysts | 317 |
| `test_backtest.py` | `src/backtest.py` | 257 |
| `test_stages.py` | `src/stages.py` | 200 |
| `test_qualitative.py` | `src/qualitative.py` | 141 |
| `test_regime.py` | `src/regime.py` | 127 |
| `test_watchlist.py` | `src/watchlist.py` | 126 |
| `test_db_roundtrip.py` | `src/db.py` | 121 |
| `test_notify_catalyst.py` | `src/notify.py` | 116 |
| `test_config.py` | `src/config.py` | 108 |
| `test_db_migrations.py` | `src/db.py` | 99 |
| `test_signals_oi.py` | `src/signals.py` | 98 |
| `test_risk.py` | `src/risk.py` | 83 |
| `test_signals_taker.py` | `src/signals.py` | 79 |
| `test_db.py` | `src/db.py` | 63 |

**Total test lines:** ~4,062 across 18 files (excluding `__init__.py`)

## Test Structure

**Suite Organization:**
```python
class TestRunPhase1Watchlist:
    """Tests for run_phase1_watchlist()."""

    def test_generates_candidates(self, monkeypatch):
        """Should generate watchlist candidates when signals are present."""
        ...

    def test_no_candidates_sends_message(self, monkeypatch):
        """When no candidates, Telegram message should say no candidates."""
        ...
```

**Patterns:**
- Test classes group related behaviors by function or component
- Test method docstrings describe the expected behavior
- `monkeypatch` fixture used extensively for dependency injection

## Mocking

**Framework:** `unittest.mock.MagicMock` and `unittest.mock.patch`, plus `pytest.monkeypatch`

**Patterns:**
```python
# Monkeypatch module-level dependencies
monkeypatch.setattr("src.pipeline.init_db", lambda: None)
monkeypatch.setattr("src.pipeline.refresh_universe", lambda: ["BTCUSDT"])

# Patch with context manager
with patch("src.confirmation.get_klines", return_value=candles):
    result = checker._check_price_action("BTCUSDT")

# MagicMock for complex collaborators
mock_checker = MagicMock()
mock_checker.run_confirmation.return_value = [...]
```

**What to Mock:**
- All external API calls (Binance, CoinGlass, Telegram, Dune)
- Database sessions (replaced with in-memory SQLite or MagicMock)
- Time-dependent functions

**What NOT to Mock:**
- Pure math/logic helpers tested directly (e.g., `_rolling_zscore`)

## Fixtures and Factories

**Test Data:**
```python
def make_candle(close=100.0, volume=1000.0, high=101.0, low=99.0):
    return {"c": close, "v": volume, "h": high, "l": low}

def make_taker_candle(ratio=0.5, ts=1000000):
    return {"buySellRatio": ratio, "timestamp": ts}
```

**Location:** Inline within test files

**Fixture Pattern:**
```python
@pytest.fixture
def stage_mgr():
    """Provide a StageManager with a clean in-memory DB."""
    cfg.DB_PATH = os.path.join(tempfile.gettempdir(), "test_stages_pump.db")
    init_db()
    mgr = StageManager()
    yield mgr
    cfg.DB_PATH = orig
```

**Parametrize:**
```python
@pytest.mark.parametrize("text", ["help", "/help", "start", "/start", "hi", "hello"])
def test_all_variants_show_keyboard(self, text):
    ...
```

## Coverage

**Requirements:** None enforced

**View Coverage:**
```bash
pytest --cov=src --cov-report=term-missing
```
(Not configured; command is hypothetical based on pytest ecosystem.)

## Test Types

**Unit Tests:**
- 100% of the test suite
- Heavy mocking of external dependencies
- Fast, isolated, no network access

**Integration Tests:**
- Not detected

**E2E Tests:**
- Not detected

## Environment Setup in Tests

**Pattern:**
```python
import os
os.environ["TELEGRAM_BOT_TOKEN"] = "test:token"
os.environ["TELEGRAM_CHAT_ID"] = "12345"
```

Required because `src/config.py` reads environment variables at import time via `load_dotenv()`.

## CI/CD Test Execution

**CI Pipeline:** Not detected (no `.github/workflows`, `.gitlab-ci.yml`, or similar)

## Known Gaps in Test Coverage

**Untested Source Modules:**
| Module | Lines | Notes |
|--------|-------|-------|
| `src/binance.py` | 362 | Core API client — no dedicated test file |
| `src/coinglass.py` | 125 | External API wrapper — untested |
| `src/dune_client.py` | 150 | External API wrapper — untested |
| `src/dune_queries.py` | 176 | Query definitions — untested |
| `src/main.py` | 205 | CLI entry point — untested |
| `src/snapshots.py` | ~100 | Snapshot storage — untested |
| `src/universe.py` | 116 | Universe refresh logic — untested |

**Partially Tested:**
- `src/notify.py` — only `test_notify_catalyst.py` (catalyst-specific logic), not full notification pipeline
- `src/watchlist.py` — covered by `test_watchlist.py` and `test_watchlist_catalyst.py`, but DB persistence mocked out

**Structural Gaps:**
- No integration tests against real or local test databases
- No tests verifying actual HTTP request/response handling
- No performance or load tests
- No contract tests for external APIs
- Database tests rely on temporary SQLite files, not in-memory `:memory:`

---

*Testing analysis: 2026-05-16*
