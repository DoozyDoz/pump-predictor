# External Integrations

**Analysis Date:** 2026-05-16

## APIs & External Services

### Binance (Public APIs)
- **Purpose:** Primary market data source — spot tickers, OHLCV, order book depth, funding rates, open interest, long/short ratios, taker buy/sell ratios
- **Auth:** None (public endpoints only)
- **Rate limiting:** Custom client-side rate limiter enforces 20 req/s (`_MIN_DELAY = 0.05`) with exponential backoff on HTTP 429
- **Base URLs:**
  - Spot: `https://api.binance.com`
  - Futures: `https://fapi.binance.com`
  - Futures data: `https://fapi.binance.com/futures/data`
- **Key endpoints used:**
  - `/api/v3/exchangeInfo` — universe discovery
  - `/api/v3/ticker/24hr` — volume ranking
  - `/api/v3/depth` — order book imbalance
  - `/api/v3/klines` — OHLCV for ATR and backtest
  - `/fapi/v1/fundingRate` — funding rate history
  - `/fapi/v1/premiumIndex` — bulk current funding
  - `/fapi/v1/openInterest` — current OI
  - `/futures/data/openInterestHist` — OI history
  - `/futures/data/globalLongShortAccountRatio` — LS ratio history
  - `/futures/data/takerlongshortRatio` — taker ratio history
- **Files:** `src/binance.py`, `src/universe.py`, `src/signals.py`

### CoinGlass API v4
- **Purpose:** Historical derivatives data for backtesting (funding, OI, LS ratio)
- **Auth:** API key via `CG-API-KEY` header (`COINGLASS_API_KEY` env var)
- **Rate limiting:** 30 req/min (~2s delay between calls)
- **Base URL:** `https://open-api-v4.coinglass.com/api`
- **Key endpoints:**
  - `/futures/funding-rate/history`
  - `/futures/open-interest/history`
  - `/futures/global-long-short-account-ratio/history`
- **Files:** `src/coinglass.py`, invoked from `src/main.py` via `import-coinglass` CLI command

### Dune Analytics
- **Purpose:** On-chain analytics (active-address growth, CEX net outflow)
- **Auth:** API key via `X-Dune-API-Key` header (`DUNE_API_KEY` env var)
- **Base URL:** `https://api.dune.com/api/v1`
- **SDK:** `dune-client>=1.2` installed but bypassed in favor of raw `requests`
- **Key operations:**
  - Create query (`POST /query/`)
  - Execute query (`POST /query/{id}/execute`)
  - Poll status (`GET /execution/{id}/status`)
  - Fetch CSV results (`GET /execution/{id}/results/csv`)
- **SQL queries:** `ACTIVE_ADDRESS_GROWTH_QUERY`, `CEX_OUTFLOW_QUERY` in `src/dune_queries.py`
- **Files:** `src/dune_client.py`, `src/dune_queries.py`

### Telegram Bot API
- **Purpose:** Alert notifications and paper-trading bot interaction
- **Auth:** Bot token (`TELEGRAM_BOT_TOKEN` env var)
- **Base URL:** `https://api.telegram.org/bot{token}`
- **Key operations:**
  - `sendMessage` — alerts, watchlists, confirmations, entries
  - `getUpdates` — long-polling for user commands
- **Bot features:** Persistent reply keyboard, scan/command handling, position tracking
- **Files:** `src/notify.py`, `src/bot.py`

### DeFiLlama
- **Purpose:** TVL and revenue metrics for catalyst scoring
- **Auth:** None
- **Base URL:** `https://api.llama.fi`
- **Endpoint:** `/protocol/{protocol_slug}`
- **Files:** `src/qualitative.py` (function `check_defillama_metrics`), called from `src/catalysts.py`

### Snapshot.org
- **Purpose:** DAO governance proposal detection for catalyst scoring
- **Auth:** None (public GraphQL)
- **Endpoint:** `https://hub.snapshot.org/graphql`
- **Query:** Proposals by `space` ID, filtered by `created_gte`
- **Files:** `src/qualitative.py` (function `check_snapshot_proposals`), called from `src/catalysts.py`

### GitHub API
- **Purpose:** Developer activity and release detection for catalyst scoring
- **Auth:** None (public endpoints only)
- **Base URL:** `https://api.github.com`
- **Endpoints:**
  - `/repos/{owner}/{repo}/releases?per_page=3`
  - `/repos/{owner}/{repo}/commits?per_page=5`
- **Files:** `src/qualitative.py` (function `check_github_activity`), called from `src/catalysts.py`

### CryptoPanic
- **Purpose:** News feed for catalyst events
- **Auth:** API token query param (`CRYPTOPANIC_API_KEY` env var)
- **Base URL:** `https://cryptopanic.com/api/v1/posts/`
- **Param:** `auth_token={key}`, `currencies={symbol}`
- **Files:** `src/catalysts.py`

### CoinMarketCal
- **Purpose:** Event calendar for catalyst detection
- **Auth:** API key via `x-api-key` header (`COINMARKETCAL_API_KEY` env var)
- **Base URL:** `https://api.coinmarketcal.com/v1/events`
- **Param:** `coins={symbol}`
- **Files:** `src/catalysts.py`

## Data Storage

**Databases:**
- SQLite 3 (`data/pump.db`) — all application state, signals, trades, watchlists, backtest results

**File Storage:**
- Local filesystem only (CSV logs, SQLite DB)

**Caching:**
- None external; local `signal_snapshots` table acts as a local cache for historical Binance/CoinGlass data

## Authentication & Identity

**Auth Provider:**
- Custom per-service API keys (no OAuth or SSO)
- Keys stored in `.env` (not committed)

## Monitoring & Observability

**Error Tracking:**
- None external; errors logged to stdout/stderr and occasionally to Telegram messages

**Logs:**
- Daily pipeline logs: `data/logs/daily_YYYYmmdd_HHMM.log`
- Log rotation: `find data/logs -name "daily_*.log" -mtime +30 -delete`

## CI/CD & Deployment

**Hosting:**
- Self-hosted Linux VPS / workstation

**CI Pipeline:**
- None detected

**Deployment:**
- systemd service (`pump-bot.service`) for Telegram bot daemon
- Shell scripts: `run_bot.sh`, `run_daily.sh`
- Cron triggers `run_daily.sh` at 08:07 UTC

## Environment Configuration

**Required env vars:**
- `COINGLASS_API_KEY` — CoinGlass API access
- `DUNE_API_KEY` — Dune Analytics API access
- `TELEGRAM_BOT_TOKEN` — Telegram bot authentication
- `TELEGRAM_CHAT_ID` — Destination chat for alerts
- `CRYPTOPANIC_API_KEY` — CryptoPanic news feed (optional, guarded in code)
- `COINMARKETCAL_API_KEY` — CoinMarketCal events (optional, guarded in code)

**Secrets location:**
- `.env` file in repository root (listed in `.gitignore`)

## Webhooks & Callbacks

**Incoming:**
- None (no web server exposed)

**Outgoing:**
- Telegram `sendMessage` calls for all alert stages (watchlist, confirmation, entry)

---

*Integration audit: 2026-05-16*
