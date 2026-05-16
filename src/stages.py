"""
Stage state machine for the staged pump-alert workflow.

Defines Stage enum for workflow progression and StageManager for persistence
via the watchlist and stage_progression tables.
"""

from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from src.db import db_session
from src.config import WATCHLIST_TTL_HOURS, CONFIRMATION_TTL_HOURS


class Stage(Enum):
    WATCHLIST = "watchlist"
    CONFIRMATION = "confirmation"
    ENTRY = "entry"
    EXPIRED = "expired"


class StageManager:
    """Manages stage progression through watchlist -> confirmation -> entry."""

    # ------------------------------------------------------------------
    # Write helpers
    # ------------------------------------------------------------------

    def add_to_watchlist(
        self,
        token_id: int,
        symbol: str,
        score: int,
        signals: str,
        boost: float = 0.0,
    ) -> Optional[int]:
        """Add a token to the watchlist. Returns watchlist id.
        If the token already exists in the watchlist (not expired), updates it."""
        with db_session() as conn:
            existing = conn.execute(
                "SELECT id FROM watchlist WHERE token_id = ? AND expired = FALSE",
                (token_id,),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE watchlist SET score=?, signals_fired=?, catalyst_boost=? WHERE id=?",
                    (score, signals, boost, existing[0]),
                )
                return existing[0]
            cur = conn.execute(
                "INSERT INTO watchlist (token_id, symbol, score, signals_fired, catalyst_boost) "
                "VALUES (?, ?, ?, ?, ?)",
                (token_id, symbol, score, signals, boost),
            )
            wl_id = cur.lastrowid
            conn.execute(
                "INSERT INTO stage_progression (watchlist_id, token_id, stage) VALUES (?, ?, ?)",
                (wl_id, token_id, Stage.WATCHLIST.value),
            )
            return wl_id

    def promote_to_confirmation(self, watchlist_id: int, reason: str = ""):
        """Promote a watchlist candidate to confirmation stage."""
        now = datetime.utcnow().isoformat()
        with db_session() as conn:
            conn.execute(
                "UPDATE stage_progression SET stage=?, promoted_ts=?, reason=? "
                "WHERE watchlist_id=? AND stage=?",
                (Stage.CONFIRMATION.value, now, reason, watchlist_id, Stage.WATCHLIST.value),
            )

    def promote_to_entry(self, watchlist_id: int, reason: str = ""):
        """Promote a confirmation candidate to entry stage."""
        now = datetime.utcnow().isoformat()
        with db_session() as conn:
            conn.execute(
                "UPDATE stage_progression SET stage=?, promoted_ts=?, reason=? "
                "WHERE watchlist_id=? AND stage=?",
                (Stage.ENTRY.value, now, reason, watchlist_id, Stage.CONFIRMATION.value),
            )

    def expire(self, watchlist_id: int, reason: str = ""):
        """Mark a watchlist item as expired."""
        now = datetime.utcnow().isoformat()
        with db_session() as conn:
            conn.execute("UPDATE watchlist SET expired=TRUE WHERE id=?", (watchlist_id,))
            conn.execute(
                "UPDATE stage_progression SET stage=?, expired_ts=?, reason=? "
                "WHERE watchlist_id=? AND stage NOT IN ('entry', 'expired')",
                (Stage.EXPIRED.value, now, reason, watchlist_id),
            )

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def get_watchlist_candidates(self, hours: int = 72) -> list[dict]:
        """Return active watchlist items (not yet promoted or expired)."""
        cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
        with db_session() as conn:
            rows = conn.execute("""
                SELECT w.* FROM watchlist w
                JOIN stage_progression sp ON sp.watchlist_id = w.id
                WHERE sp.stage = 'watchlist' AND w.expired = FALSE
                  AND w.added_ts >= ?
                ORDER BY w.score DESC
            """, (cutoff,)).fetchall()
        return [dict(r) for r in rows]

    def get_confirmation_candidates(self, hours: int = 24) -> list[dict]:
        """Return active confirmation items (promoted but not yet entry or expired)."""
        cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
        with db_session() as conn:
            rows = conn.execute("""
                SELECT w.*, sp.promoted_ts, sp.reason FROM watchlist w
                JOIN stage_progression sp ON sp.watchlist_id = w.id
                WHERE sp.stage = 'confirmation' AND w.expired = FALSE
                  AND sp.promoted_ts >= ?
                ORDER BY w.score DESC
            """, (cutoff,)).fetchall()
        return [dict(r) for r in rows]

    def get_by_stage(self, stage: str) -> list[dict]:
        """Get all watchlist items currently at a given stage."""
        with db_session() as conn:
            rows = conn.execute("""
                SELECT w.*, sp.promoted_ts, sp.reason FROM watchlist w
                JOIN stage_progression sp ON sp.watchlist_id = w.id
                WHERE sp.stage = ? AND w.expired = FALSE
                ORDER BY w.score DESC
            """, (stage,)).fetchall()
        return [dict(r) for r in rows]

    def get_all_active(self) -> list[dict]:
        """Get all non-expired watchlist items regardless of stage."""
        with db_session() as conn:
            rows = conn.execute("""
                SELECT w.*, sp.stage, sp.promoted_ts, sp.reason FROM watchlist w
                JOIN stage_progression sp ON sp.watchlist_id = w.id
                WHERE w.expired = FALSE
                ORDER BY w.score DESC
            """).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def expire_stale(self):
        """Expire items past their TTL."""
        wl_cutoff = (datetime.utcnow() - timedelta(hours=WATCHLIST_TTL_HOURS)).isoformat()
        conf_cutoff = (datetime.utcnow() - timedelta(hours=CONFIRMATION_TTL_HOURS)).isoformat()
        with db_session() as conn:
            # Expire stale watchlist items
            stale_wl = conn.execute("""
                SELECT w.id FROM watchlist w
                JOIN stage_progression sp ON sp.watchlist_id = w.id
                WHERE sp.stage = 'watchlist' AND w.added_ts < ? AND w.expired = FALSE
            """, (wl_cutoff,)).fetchall()
            for row in stale_wl:
                conn.execute("UPDATE watchlist SET expired=TRUE WHERE id=?", (row[0],))
                conn.execute(
                    "UPDATE stage_progression SET stage=?, expired_ts=?, reason=? WHERE watchlist_id=?",
                    (Stage.EXPIRED.value, datetime.utcnow().isoformat(), "TTL expired", row[0]),
                )

            # Expire stale confirmation items
            stale_conf = conn.execute("""
                SELECT w.id FROM watchlist w
                JOIN stage_progression sp ON sp.watchlist_id = w.id
                WHERE sp.stage = 'confirmation' AND sp.promoted_ts < ?
                  AND w.expired = FALSE
            """, (conf_cutoff,)).fetchall()
            for row in stale_conf:
                conn.execute("UPDATE watchlist SET expired=TRUE WHERE id=?", (row[0],))
                conn.execute(
                    "UPDATE stage_progression SET stage=?, expired_ts=?, reason=? WHERE watchlist_id=?",
                    (Stage.EXPIRED.value, datetime.utcnow().isoformat(), "Confirmation TTL expired", row[0]),
                )
