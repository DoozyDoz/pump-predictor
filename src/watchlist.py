"""
Watchlist generation — Phase 1 of the staged workflow.

Extracted from the current pipeline._build_alerts(). Uses a lower threshold
to cast a wider net, storing candidates for later confirmation.
"""

from src.config import (
    WATCHLIST_THRESHOLD,
    ALERT_THRESHOLD,
    CATALYST_WEIGHT,
    TECHNICAL_SETUP_WEIGHT,
    CONFIRMATION_WEIGHT,
    CATALYST_MIN_WATCHLIST_SCORE,
    CATALYST_MAJOR_SCORE,
)
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
    catalyst_results: dict | None = None,
    price_changes: dict[str, dict[str, float | None]] | None = None,
) -> list[dict]:
    """
    Score every symbol using the same 5-signal framework as _build_alerts(),
    but apply WATCHLIST_THRESHOLD (lower than ALERT_THRESHOLD) so we catch
    early setups. Results are persisted via StageManager.

    Returns the list of watchlist candidates dicts.
    """
    stage_mgr = StageManager()
    catalyst_results = catalyst_results or {}

    # Index signals by symbol for fast lookup
    f_map = {s.symbol: s for s in funding_signals}
    oi_map = {s.symbol: s for s in oi_signals}
    ls_map = {s.symbol: s for s in ls_signals}
    t_map = {s.symbol: s for s in taker_signals}
    b_map = {s.symbol: s for s in book_signals}
    all_syms = (
        set(f_map) | set(oi_map) | set(ls_map) | set(t_map) | set(b_map)
        | set(catalyst_results.keys())
    )

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

        # Catalyst scoring
        catalyst_result = catalyst_results.get(sym)
        catalyst_score = catalyst_result.score if catalyst_result else 0.0
        technical_setup_score = score / 5.0
        final_alpha_score = (
            CATALYST_WEIGHT * catalyst_score
            + TECHNICAL_SETUP_WEIGHT * technical_setup_score
            + CONFIRMATION_WEIGHT * 0.0
        )

        # Two-tier negative catalyst handling
        has_blocking = catalyst_result and catalyst_result.has_blocking_negative_catalyst
        is_negative = catalyst_result and catalyst_result.is_negative_catalyst

        if has_blocking:
            # Blocking negative catalysts prevent normal bullish watchlist
            continue
        if is_negative and technical_setup_score < 0.8:
            # Warning negative catalysts block weak setups
            continue

        # Determine watchlist eligibility
        catalyst_qualifies = catalyst_score >= CATALYST_MIN_WATCHLIST_SCORE
        technical_qualifies = score >= WATCHLIST_THRESHOLD
        combined_qualifies = final_alpha_score >= (ALERT_THRESHOLD / 5.0)

        if not (catalyst_qualifies or technical_qualifies or combined_qualifies):
            continue

        # Minimum quant signal rule (same as pipeline)
        catalyst_present = cat_boost >= 0.5
        if score < 2 and not (score >= 1 and catalyst_present):
            # Allow catalyst-only override if strong enough
            if not catalyst_qualifies:
                continue

        # Require at least one strong derivative signal (unless catalyst-only)
        STRONG_SIGNALS = {"funding_extreme", "oi_divergence", "ls_extreme"}
        if not (set(fired) & STRONG_SIGNALS):
            if not catalyst_qualifies:
                continue

        # Determine labels
        setup_type = "CATALYST_WATCH" if catalyst_qualifies else "TECHNICAL"
        priority = "URGENT_CATALYST" if catalyst_score >= CATALYST_MAJOR_SCORE else ""

        # Use legacy adjusted score for reference if no catalyst
        adjusted_score, _ = qualitative_override(score, cat_boost, ALERT_THRESHOLD)

        sym_price_changes = (price_changes or {}).get(sym, {})
        candidate = {
            "symbol": sym,
            "score": score,
            "fired_signals": "|".join(fired),
            "catalyst_boost": cat_boost,
            "adjusted_score": adjusted_score,
            "catalyst_score": catalyst_score,
            "setup_type": setup_type,
            "priority": priority,
            "final_alpha_score": final_alpha_score,
            "catalyst_event_type": catalyst_result.dominant_event_type if catalyst_result else "",
            "catalyst_title": (
                catalyst_result.events[0].title if catalyst_result and catalyst_result.events else ""
            ),
            "catalyst_source": (
                catalyst_result.events[0].source if catalyst_result and catalyst_result.events else ""
            ),
            "catalyst_published_at": (
                catalyst_result.events[0].published_at if catalyst_result and catalyst_result.events else ""
            ),
            # Two-tier negative catalyst fields
            "is_negative_catalyst": is_negative,
            "has_blocking_negative_catalyst": has_blocking,
            "negative_catalyst_types": (
                catalyst_result.negative_catalyst_types if catalyst_result else []
            ),
            "negative_catalyst_severities": (
                catalyst_result.negative_catalyst_severities if catalyst_result else []
            ),
            "negative_catalyst_reasons": (
                catalyst_result.negative_catalyst_reasons if catalyst_result else []
            ),
            "catalyst_event_ids": (
                catalyst_result.catalyst_event_ids if catalyst_result else []
            ),
            # Price reaction fields
            "price_change_1h": sym_price_changes.get("1h"),
            "price_change_4h": sym_price_changes.get("4h"),
            "price_change_24h": sym_price_changes.get("24h"),
        }
        candidates.append(candidate)

    # Persist candidates via StageManager
    for c in candidates:
        _persist_watchlist_candidate(c, stage_mgr)

    return candidates


