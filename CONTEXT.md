# Crypto Pump Prediction

A system that stacks weak on-chain, market-structure, and flow signals to predict significant spot-price moves before they happen.

## Language

**Pump**:
A rapid, catalyst-driven price increase ≥15% within ≤24 hours, detectable through multi-signal convergence in a 48-hour lookahead window.
_Avoid_: Gainers, moonshots, runners, breakouts

**Pump Signal**:
A quantitative or qualitative indicator that contributes evidence toward a predicted pump. Signals are either numeric (directly measurable) or qualitative (requiring interpretation).
_Avoid_: Alpha, tip, alert

**Lookahead Window**:
The 48-hour period before a pump during which signals must be observable. A signal that appears 72 hours before is stale; one that appears 2 hours before is confirmation, not prediction.

**Asset Universe**:
Top 150 tokens by market cap listed on Binance spot, filtered to those with ≥$1M daily trading volume. Universe refreshed weekly (Monday) from Binance API or CoinGecko. Volume floor checked daily — tokens falling below are excluded for that run.
_Avoid_: Watchlist, eligible set

**Quantitative Signal**:
A signal that can be expressed as a numeric value without human judgment — e.g., funding rate, TVL, wallet count.
_Avoid_: Hard data, metric, indicator

**Qualitative Signal**:
A signal requiring human interpretation — e.g., governance proposal significance, narrative momentum on X.
_Avoid_: Soft signal, sentiment

**Smart-Money Accumulation (v1)**:
The percentage growth in the number of unique wallets holding ≥$1,000 worth of a token over a 48-hour period. Fires when growth rate ranks in the top 5% of the asset universe over the same window (cross-sectional, matching the funding-rate pattern). Sourced from free-tier Dune queries.
_Avoid_: Whale buying, rich-wallet inflow

**Pump Score**:
A binary count (0–5) of how many Phase 1 signals have fired. A score of ≥2 produces a Pump Alert. Signals: funding-rate extreme (Binance), OI/price divergence (Binance), long/short ratio extreme (Binance), taker buy/sell ratio extreme (Binance), order book bid dominance (Binance).
_Avoid_: Confidence, rating, probability

**OI/Price Divergence**:
Open Interest % change minus price % change over a 7-day lookback. Rising OI with flat or falling price signals accumulation. Fires when divergence ranks in the top 5% of the universe (cross-sectional) AND the token's price has not already risen ≥5%.
_Avoid_: OI delta, OI/price spread

**Long/Short Ratio Extreme**:
The token's current long/short ratio falls at or below its own 90-day rolling 2nd percentile AND ranks in the bottom 5% cross-sectionally. Contrarian: extremely low L/S ratio means the crowd is bearish — bullish signal.
_Avoid_: LS ratio, sentiment ratio

**Funding-Rate Extreme**:
A token-specific contrarian signal: the token's current funding rate falls at or below its own 90-day rolling 2nd percentile, AND ranks in the bottom 5% of all funding rates across the universe. Only negative extremes signal a buy (shorts are overcrowded); extreme positive is bearish/avoid, not a short-sell signal.
_Avoid_: Overleveraged, crowded long, basis anomaly

## Relationships

- Each **Pump** is predicted by one or more **Pump Signals** converging within the **Lookahead Window**
- **Quantitative Signals** feed directly into the **Pump Score**; **Qualitative Signals** are tagged but do not enter the score in Phase 1
- A token must be in the **Asset Universe** to be scored

## Approach (phased)

- **Phase 1**: Quantitative-only scoring model using 3 signals from Binance (funding-rate extremes, OI/price divergence, long/short ratio extreme — equal weight). Backtest shows 25% precision / PF 3.50 on top 50 tokens.
- **Phase 2**: Qualitative tagging layer (governance events, social momentum) as boost/dampen modifiers on top of the quantitative score.
- **Phase 3**: On-chain signals (wallet growth, CEX flows) via Nansen/Arkham when budget permits. Dune free tier insufficient for raw transfer queries.

## Backtesting Phasing

**Phase 1a (funding-only backtest)**: Backtested funding-rate extreme alone against Binance historical data. Result: 9% precision, PF 1.93. Established signal edge but too noisy alone.

