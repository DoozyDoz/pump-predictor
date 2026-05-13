"""
Dune SQL queries for Phase 1b on-chain signals.

Designed for Dune free tier (2-minute execution timeout).
Uses known table schemas: erc20_ethereum.evt_Transfer, tokens.erc20, prices.usd.

Query 1: Active-address growth — % change in unique transactors over 48h.
Query 2: CEX net outflow — exchange-labeled outflows vs inflows over 48h.
"""

# ---------------------------------------------------------------------------
# Query 1: Active-address growth (proxy for wallet-count growth)
# ---------------------------------------------------------------------------
# Counts unique addresses that sent/received the token in the last 48h
# vs the previous 48h. A spike in new active addresses signals growing interest.
# This is cheaper than computing full balance snapshots.

ACTIVE_ADDRESS_GROWTH_QUERY = """
WITH token_list AS (
    SELECT * FROM (VALUES
        {token_values}
    ) AS t(contract_address, symbol)
),
-- Unique addresses active in last 48h
recent_active AS (
    SELECT
        tr.contract_address,
        COUNT(DISTINCT tr."from") + COUNT(DISTINCT tr."to") AS active_count
    FROM erc20_ethereum.evt_Transfer AS tr
    INNER JOIN token_list AS t
        ON LOWER(tr.contract_address) = LOWER(t.contract_address)
    WHERE tr.evt_block_time >= NOW() - INTERVAL '48' HOUR
    GROUP BY tr.contract_address
),
-- Unique addresses active in the 48h before that
prior_active AS (
    SELECT
        tr.contract_address,
        COUNT(DISTINCT tr."from") + COUNT(DISTINCT tr."to") AS active_count
    FROM erc20_ethereum.evt_Transfer AS tr
    INNER JOIN token_list AS t
        ON LOWER(tr.contract_address) = LOWER(t.contract_address)
    WHERE tr.evt_block_time >= NOW() - INTERVAL '96' HOUR
      AND tr.evt_block_time < NOW() - INTERVAL '48' HOUR
    GROUP BY tr.contract_address
)
SELECT
    t.symbol,
    COALESCE(r.active_count, 0) AS recent_active,
    COALESCE(p.active_count, 0) AS prior_active,
    CASE
        WHEN COALESCE(p.active_count, 0) > 0
        THEN (COALESCE(r.active_count, 0) - p.active_count) * 100.0 / p.active_count
        ELSE NULL
    END AS growth_pct
FROM token_list AS t
LEFT JOIN recent_active AS r
    ON LOWER(r.contract_address) = LOWER(t.contract_address)
LEFT JOIN prior_active AS p
    ON LOWER(p.contract_address) = LOWER(t.contract_address)
ORDER BY growth_pct DESC NULLS LAST
"""

# ---------------------------------------------------------------------------
# Query 2: CEX net outflow (simplified)
# ---------------------------------------------------------------------------
# Uses Dune's labels.cex for exchange wallet identification.
# Tracks net token flow from exchange wallets in the last 48h vs 30-day mean.

