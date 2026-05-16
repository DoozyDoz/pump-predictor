"""Unit tests for src/qualitative.py qualitative signal detection."""

import os
os.environ["TELEGRAM_BOT_TOKEN"] = "test:token"
os.environ["TELEGRAM_CHAT_ID"] = "12345"

import pytest
from datetime import datetime
from src.qualitative import (
    QualitativeTag,
    TokenQualitativeProfile,
    compute_qualitative_boost,
    qualitative_override,
)


def _make_tag(catalyst_type="listing", source="binance_announcements",
              confidence=0.5, desc="test"):
    return QualitativeTag(
        token_symbol="BTCUSDT",
        catalyst_type=catalyst_type,
        description=desc,
        source=source,
        confidence=confidence,
        detected_at=datetime.utcnow().isoformat(),
        lead_time_hours=24,
    )


class TestQualitativeTag:
    def test_tag_creation(self):
        tag = _make_tag()
        assert tag.catalyst_type == "listing"
        assert tag.source == "binance_announcements"
        assert tag.confidence == 0.5

    def test_tag_repr(self):
        tag = _make_tag()
        assert "BTCUSDT" in str(tag)


class TestTokenQualitativeProfile:
    def test_empty_profile(self):
        p = TokenQualitativeProfile(symbol="BTCUSDT")
        assert p.symbol == "BTCUSDT"
        assert p.tags == []
        assert p.catalyst_boost == 0.0
        assert not p.blocked

    def test_add_tag_increases_boost(self):
        p = TokenQualitativeProfile(symbol="BTCUSDT")
        p.add_tag(_make_tag(confidence=0.5, catalyst_type="listing"))
        assert p.catalyst_boost > 0.0

    def test_boost_capped_at_one(self):
        p = TokenQualitativeProfile(symbol="BTCUSDT")
        p.add_tag(_make_tag(confidence=0.6, catalyst_type="listing"))
        p.add_tag(_make_tag(confidence=0.5, catalyst_type="governance"))
        p.add_tag(_make_tag(confidence=0.4, catalyst_type="partnership"))
        assert p.catalyst_boost <= 1.0

    def test_boost_takes_max_per_catalyst_type(self):
        """Multiple tags of same catalyst_type should only count the max confidence."""
        p = TokenQualitativeProfile(symbol="BTCUSDT")
        p.add_tag(_make_tag(confidence=0.3, catalyst_type="listing"))
        p.add_tag(_make_tag(confidence=0.7, catalyst_type="listing"))
        # Only the max (0.7) should count for 'listing'
        assert p.catalyst_boost == 0.7

    def test_binance_24h_source_excluded_from_boost(self):
        """Tags with source='binance_24h' should not contribute to catalyst_boost."""
        p = TokenQualitativeProfile(symbol="BTCUSDT")
        p.add_tag(_make_tag(confidence=0.8, catalyst_type="momentum_volume",
                            source="binance_24h"))
        assert p.catalyst_boost == 0.0

    def test_boost_sums_across_different_catalyst_types(self):
        p = TokenQualitativeProfile(symbol="BTCUSDT")
        p.add_tag(_make_tag(confidence=0.6, catalyst_type="listing"))
        p.add_tag(_make_tag(confidence=0.3, catalyst_type="governance"))
        assert p.catalyst_boost == pytest.approx(0.9, rel=1e-9)

    def test_recent_tags(self):
        p = TokenQualitativeProfile(symbol="BTCUSDT")
        p.add_tag(_make_tag())
        recent = p.recent_tags(hours=168)
        assert len(recent) == 1

    def test_blocked_flag(self):
        p = TokenQualitativeProfile(symbol="BTCUSDT", blocked=True,
                                    block_reason="test reason")
        assert p.blocked
        assert p.block_reason == "test reason"


class TestComputeQualitativeBoost:
    def test_boost_from_non_binance_sources(self):
        p = TokenQualitativeProfile(symbol="SOLUSDT")
        p.add_tag(_make_tag(confidence=0.5, catalyst_type="listing",
                            source="exchange_blog"))
        p.add_tag(_make_tag(confidence=0.4, catalyst_type="governance",
                            source="snapshot"))
        result = compute_qualitative_boost(p)
        assert result == 0.9

    def test_boost_excludes_binance_24h(self):
        p = TokenQualitativeProfile(symbol="SOLUSDT")
        p.add_tag(_make_tag(confidence=0.9, catalyst_type="momentum_volume",
                            source="binance_24h"))
        result = compute_qualitative_boost(p)
        assert result == 0.0


class TestQualitativeOverride:
    def test_high_boost_auto_alert(self):
        """catalyst_boost >= 0.8 should override to at least threshold."""
        result, reason = qualitative_override(0, 0.9, threshold=2)
        assert result >= 2
        assert "high-confidence catalyst" in reason

    def test_medium_boost_pushes_near_miss(self):
        """catalyst_boost >= 0.5 and score = threshold-1 should push over."""
        result, reason = qualitative_override(1, 0.6, threshold=2)
        assert result == 2
        assert "catalyst boost" in reason

    def test_negative_boost_suppresses(self):
        """catalyst_boost <= -0.5 with score >= threshold should suppress."""
        result, reason = qualitative_override(2, -0.6, threshold=2)
        assert result == 1
        assert "negative catalyst" in reason

    def test_no_boost_no_change(self):
        result, reason = qualitative_override(2, 0.0, threshold=2)
        assert result == 2
        assert reason == ""

    def test_low_boost_below_0_5_no_change(self):
        result, reason = qualitative_override(1, 0.3, threshold=2)
        assert result == 1
        assert reason == ""
