# Alpha — Crypto Pump Predictor

Stacking weak signals before they become obvious. A quantitative + qualitative pump detection system with Telegram paper trading.

## Signals (5 quantitative + qualitative boost)

| # | Signal | Source | Mechanism |
|---|--------|--------|-----------|
| 1 | Funding-rate extreme | Binance Futures | 90d percentile + cross-sectional bottom 5% |
| 2 | OI/Price divergence | Binance Futures | 7d OI vs price divergence, top 5% (30d window) |
| 3 | Long/Short ratio extreme | Binance Futures | 30d percentile + cross-sectional bottom 5% |
| 4 | Taker buy/sell ratio | Binance Futures | 21d percentile + cross-sectional bottom 5% |
| 5 | Order book imbalance | Binance Spot | Multi-snapshot top-10 bid dominance, min 0.60 floor |

**Qualitative boost**: Real catalysts only (listings, governance, on-chain anomalies, TVL/revenue growth). 24h ticker tags (volume, momentum) are display-only — they do not contribute to scoring.

**Alert rules:** ≥2/5 signals, at least one strong derivative signal (funding/OI/LS), or ≥1 + real catalyst boost ≥0.5. Taker + book alone is blocked. Alerts on partial scans are marked PAPER ONLY.

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
TELEGRAM_BOT_TOKEN=  # from @BotFather
TELEGRAM_CHAT_ID=    # your Telegram user ID
DUNE_API_KEY=        # optional — on-chain queries (free tier blocked)
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
│   ├── binance.py          # Binance API client (spot, futures, derivatives — all signals)
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
