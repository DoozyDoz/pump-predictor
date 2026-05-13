# Alpha — Crypto Pump Predictor

Stacking weak signals before they become obvious. A quantitative + qualitative pump detection system with Telegram paper trading.

## Signals (5 quantitative + qualitative boost)

| # | Signal | Source | Mechanism |
|---|--------|--------|-----------|
| 1 | Funding-rate extreme | CoinAnalyze | 90d percentile + cross-sectional bottom 5% |
| 2 | OI/Price divergence | CoinAnalyze | 7d OI vs price divergence, top 5% |
| 3 | Long/Short ratio extreme | CoinAnalyze | 90d percentile + cross-sectional bottom 5% |
| 4 | Taker buy/sell ratio | Binance | 21d percentile + cross-sectional bottom 5% |
| 5 | Order book imbalance | Binance | Top-10 bid dominance, cross-sectional top 5% |

**Qualitative boost** stacks weak signals: volume anomalies, capitulation, momentum, trade count spikes, volatility. Two 0.5-confidence signals = one 1.0 signal.

**Alert threshold:** ≥2/5 signals or qualitative boost ≥ 0.8.

## Backtest Results (top 50 tokens by volume)

| Model | Alerts | Precision | Profit Factor |
|-------|--------|-----------|---------------|
| Funding-only (1 signal) | 124 | 9% | 1.93 |
| 3-signal (funding + OI + LS) | 8 | 25% | **3.50** |

Exit strategy: 50% at +15%, 30% at +25%, 20% trailing -3%. Hard stop -7%.

## Quick Start

```bash
git clone https://github.com/DoozyDoz/pump-predictor.git
cd pump-predictor
pip install -r requirements.txt
cp .env.example .env  # add your API keys
python3 -m src.main init
python3 -m src.main universe
python3 -m src.main daily
```

## Environment Variables

```
COINALYZE_API_KEY=   # free tier at coinalyze.net
DUNE_API_KEY=        # optional — on-chain queries (free tier blocked)
TELEGRAM_BOT_TOKEN=  # from @BotFather
TELEGRAM_CHAT_ID=    # your Telegram user ID
```

## Commands

```bash
python3 -m src.main init          # Initialize SQLite database
python3 -m src.main universe      # Refresh token universe (top 132)
python3 -m src.main daily         # Run daily pipeline → alerts CSV + Telegram
python3 -m src.main backtest      # Run 4-signal walk-forward backtest
python3 -m src.main backtest --limit 50  # Backtest on top 50 tokens
```

## Cron

```
7 8 * * * /home/muhammad/pump-predictor/run_daily.sh
```

Runs daily at 08:07 UTC. Outputs to `data/logs/` and `pump_alerts.csv`.

## Telegram Bot (@alphact_bot)

Paper trading companion. Tracks positions and alerts on TP/SL.

| Command | Example |
|---------|---------|
| `buy COS at 0.00123` | Start tracking a paper position |
| `COS 0.00123` | Shorthand |
| `close COS` | Close position at current price |
| `positions` | Show all active positions with live P&L |
| `help` | Show command reference |

**Auto-alerts:** Hourly P&L, instant TP1 (+15%), TP2 (+25%), stop-loss (-7%), trailing stop (-3%).

## Project Structure

```
pump-predictor/
├── src/
│   ├── config.py           # All parameters and thresholds
│   ├── coinalyze.py        # CoinAnalyze API client (12 endpoints)
│   ├── binance.py          # Binance API client (taker ratio, order book, ticker)
│   ├── signals.py          # 5 signal computations + backtest variants
│   ├── backtest.py         # Walk-forward backtest with trade simulator
│   ├── pipeline.py         # Daily batch orchestrator
│   ├── qualitative.py      # Qualitative signal stacking and override logic
│   ├── notify.py           # Telegram alert formatting
│   ├── bot.py              # Telegram paper trading bot daemon
│   ├── universe.py         # Token universe management
│   ├── db.py               # SQLite schema (8 tables)
│   ├── dune_client.py      # Dune Analytics API wrapper
│   ├── dune_queries.py     # On-chain SQL queries (free-tier blocked)
│   └── main.py             # CLI entry point
├── data/                   # SQLite DB, logs, CoinGecko mapping
├── docs/adr/               # Architecture decision records
├── run_daily.sh            # Cron wrapper script
├── run_bot.sh              # Bot daemon launcher
├── pump-bot.service        # Systemd service file
└── CONTEXT.md              # Domain glossary and design decisions
```

## License

MIT
