"""
Watchlist generation — Phase 1 of the staged workflow.

Extracted from the current pipeline._build_alerts(). Uses a lower threshold
to cast a wider net, storing candidates for later confirmation.
"""

from src.config import WATCHLIST_THRESHOLD, ALERT_THRESHOLD
from src.stages import StageManager
from src.qualitative import qualitative_override


def generate_watchlist(
    funding_signals: list,
    oi_signals: list,
    ls_signals: list,
    taker_signals: list,
    book_signals: list,
    qual_profiles: dict,
    regime=None,
) -> list[dict]:
    """
    Score every symbol using the same 5-signal framework as _build_alerts(),
    but apply WATCHLIST_THRESHOLD (lower than ALERT_THRESHOLD) so we catch
    early setups. Results are persisted via StageManager.

    Returns the list of watchlist candidates dicts.
    """
    stage_mgr = StageManager()

    # Index signals by symbol for fast lookup
    f_map = {s.symbol: s for s in funding_signals}
    oi_map = {s.symbol: s for s in oi_signals}
    ls_map = {s.symbol: s for s in ls_signals}
    t_map = {s.symbol: s for s in taker_signals}
    b_map = {s.symbol: s for s in book_signals}
    all_syms = set(f_map) | set(oi_map) | set(ls_map) | set(t_map) | set(b_map)

    candidates = []

    for sym in sorted(all_syms):
        score = 0
        fired = []

        # Quantitative scoring (same as pipeline._build_alerts)
        fs = f_map.get(sym)
        if fs and fs.fired:
            score += 1
            fired.append("funding_extreme")

        oi_s = oi_map.get(sym)
        if oi_s and oi_s.fired:
            score += 1
            fired.append("oi_divergence")

        ls_s = ls_map.get(sym)
        if ls_s and ls_s.fired:
            score += 1
            fired.append("ls_extreme")

        t_s = t_map.get(sym)
        if t_s and t_s.fired:
            score += 1
            fired.append("taker_extreme")

        b_s = b_map.get(sym)
        if b_s and b_s.fired:
            score += 1
            fired.append("book_imbalance")

        # Blocking filters
        profile = qual_profiles.get(sym)
        if profile and profile.blocked:
            continue

        cat_boost = profile.catalyst_boost if profile else 0.0

        # Apply threshold (lower than alert threshold)
        if score < WATCHLIST_THRESHOLD:
            continue

        # Minimum quant signal rule (same as pipeline)
        catalyst_present = cat_boost >= 0.5
        if score < 2 and not (score >= 1 and catalyst_present):
            continue

        # Require at least one strong derivative signal
        STRONG_SIGNALS = {"funding_extreme", "oi_divergence", "ls_extreme"}
        if not (set(fired) & STRONG_SIGNALS):
            continue

        # Compute adjusted score for reference
        adjusted_score, _ = qualitative_override(score, cat_boost, ALERT_THRESHOLD)

        candidates.append({
            "symbol": sym,
            "score": score,
            "fired_signals": "|".join(fired),
            "catalyst_boost": cat_boost,
            "adjusted_score": adjusted_score,
        })

    # Persist candidates via StageManager
    for c in candidates:
        _persist_watchlist_candidate(c, stage_mgr)

    return candidates


def _persist_watchlist_candidate(candidate: dict, stage_mgr: StageManager):
    """Insert or update the candidate in the watchlist via StageManager."""
    from src.db import db_session

    sym = candidate["symbol"]
    with db_session() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO tokens (symbol, exchange, market) VALUES (?, 'B', 'spot')",
            (sym,),
        )
        row = conn.execute(
            "SELECT id FROM tokens WHERE symbol = ? AND exchange = 'B' AND market = 'spot'",
            (sym,),
        ).fetchone()
        if row:
            stage_mgr.add_to_watchlist(
                token_id=row[0],
                symbol=sym,
                score=candidate["score"],
                signals=candidate["fired_signals"],
                boost=candidate["catalyst_boost"],
            )