import json

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
        token_id = row[0] if row else None

    if not token_id:
        return

    wl_id = stage_mgr.add_to_watchlist(
        token_id=token_id,
        symbol=sym,
        score=candidate["score"],
        signals=candidate["fired_signals"],
        boost=candidate["catalyst_boost"],
    )

    # Update catalyst columns (new ones are added safely via init_db migration)
    with db_session() as conn:
        try:
            conn.execute(
                """UPDATE watchlist SET
                    catalyst_score = ?,
                    catalyst_event_type = ?,
                    catalyst_title = ?,
                    catalyst_source = ?,
                    catalyst_published_at = ?,
                    final_alpha_score = ?,
                    priority = ?,
                    setup_type = ?,
                    is_negative_catalyst = ?,
                    has_blocking_negative_catalyst = ?,
                    negative_catalyst_types = ?,
                    negative_catalyst_severities = ?,
                    negative_catalyst_reasons = ?,
                    catalyst_event_ids = ?,
                    price_change_1h = ?,
                    price_change_4h = ?,
                    price_change_24h = ?
                WHERE id = ?""",
                (
                    candidate.get("catalyst_score", 0.0),
                    candidate.get("catalyst_event_type", ""),
                    candidate.get("catalyst_title", ""),
                    candidate.get("catalyst_source", ""),
                    candidate.get("catalyst_published_at", ""),
                    candidate.get("final_alpha_score", 0.0),
                    candidate.get("priority", ""),
                    candidate.get("setup_type", ""),
                    1 if candidate.get("is_negative_catalyst") else 0,
                    1 if candidate.get("has_blocking_negative_catalyst") else 0,
                    json.dumps(candidate.get("negative_catalyst_types", [])),
                    json.dumps(candidate.get("negative_catalyst_severities", [])),
                    json.dumps(candidate.get("negative_catalyst_reasons", [])),
                    json.dumps(candidate.get("catalyst_event_ids", [])),
                    candidate.get("price_change_1h"),
                    candidate.get("price_change_4h"),
                    candidate.get("price_change_24h"),
                    wl_id,
                ),
            )
        except Exception:
            pass
