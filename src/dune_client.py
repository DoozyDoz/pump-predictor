"""Dune Analytics client — direct API wrapper (bypasses buggy SDK execute method)."""

import time
import json
import requests
import pandas as pd
from datetime import datetime
from typing import Optional
from src.config import DUNE_API_KEY, WALLET_MIN_BALANCE_USD
from src.dune_queries import ACTIVE_ADDRESS_GROWTH_QUERY, CEX_OUTFLOW_QUERY

DUNE_API = "https://api.dune.com/api/v1"
HEADERS = {"X-Dune-API-Key": DUNE_API_KEY, "Content-Type": "application/json"}


class DuneQueryError(Exception):
    pass


def _post(route: str, body: dict | None = None) -> dict:
    resp = requests.post(f"{DUNE_API}{route}", headers=HEADERS, json=body or {}, timeout=30)
    if resp.status_code != 200:
        raise DuneQueryError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def _get(route: str) -> dict:
    resp = requests.get(f"{DUNE_API}{route}", headers=HEADERS, timeout=30)
    if resp.status_code != 200:
        raise DuneQueryError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def create_query(name: str, sql: str, is_private: bool = False) -> int:
    """Create a Dune query and return its ID."""
    data = _post("/query/", {
        "name": name,
        "query_sql": sql,
        "is_private": is_private,
    })
    return data["query_id"]


def execute_query(query_id: int, params: dict | None = None) -> str:
    """Execute a query, return execution_id."""
    body = {"query_parameters": params or {}}
    data = _post(f"/query/{query_id}/execute", body)
    return data["execution_id"]


def get_status(execution_id: str) -> dict:
    """Get execution status. Returns {state, ...}."""
    return _get(f"/execution/{execution_id}/status")


def get_results_csv(execution_id: str, limit: int = 32000) -> str:
    """Get execution results as CSV string."""
    resp = requests.get(
        f"{DUNE_API}/execution/{execution_id}/results/csv",
        headers=HEADERS,
        params={"limit": limit},
        timeout=60,
    )
    if resp.status_code != 200:
        raise DuneQueryError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.text


def wait_for_completion(execution_id: str, max_wait: int = 300) -> str:
    """Wait for execution to complete. Returns final state."""
    waited = 0
    while waited < max_wait:
        status = get_status(execution_id)
        state = status["state"]
        if state in ("QUERY_STATE_COMPLETED", "QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"):
            return state
        time.sleep(5)
        waited += 5
    return "TIMEOUT"


def run_query(query_id: int, params: dict | None = None, wait: bool = True) -> pd.DataFrame:
    """Execute a Dune query and return results as DataFrame."""
    execution_id = execute_query(query_id, params)
    if wait:
        state = wait_for_completion(execution_id)
        if state == "QUERY_STATE_FAILED":
            raise DuneQueryError(f"Query {query_id} execution failed")
        if state != "QUERY_STATE_COMPLETED":
            raise DuneQueryError(f"Query {query_id} ended in state: {state}")
    csv_data = get_results_csv(execution_id)
    if not csv_data.strip():
        return pd.DataFrame()
    return pd.read_csv(pd.io.common.StringIO(csv_data))


def run_sql(sql: str) -> pd.DataFrame:
    """One-shot: create, execute, fetch, delete a query."""
    qid = create_query(f"adhoc_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}", sql)
    try:
        return run_query(qid)
    finally:
        pass  # queries persist — we keep them for debugging


def fetch_token_addresses(symbols: list[str]) -> dict[str, str]:
    """
    Map token symbols to Ethereum contract addresses via Dune's tokens.erc20 table.
    Returns {symbol: contract_address}.
    """
    if not symbols:
        return {}
    quoted = ",".join(f"'{s}'" for s in symbols)
    sql = f"""
        SELECT symbol, contract_address
        FROM tokens.erc20
        WHERE symbol IN ({quoted})
          AND blockchain = 'ethereum'
        ORDER BY symbol
    """
    df = run_sql(sql)
    if df.empty:
        return {}
    mapping = {}
    for _, row in df.iterrows():
        sym = row.get("symbol", "")
        addr = row.get("contract_address", "")
        if sym and addr and sym not in mapping:
            mapping[sym] = addr
    return mapping


def fetch_active_address_growth(token_addresses: list[dict]) -> pd.DataFrame:
    """
    Fetch active-address growth for tokens.
    token_addresses: [{'symbol': 'WLD', 'address': '0x...'}]
    Returns DataFrame with: symbol, recent_active, prior_active, growth_pct
    """
    from src.dune_queries import build_token_values_param
    values = build_token_values_param(token_addresses)
    sql = ACTIVE_ADDRESS_GROWTH_QUERY.format(token_values=values)
    return run_sql(sql)


def fetch_cex_netflow(token_addresses: list[dict]) -> pd.DataFrame:
    """Fetch CEX net outflow for tokens."""
    from src.dune_queries import build_token_values_param
    values = build_token_values_param(token_addresses)
    sql = CEX_OUTFLOW_QUERY.format(token_values=values)
    return run_sql(sql)
