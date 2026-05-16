# Codebase Concerns

**Analysis Date:** 2026-05-16

## Tech Debt

**Broad Exception Swallowing (`except Exception: pass`)**
- Issue: Over 30 locations silently swallow all exceptions, making debugging impossible and hiding runtime failures.
- Files: `src/pipeline.py` (lines 65, 72, 87, 92, 100, 131, 176, 228, 403, 436, 677, 733), `src/catalysts.py` (lines 356, 388, 411, 460, 489), `src/universe.py` (lines 16, 35, 57), `src/signals.py` (lines 87, 94, 153), `src/binance.py` (lines 155, 347, 361), `src/qualitative.py` (lines 146, 172, 191), `src/watchlist.py` (line 264), `src/risk.py` (line 21), `src/db.py` (lines 179, 197, 204, 231, 244), `src/notify.py` (line 29)
- Impact: Production failures go undetected; data quality degrades silently.
- Fix approach: Replace bare `except Exception: pass` with specific exception types, add structured logging, and fail fast on critical paths.

**Print-Statement Logging**
- Issue: `print()` is used throughout as the primary observability mechanism instead of a proper logging framework.
- Files: `src/pipeline.py`, `src/main.py`, `src/universe.py`, `src/bot.py`, `src/backtest.py`
- Impact: Logs are unstructured, cannot be filtered by severity, and pollute stdout in test runs.
- Fix approach: Replace `print()` with `logging.getLogger(__name__)` calls at appropriate levels (INFO, WARNING, ERROR).

**Large Monolithic Modules**
- Issue: `src/pipeline.py` (852 lines) and `src/backtest.py` (700 lines) contain too many responsibilities.
- Files: `src/pipeline.py`, `src/backtest.py`
- Impact: High cognitive load, difficult to test in isolation, high merge-conflict risk.
- Fix approach: Extract `Phase1Watcher`, `Phase2Confirmer`, `Phase3EntrySizer` classes from pipeline. Extract backtest simulation engine into `src/backtest_engine.py`.

**Duplicated Scoring Logic**
- Issue: The 5-signal scoring framework is copy-pasted between `pipeline._build_alerts()` and `watchlist.generate_watchlist()`.
- Files: `src/pipeline.py` (lines 576-663), `src/watchlist.py` (lines 54-183)
- Impact: Threshold or signal-rule changes must be applied in two places; easy to drift out of sync.
- Fix approach: Extract a single `score_signals(symbol, f_map, oi_map, ls_map, t_map, b_map)` function in `src/scoring.py`.

**Circular-Import Workarounds**
- Issue: Local imports inside functions are used to avoid circular dependencies between `qualitative.py` and `catalysts.py`.
- Files: `src/qualitative.py` (line 257), `src/pipeline.py` (line 291)
- Impact: Violates PEP 8, masks architecture coupling, makes static analysis unreliable.
- Fix approach: Introduce a shared `src/types.py` or `src/models.py` module for data classes like `CatalystEvent`, `CatalystResult`.

**Legacy Dual Code Paths**
- Issue: `LEGACY_IMMEDIATE_ALERTS` flag branches the entire pipeline into two implementations (staged vs immediate).
- Files: `src/pipeline.py`, `src/config.py` (line 106)
- Impact: Double the test surface, double the maintenance burden.
- Fix approach: Deprecate legacy mode, remove flag and dead code after backtest validation.

**Magic Numbers Without Named Constants**
- Issue: Inline thresholds like `10_000_000` volume, `25%` daily range, `50_000` trades appear in `_build_qualitative()` without central definitions.
- Files: `src/pipeline.py` (lines 460, 472, 484, 499, 554)
- Impact: Hard to tune, easy to mismatch between pipeline and tests.
- Fix approach: Promote to named constants in `src/config.py`.

## Known Bugs

**Silent Data Loss on Schema Migration Failure**
- Issue: `db.py` catches `Exception` on every `ALTER TABLE` migration and silently ignores it.
- Files: `src/db.py` (lines 195-245)
- Symptoms: If a migration fails due to a typo or incompatible schema change, the application starts with an incomplete schema and may crash later with `OperationalError`.
- Trigger: Running `init_db()` when a new migration column conflicts with an existing one.
- Workaround: Manual inspection of SQLite schema after each deployment.

**Wrong Variable Reference in `_safe_pct`**
- Issue: `_safe_pct` in `_get_price_changes` takes an `interval` parameter but never uses it in the warning log message — it logs the hardcoded string `"1h"` regardless.
- Files: `src/pipeline.py` (lines 412-419)
- Symptoms: Log messages always say `"1h"` even for 4h and 24h windows.
- Trigger: Any call to `_get_price_changes`.
- Workaround: None needed functionally, but logs are misleading.

