"""
Qualitative signal detection framework for Phase 2.

Monitors external sources for catalyst events that precede pumps:
- Governance proposals (Snapshot, Tally)
- Token unlocks & supply events (CoinGecko, TokenUnlocks)
- Exchange listings & trading pairs
- Social momentum (X/Twitter, Telegram, Reddit growth)
- Developer activity (GitHub commits, releases)

Each signal is stored as a tag with:
- source: where it was detected
- confidence: 0-1 how reliable this signal type is
- lead_time_hours: typical time from signal to pump
"""

from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field
import requests

# ---------------------------------------------------------------------------
# Catalyst signal types — ranked by historical predictive power
# ---------------------------------------------------------------------------
CATALYST_TYPES = {
    "governance_proposal": {
        "label": "Governance Proposal",
        "confidence": 0.6,
        "lead_time_hours": 72,  # proposals usually known days before votes
        "sources": ["snapshot.org", "tally.xyz", "project forums"],
        "look_for": ["fee switch", "token burn", "emissions cut", "treasury buyback", "protocol upgrade"],
    },
    "token_unlock": {
        "label": "Token Unlock Event",
        "confidence": 0.5,
        "lead_time_hours": 168,  # unlocks are scheduled weeks ahead
        "sources": ["tokenunlocks.app", "coingecko events", "project docs"],
        "look_for": ["cliff unlock passed", "linear emission ending", "supply squeeze after unlock"],
    },
    "exchange_listing": {
        "label": "Exchange Listing",
        "confidence": 0.8,
        "lead_time_hours": 24,
        "sources": ["binance announcements", "coinbase listings", "exchange blogs"],
        "look_for": ["new spot listing", "new perp pair", "new chain support"],
    },
    "mainnet_upgrade": {
        "label": "Mainnet/Protocol Upgrade",
        "confidence": 0.5,
        "lead_time_hours": 168,
        "sources": ["github releases", "project blog", "dev docs"],
        "look_for": ["mainnet launch", "v2/v3 upgrade", "new SDK", "bridge deployment"],
    },
    "partnership": {
        "label": "Partnership / Integration",
        "confidence": 0.4,
        "lead_time_hours": 48,
        "sources": ["project blog", "partner announcements", "chain official accounts"],
        "look_for": ["enterprise partnership", "protocol integration", "grant award"],
    },
    "social_momentum": {
        "label": "Social Momentum",
        "confidence": 0.3,
        "lead_time_hours": 12,
        "sources": ["x.com trending", "telegram growth", "reddit mentions", "lunarcrush"],
        "look_for": ["follower spike", "mention surge", "sentiment shift positive", "KOL endorsement"],
    },
    "onchain_anomaly": {
        "label": "On-Chain Anomaly",
        "confidence": 0.7,
        "lead_time_hours": 48,
        "sources": ["nansen", "arkham", "dune"],
        "look_for": ["whale wallet accumulation", "exchange outflow spike", "new wallet cluster"],
    },
    "sector_rotation": {
        "label": "Sector / Narrative Rotation",
        "confidence": 0.3,
        "lead_time_hours": 72,
        "sources": ["messari", "delphi", "the block", "coindesk"],
        "look_for": ["sector outperforming", "narrative shift", "regulatory clarity", "institutional flow"],
    },
}


@dataclass
class QualitativeTag:
    token_symbol: str
    catalyst_type: str
    description: str
    source: str
    confidence: float
    detected_at: str
    lead_time_hours: int
    url: Optional[str] = None


@dataclass
class TokenQualitativeProfile:
    symbol: str
    tags: list[QualitativeTag] = field(default_factory=list)
    catalyst_boost: float = 0.0   # real catalysts only (governance, listing, on-chain, etc.)
    blocked: bool = False
    block_reason: str = ""

    def add_tag(self, tag: QualitativeTag):
        self.tags.append(tag)
        self._recompute_catalyst_boost()

    def _recompute_catalyst_boost(self):
        """Recompute catalyst_boost by taking max confidence per unique catalyst_type,
        then summing across catalyst types, capped at 1.0.
        This prevents multiple tags from the same event (same catalyst_type)
        from over-boosting the score."""
        max_per_type = {}
        for tag in self.tags:
            if tag.source != "binance_24h":
                existing = max_per_type.get(tag.catalyst_type, 0.0)
                if tag.confidence > existing:
                    max_per_type[tag.catalyst_type] = tag.confidence
        total = sum(max_per_type.values())
        self.catalyst_boost = min(1.0, total)

    def recent_tags(self, hours: int = 168) -> list[QualitativeTag]:
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        return [t for t in self.tags
                if datetime.fromisoformat(t.detected_at) > cutoff]


# ---------------------------------------------------------------------------
# Data source integrations (free/cheap where possible)
# ---------------------------------------------------------------------------

def check_defillama_metrics(protocol_slug: str) -> Optional[dict]:
    """Check DeFiLlama for TVL, revenue, and volume trends."""
    try:
        resp = requests.get(f"https://api.llama.fi/protocol/{protocol_slug}", timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "tvl": data.get("tvl"),
                "change_7d": data.get("change_7d"),
                "change_1m": data.get("change_1m"),
                "revenue_7d": data.get("revenue7d"),
                "volume_7d": data.get("volume7d"),
            }
    except Exception:
        pass
    return None


