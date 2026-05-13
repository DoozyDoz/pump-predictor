# Backtest Phasing: Funding-rate first, on-chain data collected live

We chose to backtest the funding-rate extreme signal in isolation first (using 2 years of CoinAnalyze historical data), while collecting wallet-count and CEX-flow signals live without trading for ≥6 months before running the full 3-signal backtest.

**Why**: Dune cannot easily query historical on-chain state ("how many wallets held ≥$1K of this token on June 13, 2024?"). The free path to historical on-chain data is to start collecting it now and wait. The paid path (Nansen/Arkham) costs money before we've proven any signal has predictive power. Rather than spend upfront, we validate the strongest standalone signal (funding-rate extremes) first — if it doesn't show an edge, the on-chain signals wouldn't have saved it anyway.

**Why not pay for Nansen/Arkham immediately**: If funding-rate extremes alone show no predictive power in backtest, the paid data would be wasted. Proving the approach works on one clean signal before buying more data is capital-efficient.

**Consequence**: Full 3-signal backtest results are delayed by ~6 months. Phase 1 trades only based on funding-rate signals during that window (relaxed to single-signal alerts). On-chain signals (wallet growth, CEX flows) run passively to build the dataset.

**Status**: accepted