**Empty `__init__.py`**
- Issue: `src/__init__.py` is empty, providing no package-level exports or version metadata.
- Files: `src/__init__.py`
- Impact: No clean public API surface.

## Security Considerations

**SQL Injection in Dune Client**
- Risk: `fetch_token_addresses()` interpolates raw symbol strings into a SQL query sent to Dune Analytics API.
- Files: `src/dune_client.py` (lines 113-120)
- Current mitigation: Symbols originate from Binance API, not direct user input, but downstream consumers could pass untrusted data.
- Recommendations: Validate and escape symbols before interpolation, or use parameterized queries if Dune supports them.

**No Input Sanitization in Telegram Bot**
- Risk: `handle_message()` parses arbitrary text from Telegram users without validation.
- Files: `src/bot.py` (lines 233-436)
- Current mitigation: Bot is intended for a single private chat.
- Recommendations: Validate `chat_id` against an allow-list, sanitize symbol names with a regex (`^[A-Z0-9]+$`), and rate-limit commands.

**Secrets Loaded Without Validation**
- Risk: API keys and tokens are loaded via `os.getenv()` with no checks for presence or format.
- Files: `src/config.py` (lines 6-9, 184-185)
- Current mitigation: `.env.example` documents required variables.
- Recommendations: Add a startup validation function that raises `RuntimeError` if required secrets are missing or malformed.

**Hardcoded API URL with Embedded Token**
- Risk: Telegram API URL is built with the bot token embedded in the path string.
- Files: `src/bot.py` (line 19)
- Current mitigation: Token comes from environment.
- Recommendations: Pass token as a header or parameter where supported, or mask it in logs.

**No HTTPS Certificate Verification Mentioned**
- Risk: `requests.get/post` calls use default `verify=True`, but no pinning or cert bundle validation is configured.
- Files: `src/binance.py`, `src/coinglass.py`, `src/dune_client.py`, `src/notify.py`
- Recommendations: Document the trust model; consider pinning known API certificates for Binance.

## Performance Bottlenecks

**N+1 API Calls in Signal Computation**
- Problem: `_compute_signals()` iterates over every symbol and calls `get_taker_ratio_history()`, `compute_oi_divergence_signal()`, and `compute_ls_ratio_signal()` individually.
- Files: `src/pipeline.py` (lines 83-101)
- Cause: No batch endpoints are used for per-symbol derivative data.
- Improvement path: Use Binance's bulk endpoints where available; parallelize with `asyncio` or `concurrent.futures.ThreadPoolExecutor`.

**Redundant Kline Fetches in Confirmation**
- Problem: Each confirmation check fetches 48h of 1h klines independently for price action, volume, and pre-move checks.
- Files: `src/confirmation.py` (lines 193, 252, 284)
- Cause: No caching layer shares fetched candles between sub-checks.
- Improvement path: Fetch once per symbol per confirmation run and pass the candle list to each checker.

**Sequential Price Queries in Bot**
- Problem: `check_positions()` and position-status commands call `get_price()` serially for each active position.
- Files: `src/bot.py` (lines 157-228, 274-299)
- Cause: Uses single-symbol Binance ticker endpoint.
- Improvement path: Use `get_24h_tickers()` once and build a price map.

**Order Book Snapshots with Blocking Sleep**
- Problem: `compute_order_book_signal()` fetches the order book 3 times with `time.sleep(3)` between calls.
- Files: `src/signals.py` (lines 565-573)
- Cause: Designed to detect spoofed walls but blocks the thread for ~6 seconds per symbol.
- Improvement path: Reduce to 2 snapshots with 2s delay, or make async.

**Backtest Memory Consumption**
- Problem: `run_backtest()` loads all historical candles for all symbols into memory upfront.
- Files: `src/backtest.py` (lines 140-151)
- Cause: No lazy loading or windowed streaming.
- Improvement path: Stream data symbol-by-symbol or use memory-mapped files for OHLCV history.

**SQLite Single-Writer Bottleneck**
- Problem: SQLite with WAL mode still serializes writers.
- Files: `src/db.py`
- Cause: Every DB operation acquires the file lock.
- Improvement path: For multi-process deployments, migrate to PostgreSQL; for single-process, batch writes.

## Fragile Areas

**Database Schema Drift via Exception Swallowing**
- Files: `src/db.py`
- Why fragile: `ALTER TABLE` migrations silently ignore all failures, so schema drift accumulates unnoticed.
- Safe modification: Always inspect the DB schema before altering migrations; add an explicit schema-version table.
- Test coverage: Gaps — `test_db_migrations.py` (99 lines) does not assert the presence of every migrated column.