**Phase 1b (3-signal backtest)**: All 3 Binance signals (funding, OI divergence, LS ratio) with ≥2/3 alert threshold. Result: 25% precision, PF 3.50 on top 50 tokens. 93% noise reduction vs funding-only. PF passes go bar; precision improving but below 50%.

**Phase 1c (go-live decision)**: If full backtest hits precision ≥50% AND profit factor ≥1.5, enable live trading. Currently PF ✅, precision ❌. Phase 2 qualitative layer expected to close the gap.

## Go/No-Go Bar

Before any live trading, the rolling walk-forward backtest must show **precision ≥50%** (of alerts followed by a ≥15% pump within 48h) AND **profit factor ≥1.5** (gross profit / gross loss across all simulated trades using the take-profit ladder and -7% stop). Below either bar, iterate in order: (1) tighten signal thresholds, (2) segment by market regime and retest. Do not add signals or change the universe until the original 3 are understood.
_Avoid_: Accuracy, hit rate

## Daily Workflow

1. **08:00 UTC** — Cron triggers pipeline: Binance funding data + Dune on-chain data pulled, scored, written to SQLite.
2. CSV of Pump Alerts (≥2/3 signals fired) produced.
3. Human reviews each alert with a **5-minute sanity check**: scan for catastrophic news (hack, delisting, founder arrest, regulatory action). The model catches quantitative signals; it cannot see these.
4. If alert passes, **manually** place spot orders on Binance: OCO with take-profit ladder (50% at +15%, 30% at +25%, 20% trailing stop -3%) and hard stop-loss at -7%.
5. Log outcome for performance tracking via SQLite `trades` table (token, alert_date, entry_price, exit_price, exit_reason, pnl_pct, signals_fired). Phase 1 execution is manual — automation via Binance API comes after validation.

## Example dialogue

> **Dev:** "TOKEN_X fired on wallet-count growth and CEX outflow, but funding isn't extreme — that's 2/3. Do we alert?"
> **Domain expert:** "Yes, that's a Pump Alert. The funding signal is contrarian only — it doesn't need to fire for a buy. Two signals firing means the accumulation and flow data agree, and funding isn't screaming overbought."

## Data Sources

- **Binance** (public REST API — no key required): Spot markets (exchangeInfo, 24h ticker, klines, order book depth), USDⓈ-M Futures (funding rates, open interest, open interest history), Futures Data (global long/short account ratio, taker buy/sell volume ratio). All 5 quantitative signals sourced from Binance public endpoints. Historical depth: funding ~deep, OI ~30 days, L/S ratio ~30 days.
- **Dune** (account held): On-chain wallet counts, CEX exchange wallet flows. SQL queries via Dune API. Source for wallet-count growth and CEX inflow anomaly signals.
- **Exchange wallet labeling**: Fork an existing community-maintained Dune dashboard for CEX flow tracking. Fallback: Dune `labels.cex` tables. Upgrade to Arkham/Nansen address labels in Phase 3 if quality is poor.

## Tech Stack

Python 3 with: `requests` for Binance API, `dune-client` for Dune queries, `pandas` for signal scoring and aggregation, SQLite for alert history, backtest results, and signal state. Run via cron/systemd timer for daily batch. Backtesting uses rolling walk-forward on the same stack.
_Avoid_: Real-time stream, web dashboard (Phase 1)

## Output

- **Phase 1**: Daily batch output — Pump Alerts as a CSV/terminal report, generated once per day. Human reviews and decides which to trade.
- **Later**: Push alerts (Telegram/Discord) when ≥2/3 signals fire in near-real-time.

## Position Sizing

10% of portfolio (~$1K starting, $100 per alert) per Pump Alert, max 5 concurrent positions (50% of portfolio deployed). If a 6th alert fires while 5 are open, skip it or manually replace the weakest open position. No scaling by conviction in Phase 1 — equal size per alert. Pipeline computes and prints position sizes in the daily CSV.
_Avoid_: Bet size, allocation, risk per trade

## Exit Strategy

Take-profit ladder: Sell 50% at +15%, 30% at +25%, 20% held with trailing stop (-3% from peak). Hard stop-loss at -7% on the full position. Exit is triggered by price, not signal decay.
_Avoid_: DCA out, scaling out

## Flagged ambiguities

- "Alpha" was used to mean both actionable trading signals and general market insight — resolved: use **Pump Signal** for the former.
