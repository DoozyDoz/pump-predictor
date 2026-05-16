"""Catalyst scoring engine for qualitative/news alpha.

Replaces the simplistic catalyst_boost with a full multi-dimensional scorer
that produces a 0-1 catalyst_score per token from multiple CatalystEvents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import requests

import logging

from src.config import (
    CATALYST_MAX_1H_PREMOVE_PCT,
    CATALYST_MAX_4H_PREMOVE_PCT,
    CATALYST_MAX_24H_PREMOVE_PCT,
    CATALYST_MAJOR_SCORE,
    CRYPTOPANIC_API_KEY,
    COINMARKETCAL_API_KEY,
    NEGATIVE_CATALYST_SEVERITY,
    BLOCKING_NEGATIVE_EVENT_TYPES,
)
from src.qualitative import (
    check_defillama_metrics,
    check_snapshot_proposals,
    check_github_activity,
)


# ---------------------------------------------------------------------------
# Event taxonomy weights
# ---------------------------------------------------------------------------
EVENT_TYPE_WEIGHTS = {
    # Positive
    "binance_listing_or_launchpool": 1.00,
    "major_exchange_listing": 0.90,
    "token_burn_or_buyback": 0.85,
    "fee_switch_or_revenue_share": 0.85,
    "mainnet_launch_or_major_upgrade": 0.80,
    "airdrop_or_snapshot": 0.75,
    "major_partnership_revenue_relevant": 0.75,
    "governance_passed_material": 0.70,
    "tvl_or_revenue_surge": 0.65,
    "github_release_major": 0.55,
    "social_trending_only": 0.35,
    "generic_partnership": 0.25,
    "rumor_unverified": 0.15,
    # Negative
    "exploit_or_hack": -1.00,
    "delisting": -0.90,
    "regulatory_action": -0.85,
    "token_unlock_large": -0.70,
    "chain_halt": -0.80,
    "team_resignation": -0.60,
    "governance_rejected": -0.50,
    "bridge_issue": -0.75,
}

NEGATIVE_EVENT_TYPES = {
    "exploit_or_hack",
    "delisting",
    "regulatory_action",
    "token_unlock_large",
    "chain_halt",
    "team_resignation",
    "governance_rejected",
    "bridge_issue",
}


@dataclass
class CatalystEvent:
    symbol: str
    source: str
    source_url: str = ""
    title: str = ""
    event_type: str = ""
    published_at: str = ""
    event_time: Optional[str] = None
    credibility_score: float = 0.5
    materiality_score: float = 0.5
    freshness_score: float = 0.5
    relevance_score: float = 0.5
    novelty_score: float = 0.5
    market_attention_score: float = 0.5
    pre_move_score: float = 1.0
    negative_risk_score: float = 0.0
    final_score: float = 0.0
    reasons: list[str] = field(default_factory=list)


@dataclass
class CatalystResult:
    symbol: str
    score: float = 0.0
    max_event_score: float = 0.0
    events: list[CatalystEvent] = field(default_factory=list)
    dominant_event_type: str = ""
    reasons: list[str] = field(default_factory=list)
    is_major_catalyst: bool = False
    is_negative_catalyst: bool = False
    has_blocking_negative_catalyst: bool = False
    negative_catalyst_types: list[str] = field(default_factory=list)
    negative_catalyst_severities: list[str] = field(default_factory=list)
    negative_catalyst_reasons: list[str] = field(default_factory=list)
    catalyst_event_ids: list[int] = field(default_factory=list)
    requires_fast_monitoring: bool = False


class CatalystScorer:
    """Score catalyst events and aggregate into a per-token result."""

    event_type_weights: dict[str, float] = EVENT_TYPE_WEIGHTS

    def score_event(
        self,
        event: CatalystEvent,
        price_changes: dict[str, float] | None = None,
    ) -> CatalystEvent:
        """Compute sub-scores and final_score for a single event."""
        now = datetime.utcnow()
        reasons: list[str] = []

        # 1. Freshness decay
        freshness = 0.10
        if event.published_at:
            try:
                pub = datetime.fromisoformat(event.published_at)
                age = (now - pub).total_seconds()
                if age <= 1800:
                    freshness = 1.0
                elif age <= 7200:
                    freshness = 0.85
                elif age <= 21600:
                    freshness = 0.60
                elif age <= 86400:
                    freshness = 0.35
                else:
                    freshness = 0.10
            except (ValueError, TypeError):
                freshness = 0.10
        event.freshness_score = freshness

        # 2. Scheduled-event proximity
        proximity = 1.0
        if event.event_time:
            try:
                et = datetime.fromisoformat(event.event_time)
                delta = (et - now).total_seconds()
                if -7200 <= delta <= 86400:
                    proximity = 1.0
                elif 86400 < delta <= 259200:
                    proximity = 0.75
                elif 259200 < delta <= 604800:
                    proximity = 0.45
                else:
                    proximity = 0.20
            except (ValueError, TypeError):
                proximity = 0.20
        else:
            # If no explicit event_time, treat as already happened (proximity = 1.0)
            proximity = 1.0

        # 3. Pre-move penalty (absolute price changes)
        price_changes = price_changes or {}
        pre_move = 1.0
        pre_move_reasons: list[str] = []
        h1 = abs(price_changes.get("1h", 0.0))
        h4 = abs(price_changes.get("4h", 0.0))
        h24 = abs(price_changes.get("24h", 0.0))
        if h1 >= CATALYST_MAX_1H_PREMOVE_PCT:
            pre_move *= 0.3
            pre_move_reasons.append(f"1h move {h1:.1f}%")
        if h4 >= CATALYST_MAX_4H_PREMOVE_PCT:
            pre_move *= 0.3
            pre_move_reasons.append(f"4h move {h4:.1f}%")
        if h24 >= CATALYST_MAX_24H_PREMOVE_PCT:
            pre_move *= 0.3
            pre_move_reasons.append(f"24h move {h24:.1f}%")
        event.pre_move_score = pre_move
        if pre_move_reasons:
            reasons.append(f"pre-move penalty: {', '.join(pre_move_reasons)}")

        # 4. Negative risk dampening
        neg_dampen = 1.0
        if event.event_type in NEGATIVE_EVENT_TYPES:
            neg_dampen = 0.0
            event.negative_risk_score = 1.0
            reasons.append("negative event type")
        else:
            event.negative_risk_score = 0.0

        # 5. Base weight from taxonomy
        base_weight = self.event_type_weights.get(event.event_type, 0.15)
        if base_weight < 0:
            base_weight = abs(base_weight)

        # Combine dimensions
        # We average the quality dimensions, then multiply by freshness, proximity, pre_move, neg_dampen
        quality = (
            event.credibility_score
            + event.materiality_score
            + event.relevance_score
            + event.novelty_score
            + event.market_attention_score
        ) / 5.0

        event.final_score = base_weight * quality * freshness * proximity * pre_move * neg_dampen
        if reasons:
            event.reasons.extend(reasons)
        return event

    def aggregate(
        self,
        symbol: str,
        events: list[CatalystEvent],
        price_changes: dict[str, float] | None = None,
    ) -> CatalystResult:
        """Score each event, de-duplicate by event_type (max per type), then aggregate."""
        scored_events: list[CatalystEvent] = []
        max_per_type: dict[str, float] = {}
        type_best_event: dict[str, CatalystEvent] = {}

        for ev in events:
            scored = self.score_event(ev, price_changes)
            scored_events.append(scored)
            current_max = max_per_type.get(scored.event_type, 0.0)
            if scored.final_score > current_max:
                max_per_type[scored.event_type] = scored.final_score
                type_best_event[scored.event_type] = scored

        # Aggregate: weighted sum where each event type contributes its max score,
        # capped at 1.0 to avoid spam inflation.
        total = sum(max_per_type.values())
        score = min(1.0, total)

        max_event_score = max(max_per_type.values()) if max_per_type else 0.0
        dominant_type = (
            max(max_per_type, key=max_per_type.get) if max_per_type else ""
        )

        # Two-tier negative catalyst classification
        negative_types = []
        negative_severities = []
        negative_reasons = []
        event_ids = []
        for ev in scored_events:
            if ev.event_type in NEGATIVE_CATALYST_SEVERITY:
                negative_types.append(ev.event_type)
                negative_severities.append(NEGATIVE_CATALYST_SEVERITY[ev.event_type])
                negative_reasons.extend(ev.reasons)

        is_negative = len(negative_types) > 0
        is_blocking = any(s == "blocking" for s in negative_severities)
        is_major = score >= CATALYST_MAJOR_SCORE
        requires_fast = is_major and (any(ev.freshness_score >= 0.85 for ev in scored_events))

        reasons = []
        if is_negative:
            reasons.append("negative catalyst present")
        if is_blocking:
            reasons.append("blocking negative catalyst")
        if is_major:
            reasons.append("major catalyst")
        if requires_fast:
            reasons.append("requires fast monitoring")

        return CatalystResult(
            symbol=symbol,
            score=score,
            max_event_score=max_event_score,
            events=scored_events,
            dominant_event_type=dominant_type,
            reasons=reasons,
            is_major_catalyst=is_major,
            is_negative_catalyst=is_negative,
            has_blocking_negative_catalyst=is_blocking,
            negative_catalyst_types=negative_types,
            negative_catalyst_severities=negative_severities,
            negative_catalyst_reasons=negative_reasons,
            catalyst_event_ids=event_ids,
            requires_fast_monitoring=requires_fast,
        )


def _log_catalyst_source_error(source: str, symbol: str, exc: Exception):
    """Log a catalyst source failure without leaking secrets."""
    msg = str(exc)
    # Redact any API key-like strings from the message
    import re
    msg = re.sub(r"[a-zA-Z0-9_]{20,}", "<REDACTED>", msg)
    logging.warning(
        "Catalyst source %s failed for %s: %s (%s)",
        source,
        symbol,
        exc.__class__.__name__,
        msg,
    )


def fetch_catalyst_data(
    symbol: str,
    all_tickers: dict,
    mapping: dict,
) -> list[CatalystEvent]:
    """Fetch catalyst events from multiple sources.

    Requires `all_tickers` to be a dict mapping symbol -> Binance 24h ticker dict.
    `mapping` is the CoinGecko id mapping dict.
    """
    events: list[CatalystEvent] = []
    name = symbol.replace("USDT", "").lower()
    cg_info = mapping.get(name, {})
    cg_id = cg_info.get("coingecko_id") if isinstance(cg_info, dict) else None
    source_errors = []

    # 1. DeFiLlama
    try:
        metrics = None
        if cg_id:
            metrics = check_defillama_metrics(cg_id)
        if not metrics:
            metrics = check_defillama_metrics(name)
        if metrics:
            tvl = metrics.get("tvl")
            change_7d = metrics.get("change_7d")
            if tvl and change_7d and change_7d > 10:
                events.append(
                    CatalystEvent(
                        symbol=symbol,
                        source="defillama",
                        title=f"TVL +{change_7d:.1f}% in 7d",
                        event_type="tvl_or_revenue_surge",
                        published_at=datetime.utcnow().isoformat(),
                        credibility_score=0.6,
                        materiality_score=0.6,
                    )
                )
            revenue_7d = metrics.get("revenue_7d")
            if revenue_7d and revenue_7d > 10000:
                events.append(
                    CatalystEvent(
                        symbol=symbol,
                        source="defillama",
                        title=f"Revenue ${revenue_7d:,.0f} in 7d",
                        event_type="tvl_or_revenue_surge",
                        published_at=datetime.utcnow().isoformat(),
                        credibility_score=0.6,
                        materiality_score=0.6,
                    )
                )
    except Exception as exc:
        _log_catalyst_source_error("defillama", symbol, exc)
        source_errors.append("defillama")

    # 2. Snapshot proposals (if space_id known)
    space_id = cg_info.get("snapshot_space") if isinstance(cg_info, dict) else None
    if space_id:
        try:
            proposals = check_snapshot_proposals(space_id, since_days=14)
            for prop in proposals:
                state = prop.get("state", "")
                title = prop.get("title", "")
                created = prop.get("created", 0)
                # Only material passed/open proposals
                if state in ("active", "closed") and title:
                    event_type = (
                        "governance_passed_material"
                        if state == "closed"
                        else "governance_passed_material"
                    )
                    pub = datetime.utcfromtimestamp(created).isoformat() if created else datetime.utcnow().isoformat()
                    events.append(
                        CatalystEvent(
                            symbol=symbol,
                            source="snapshot",
                            title=title[:200],
                            event_type=event_type,
                            published_at=pub,
                            credibility_score=0.5,
                            materiality_score=0.5,
                        )
                    )
        except Exception as exc:
            _log_catalyst_source_error("snapshot", symbol, exc)
            source_errors.append("snapshot")

    # 3. GitHub activity (if repo known)
    repo = cg_info.get("github_repo") if isinstance(cg_info, dict) else None
    if repo:
        try:
            gh = check_github_activity(repo, since_days=30)
            if gh and gh.get("recent_releases", 0) > 0:
                latest = gh.get("latest_release", "")
                pub = gh.get("release_date") or datetime.utcnow().isoformat()
                events.append(
                    CatalystEvent(
                        symbol=symbol,
                        source="github",
                        title=f"Release {latest}",
                        event_type="github_release_major",
                        published_at=pub,
                        credibility_score=0.6,
                        materiality_score=0.4,
                    )
                )
        except Exception as exc:
            _log_catalyst_source_error("github", symbol, exc)
            source_errors.append("github")

    # 4. Binance 24h ticker context
    ticker = all_tickers.get(symbol.upper())
    if ticker:
        try:
            vol = float(ticker.get("quoteVolume", 0) or 0)
            price_chg = float(ticker.get("priceChangePercent", 0) or 0)
            trades = int(ticker.get("count", 0) or 0)
            if vol > 10_000_000 and abs(price_chg) > 5:
                events.append(
                    CatalystEvent(
                        symbol=symbol,
                        source="binance_24h",
                        title=f"{trades:,} trades, ${vol:,.0f} vol, {price_chg:+.1f}%",
                        event_type="social_trending_only",
                        published_at=datetime.utcnow().isoformat(),
                        credibility_score=0.3,
                        materiality_score=0.3,
                    )
                )
        except (ValueError, TypeError):
            pass

    # 5. CryptoPanic (optional)
    if CRYPTOPANIC_API_KEY:
        try:
            resp = requests.get(
                "https://cryptopanic.com/api/v1/posts/",
                params={"auth_token": CRYPTOPANIC_API_KEY, "currencies": name.upper()},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                for post in data.get("results", [])[:5]:
                    events.append(
                        CatalystEvent(
                            symbol=symbol,
                            source="cryptopanic",
                            source_url=post.get("url", ""),
                            title=post.get("title", "")[:200],
                            event_type="rumor_unverified",
                            published_at=post.get("published_at", datetime.utcnow().isoformat()),
                            credibility_score=0.3,
                            materiality_score=0.3,
                        )
                    )
        except Exception as exc:
            _log_catalyst_source_error("cryptopanic", symbol, exc)
            source_errors.append("cryptopanic")

    # 6. CoinMarketCal (optional)
    if COINMARKETCAL_API_KEY:
        try:
            resp = requests.get(
                "https://api.coinmarketcal.com/v1/events",
                headers={"x-api-key": COINMARKETCAL_API_KEY},
                params={"coins": name},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                for ev in data.get("body", [])[:5]:
                    events.append(
                        CatalystEvent(
                            symbol=symbol,
                            source="coinmarketcal",
                            source_url=ev.get("source", ""),
                            title=ev.get("title", "")[:200],
                            event_type="rumor_unverified",
                            published_at=ev.get("date_event", datetime.utcnow().isoformat()),
                            event_time=ev.get("date_event", None),
                            credibility_score=0.3,
                            materiality_score=0.3,
                        )
                    )
        except Exception as exc:
            _log_catalyst_source_error("coinmarketcal", symbol, exc)
            source_errors.append("coinmarketcal")

    if source_errors:
        logging.info("Catalyst scan for %s: %d/%d sources failed (%s)",
                     symbol, len(source_errors), 6, ", ".join(source_errors))

    return events