**Signal Computation Failure Cascades**
- Files: `src/pipeline.py` (lines 62-77)
- Why fragile: If `compute_all_funding_signals()` or `get_bulk_funding_rates()` fails, the entire daily scan aborts with `return None`.
- Safe modification: Degrade gracefully — compute what you can and mark scan status as `PARTIAL`.
- Test coverage: `test_pipeline.py` (511 lines) covers some cases but does not simulate wholesale API failure.

**Taker History Z-Score Computation**
- Files: `src/confirmation.py` (lines 433-455)
- Why fragile: `_rolling_zscore` uses a centered window that can look into the future at index `i + half`, which is acceptable for confirmation but dangerous if reused for signal generation.
- Safe modification: Add a docstring warning and a flag to enforce causal (lookback-only) windows.
- Test coverage: No dedicated test for edge cases (constant values, short arrays).

## Scaling Limits

**Telegram Bot Polling**
- Current capacity: Single-threaded long-polling loop with 2-second intervals.
- Limit: Cannot handle more than one chat concurrently; CPU-bound by Python GIL.
- Scaling path: Replace polling with webhook + async framework (FastAPI + aiogram).

**Universe Size vs API Rate Limits**
- Current capacity: 150 symbols × 5 signal types × multiple API endpoints.
- Limit: Binance public API rate limits (1200 req/min) are approached when universe grows or confirmation polling is frequent.
- Scaling path: Implement request coalescing, caching, and move to websocket streams for real-time data.

**SQLite Database Growth**
- Current capacity: Local file `data/pump.db` with WAL journal.
- Limit: File size and concurrent writer contention will degrade performance as snapshot history grows.
- Scaling path: Archive old snapshots to Parquet/CSV; migrate to PostgreSQL.

## Dependencies at Risk

**Dune Client SDK (`dune-client>=1.2`)**
- Risk: `src/dune_client.py` explicitly says it bypasses the SDK's buggy execute method, yet the SDK is still listed in requirements.
- Impact: Unused dependency increases attack surface and install time.
- Migration plan: Remove `dune-client` from `requirements.txt` or switch to the SDK once the bug is resolved.

**Binance API Reliability**
- Risk: No fallback exchange is configured if Binance API is unreachable or delists a symbol.
- Impact: Pipeline halts or produces partial scans.
- Migration plan: Abstract exchange client behind an interface; add Bybit or OKX as fallback.

## Missing Critical Features

**Health Check / Readiness Probe**
- Problem: No endpoint or function reports whether the bot, pipeline, and DB are healthy.
- Blocks: Cannot deploy to Kubernetes or any orchestrated environment safely.

**Metrics and Observability**
- Problem: No Prometheus-style metrics, no error-rate tracking, no latency histograms.
- Blocks: Cannot set up alerting on pipeline failures or API latency spikes.

**Circuit Breaker for External APIs**
- Problem: If Binance, CoinGlass, or Telegram APIs fail repeatedly, the code keeps retrying blindly.
- Blocks: Resilience during API outages.

**Graceful Shutdown**
- Problem: Beyond `KeyboardInterrupt` in `bot.py`, there is no signal handling or cleanup of DB connections.
- Blocks: Risk of WAL file corruption on forced termination.

**Live Trading Safeguards**
- Problem: `ENABLE_PAPER_ONLY_MODE = True` exists but there is no enforcement layer preventing accidental activation of live order placement.
- Blocks: Dangerous if someone adds a real exchange client later.

## Test Coverage Gaps

**Untested External Integrations**
- What's not tested: `coinglass.py`, `dune_client.py`, `dune_queries.py`, `main.py`, `universe.py`, `snapshots.py`, `regime.py` have zero or minimal test coverage.
- Files: `tests/` has 20 files but many are <150 lines.
- Risk: API contract changes in Binance, CoinGlass, or Dune break the pipeline unnoticed.
- Priority: High for `coinglass.py` and `dune_client.py`; Medium for `regime.py`.

**Missing End-to-End Pipeline Test**
- What's not tested: No test exercises the full `run_phase1_watchlist → run_phase2_confirmation → run_phase3_entry` flow with real (or mocked) external APIs.
- Files: `tests/test_pipeline.py` (511 lines) covers unit-style assertions only.
- Risk: Stage hand-off bugs (e.g., watchlist candidate format mismatch with confirmation checker) slip through.
- Priority: High.

**No Performance / Load Tests**
- What's not tested: Backtest runtime for 150 symbols, bot polling under message burst, DB write throughput.
- Risk: Performance regressions on larger universes.
- Priority: Medium.

**No Concurrency Tests**
- What's not tested: Simultaneous bot commands, overlapping pipeline runs, DB locking under concurrent readers.
- Risk: Race conditions in `paper_trades` updates or stage progression.
- Priority: Medium.

---

*Concerns audit: 2026-05-16*