CEX_OUTFLOW_QUERY = """
WITH token_list AS (
    SELECT * FROM (VALUES
        {token_values}
    ) AS t(contract_address, symbol)
),
-- Known exchange wallet addresses from Dune labels
exchange_wallets AS (
    SELECT DISTINCT address
    FROM labels.cex
    WHERE LOWER(name) LIKE '%binance%'
       OR LOWER(name) LIKE '%coinbase%'
       OR LOWER(name) LIKE '%kraken%'
       OR LOWER(name) LIKE '%okx%'
       OR LOWER(name) LIKE '%bybit%'
       OR LOWER(name) LIKE '%gate%'
       OR LOWER(name) LIKE '%kucoin%'
       OR LOWER(name) LIKE '%huobi%'
       OR LOWER(name) LIKE '%upbit%'
),
-- Outflows (exchange -> non-exchange) per token in last 48h
outflows_48h AS (
    SELECT
        tr.contract_address,
        SUM(tr.value) AS total_outflow
    FROM erc20_ethereum.evt_Transfer AS tr
    INNER JOIN token_list AS t
        ON LOWER(tr.contract_address) = LOWER(t.contract_address)
    WHERE tr.evt_block_time >= NOW() - INTERVAL '48' HOUR
      AND LOWER(tr."from") IN (SELECT LOWER(address) FROM exchange_wallets)
      AND LOWER(tr."to") NOT IN (SELECT LOWER(address) FROM exchange_wallets)
    GROUP BY tr.contract_address
),
-- Inflows (non-exchange -> exchange) per token in last 48h
inflows_48h AS (
    SELECT
        tr.contract_address,
        SUM(tr.value) AS total_inflow
    FROM erc20_ethereum.evt_Transfer AS tr
    INNER JOIN token_list AS t
        ON LOWER(tr.contract_address) = LOWER(t.contract_address)
    WHERE tr.evt_block_time >= NOW() - INTERVAL '48' HOUR
      AND LOWER(tr."to") IN (SELECT LOWER(address) FROM exchange_wallets)
      AND LOWER(tr."from") NOT IN (SELECT LOWER(address) FROM exchange_wallets)
    GROUP BY tr.contract_address
),
-- Daily outflows for last 30 days (for mean/std calculation)
daily_outflows AS (
    SELECT
        tr.contract_address,
        DATE_TRUNC('day', tr.evt_block_time) AS day,
        SUM(tr.value) AS daily_outflow
    FROM erc20_ethereum.evt_Transfer AS tr
    INNER JOIN token_list AS t
        ON LOWER(tr.contract_address) = LOWER(t.contract_address)
    WHERE tr.evt_block_time >= NOW() - INTERVAL '30' DAY
      AND LOWER(tr."from") IN (SELECT LOWER(address) FROM exchange_wallets)
      AND LOWER(tr."to") NOT IN (SELECT LOWER(address) FROM exchange_wallets)
    GROUP BY tr.contract_address, DATE_TRUNC('day', tr.evt_block_time)
),
-- 30-day statistics
outflow_stats AS (
    SELECT
        contract_address,
        AVG(daily_outflow) AS mean_daily,
        STDDEV(daily_outflow) AS std_daily,
        COUNT(*) AS days_with_data
    FROM daily_outflows
    GROUP BY contract_address
)
SELECT
    t.symbol,
    COALESCE(o.total_outflow, 0) AS outflow_48h,
    COALESCE(i.total_inflow, 0) AS inflow_48h,
    COALESCE(o.total_outflow, 0) - COALESCE(i.total_inflow, 0) AS net_outflow_48h,
    COALESCE(s.mean_daily, 0) AS mean_daily_outflow,
    COALESCE(s.std_daily, 0) AS std_daily_outflow,
    CASE
        WHEN COALESCE(s.std_daily, 0) > 0
        THEN (COALESCE(o.total_outflow, 0) - COALESCE(s.mean_daily, 0)) / s.std_daily
        ELSE NULL
    END AS outflow_std,
    CASE
        WHEN COALESCE(i.total_inflow, 0) > 0
        THEN COALESCE(o.total_outflow, 0) * 1.0 / i.total_inflow
        ELSE NULL
    END AS outflow_inflow_ratio
FROM token_list AS t
LEFT JOIN outflows_48h AS o
    ON LOWER(o.contract_address) = LOWER(t.contract_address)
LEFT JOIN inflows_48h AS i
    ON LOWER(i.contract_address) = LOWER(t.contract_address)
LEFT JOIN outflow_stats AS s
    ON LOWER(s.contract_address) = LOWER(t.contract_address)
ORDER BY outflow_std DESC NULLS LAST
"""


def build_token_values_param(tokens: list[dict]) -> str:
    """
    Build the VALUES clause for token contract addresses.
    tokens: [{'symbol': 'PEPE', 'address': '0x...'}]
    """
    values = []
    for t in tokens:
        values.append(f"('{t['address']}', '{t['symbol']}')")
    return ",\n        ".join(values)