def check_snapshot_proposals(space_id: str, since_days: int = 14) -> list[dict]:
    """Query Snapshot.org GraphQL for recent proposals in a DAO space."""
    query = """
    query Proposals($space: String!, $since: Int!) {
      proposals(
        first: 10
        where: { space: $space, created_gte: $since }
        orderBy: "created"
        orderDirection: desc
      ) {
        id title state end scores_total created
      }
    }
    """
    since_ts = int((datetime.utcnow() - timedelta(days=since_days)).timestamp())
    try:
        resp = requests.post("https://hub.snapshot.org/graphql",
                             json={"query": query, "variables": {"space": space_id, "since": since_ts}},
                             timeout=15)
        if resp.status_code == 200:
            return resp.json().get("data", {}).get("proposals", [])
    except Exception:
        pass
    return []


def check_github_activity(repo: str, since_days: int = 30) -> Optional[dict]:
    """Check GitHub for recent commits and releases."""
    try:
        resp = requests.get(f"https://api.github.com/repos/{repo}/releases?per_page=3", timeout=15)
        releases = resp.json() if resp.status_code == 200 else []
        resp2 = requests.get(f"https://api.github.com/repos/{repo}/commits?per_page=5", timeout=15)
        commits = resp2.json() if resp2.status_code == 200 else []
        return {
            "recent_releases": len(releases),
            "latest_release": releases[0].get("tag_name") if releases else None,
            "release_date": releases[0].get("published_at") if releases else None,
            "recent_commits": len(commits),
            "commit_dates": [c.get("commit", {}).get("author", {}).get("date") for c in commits[:5]],
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Scoring integration
# ---------------------------------------------------------------------------

def compute_qualitative_boost(profile: TokenQualitativeProfile) -> float:
    """Recompute catalyst boost from stored tags (non-binance_24h sources only).
    Uses max confidence per unique catalyst_type to prevent over-boosting."""
    max_per_type = {}
    for tag in profile.tags:
        if tag.source != "binance_24h":
            existing = max_per_type.get(tag.catalyst_type, 0.0)
            if tag.confidence > existing:
                max_per_type[tag.catalyst_type] = tag.confidence
    total = sum(max_per_type.values())
    return min(1.0, total)


def qualitative_override(pump_score: int, catalyst_boost: float,
                         threshold: int = 2) -> tuple[int, str]:
    """
    Apply qualitative catalyst boost to Pump Score.

    catalyst_boost (real catalysts: listing, on-chain, governance, etc.):
      - >= 0.8: auto-alert even with score = 0
      - >= 0.5: push near-miss (score = threshold-1) over threshold
      - <= -0.5: suppress an otherwise-qualifying alert

    Returns (adjusted_score, reason).
    """
    if catalyst_boost >= 0.8:
        return max(pump_score, threshold), "high-confidence catalyst overrides score"
    if catalyst_boost >= 0.5 and pump_score >= threshold - 1:
        return pump_score + 1, "catalyst boost pushes near-miss over threshold"
    if catalyst_boost <= -0.5 and pump_score >= threshold:
        return threshold - 1, "negative catalyst suppresses alert"
    return pump_score, ""


# Mapping from old QualitativeTag catalyst_type to new CatalystScorer event_type
_TAG_TO_EVENT_TYPE = {
    "tvl_surge": "tvl_or_revenue_surge",
    "protocol_revenue": "tvl_or_revenue_surge",
    "governance_proposal": "governance_passed_material",
    "exchange_listing": "major_exchange_listing",
    "mainnet_upgrade": "mainnet_launch_or_major_upgrade",
    "partnership": "major_partnership_revenue_relevant",
    "social_momentum": "social_trending_only",
    "onchain_anomaly": "tvl_or_revenue_surge",
    "sector_rotation": "social_trending_only",
    "capitulation_volume": "social_trending_only",
    "momentum_volume": "social_trending_only",
    "trade_count_spike": "social_trending_only",
    "oversold": "social_trending_only",
    "token_unlock": "token_unlock_large",
}


def profile_to_catalyst_events(profile: TokenQualitativeProfile, all_tickers: dict) -> list:
    """Bridge old TokenQualitativeProfile tags into CatalystEvent objects.

    Imports `catalysts` locally to avoid circular imports.
    """
    from src.catalysts import CatalystEvent  # local import to avoid cycles
    events = []
    for tag in profile.tags:
        mapped_type = _TAG_TO_EVENT_TYPE.get(tag.catalyst_type, "rumor_unverified")
        events.append(
            CatalystEvent(
                symbol=profile.symbol,
                source=tag.source,
                title=tag.description,
                event_type=mapped_type,
                published_at=tag.detected_at,
                event_time=None,
                credibility_score=tag.confidence,
                materiality_score=0.5,
                relevance_score=0.5,
                novelty_score=0.5,
                market_attention_score=0.5,
            )
        )
    return events
