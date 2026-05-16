"""Unit tests for src/catalysts.py catalyst scoring engine."""

import os
os.environ["TELEGRAM_BOT_TOKEN"] = "test:token"
os.environ["TELEGRAM_CHAT_ID"] = "12345"

from datetime import datetime, timedelta
from unittest.mock import patch


from src.catalysts import (
    CatalystEvent,
    CatalystScorer,
    fetch_catalyst_data,
    EVENT_TYPE_WEIGHTS,
)
from src.config import (
    CATALYST_MAJOR_SCORE,
    CATALYST_MAX_1H_PREMOVE_PCT,
)


class TestCatalystEvent:
    def test_dataclass_creation(self):
        ev = CatalystEvent(symbol="BTCUSDT", source="test", title="test event")
        assert ev.symbol == "BTCUSDT"
        assert ev.source == "test"
        assert ev.final_score == 0.0
        assert ev.reasons == []

    def test_field_defaults(self):
        ev = CatalystEvent(symbol="ETHUSDT", source="binance")
        assert ev.credibility_score == 0.5
        assert ev.materiality_score == 0.5
        assert ev.pre_move_score == 1.0
        assert ev.negative_risk_score == 0.0


class TestCatalystScorer:
    def test_fresh_event_high_score(self):
        scorer = CatalystScorer()
        pub = (datetime.utcnow() - timedelta(minutes=10)).isoformat()
        ev = CatalystEvent(
            symbol="BTCUSDT",
            source="test",
            title="fresh event",
            event_type="major_exchange_listing",
            published_at=pub,
            credibility_score=1.0,
            materiality_score=1.0,
            relevance_score=1.0,
            novelty_score=1.0,
            market_attention_score=1.0,
        )
        scored = scorer.score_event(ev, {})
        assert scored.freshness_score == 1.0
        assert scored.final_score > 0.5

    def test_stale_event_low_score(self):
        scorer = CatalystScorer()
        pub = (datetime.utcnow() - timedelta(hours=12)).isoformat()
        ev = CatalystEvent(
            symbol="BTCUSDT",
            source="test",
            title="stale event",
            event_type="major_exchange_listing",
            published_at=pub,
            credibility_score=1.0,
            materiality_score=1.0,
            relevance_score=1.0,
            novelty_score=1.0,
            market_attention_score=1.0,
        )
        scored = scorer.score_event(ev, {})
        assert scored.freshness_score == 0.35
        assert scored.final_score < 1.0

    def test_premove_penalty_1h(self):
        scorer = CatalystScorer()
        ev = CatalystEvent(
            symbol="BTCUSDT",
            source="test",
            title="event",
            event_type="major_exchange_listing",
            published_at=datetime.utcnow().isoformat(),
            credibility_score=1.0,
            materiality_score=1.0,
            relevance_score=1.0,
            novelty_score=1.0,
            market_attention_score=1.0,
        )
        price_changes = {"1h": CATALYST_MAX_1H_PREMOVE_PCT + 1, "4h": 0.0, "24h": 0.0}
        scored = scorer.score_event(ev, price_changes)
        assert scored.pre_move_score < 1.0
        assert scored.final_score < 1.0

    def test_negative_event_blocks(self):
        scorer = CatalystScorer()
        ev = CatalystEvent(
            symbol="BTCUSDT",
            source="test",
            title="hack",
            event_type="exploit_or_hack",
            published_at=datetime.utcnow().isoformat(),
            credibility_score=1.0,
            materiality_score=1.0,
            relevance_score=1.0,
            novelty_score=1.0,
            market_attention_score=1.0,
        )
        result = scorer.aggregate("BTCUSDT", [ev], {})
        assert result.is_negative_catalyst is True

    def test_major_catalyst_threshold(self):
        scorer = CatalystScorer()
        ev = CatalystEvent(
            symbol="BTCUSDT",
            source="test",
            title="binance listing",
            event_type="binance_listing_or_launchpool",
            published_at=datetime.utcnow().isoformat(),
            credibility_score=1.0,
            materiality_score=1.0,
            relevance_score=1.0,
            novelty_score=1.0,
            market_attention_score=1.0,
        )
        result = scorer.aggregate("BTCUSDT", [ev], {})
        assert result.score >= CATALYST_MAJOR_SCORE
        assert result.is_major_catalyst is True

    def test_event_type_weights(self):
        assert EVENT_TYPE_WEIGHTS["binance_listing_or_launchpool"] == 1.00
        assert EVENT_TYPE_WEIGHTS["major_exchange_listing"] == 0.90
        assert EVENT_TYPE_WEIGHTS["token_burn_or_buyback"] == 0.85
        assert EVENT_TYPE_WEIGHTS["fee_switch_or_revenue_share"] == 0.85
        assert EVENT_TYPE_WEIGHTS["mainnet_launch_or_major_upgrade"] == 0.80
        assert EVENT_TYPE_WEIGHTS["airdrop_or_snapshot"] == 0.75
        assert EVENT_TYPE_WEIGHTS["major_partnership_revenue_relevant"] == 0.75
        assert EVENT_TYPE_WEIGHTS["governance_passed_material"] == 0.70
        assert EVENT_TYPE_WEIGHTS["tvl_or_revenue_surge"] == 0.65
        assert EVENT_TYPE_WEIGHTS["github_release_major"] == 0.55
        assert EVENT_TYPE_WEIGHTS["social_trending_only"] == 0.35
        assert EVENT_TYPE_WEIGHTS["generic_partnership"] == 0.25
        assert EVENT_TYPE_WEIGHTS["rumor_unverified"] == 0.15
        assert EVENT_TYPE_WEIGHTS["exploit_or_hack"] == -1.00

    def test_scheduled_event_proximity_near(self):
        scorer = CatalystScorer()
        event_time = (datetime.utcnow() + timedelta(hours=6)).isoformat()
        ev = CatalystEvent(
            symbol="BTCUSDT",
            source="test",
            title="upgrade",
            event_type="mainnet_launch_or_major_upgrade",
            published_at=datetime.utcnow().isoformat(),
            event_time=event_time,
            credibility_score=1.0,
            materiality_score=1.0,
            relevance_score=1.0,
            novelty_score=1.0,
            market_attention_score=1.0,
        )
        scored = scorer.score_event(ev, {})
        # Proximity within +24h should be 1.0
        assert scored.final_score > 0.5

    def test_scheduled_event_proximity_far(self):
        scorer = CatalystScorer()
        event_time = (datetime.utcnow() + timedelta(hours=48)).isoformat()
        ev = CatalystEvent(
            symbol="BTCUSDT",
            source="test",
            title="upgrade",
            event_type="mainnet_launch_or_major_upgrade",
            published_at=datetime.utcnow().isoformat(),
            event_time=event_time,
            credibility_score=1.0,
            materiality_score=1.0,
            relevance_score=1.0,
            novelty_score=1.0,
            market_attention_score=1.0,
        )
        scored = scorer.score_event(ev, {})
        # Proximity 24-72h = 0.75
        assert scored.final_score > 0.3

    def test_missing_api_key_graceful(self):
        with patch.dict(os.environ, {"CRYPTOPANIC_API_KEY": ""}, clear=False):
            with patch("src.catalysts.requests.get", side_effect=Exception("no key")):
                events = fetch_catalyst_data("BTCUSDT", {}, {})
        assert isinstance(events, list)

    def test_aggregate_deduplicates_by_event_type(self):
        """Duplicate event types should not inflate score; max per type is used."""
        scorer = CatalystScorer()
        pub = datetime.utcnow().isoformat()
        ev1 = CatalystEvent(
            symbol="BTCUSDT",
            source="test",
            title="event A",
            event_type="major_exchange_listing",
            published_at=pub,
            credibility_score=1.0,
            materiality_score=1.0,
            relevance_score=1.0,
            novelty_score=1.0,
            market_attention_score=1.0,
        )
        ev2 = CatalystEvent(
            symbol="BTCUSDT",
            source="test",
            title="event B",
            event_type="major_exchange_listing",
            published_at=pub,
            credibility_score=0.5,
            materiality_score=0.5,
            relevance_score=0.5,
            novelty_score=0.5,
            market_attention_score=0.5,
        )
        result = scorer.aggregate("BTCUSDT", [ev1, ev2], {})
        # Score should reflect the higher-quality event (ev1), not sum of both.
        # With ev1: base_weight 0.90 * quality 1.0 * freshness 1.0 = 0.90
        assert abs(result.score - 0.90) < 0.01
        # If duplicates were summed, score would be ~0.90 + ~0.45 = 1.35 (capped at 1.0),
        # so asserting exact 0.90 proves de-duplication.
        assert len(result.events) == 2  # both events are still stored


class TestTwoTierSeverity:
    def test_blocking_negative_sets_has_blocking(self):
        scorer = CatalystScorer()
        ev = CatalystEvent(
            symbol="BTCUSDT",
            source="test",
            title="hack",
            event_type="exploit_or_hack",
            published_at=datetime.utcnow().isoformat(),
            credibility_score=1.0,
            materiality_score=1.0,
            relevance_score=1.0,
            novelty_score=1.0,
            market_attention_score=1.0,
        )
        result = scorer.aggregate("BTCUSDT", [ev], {})
        assert result.is_negative_catalyst is True
        assert result.has_blocking_negative_catalyst is True
        assert "exploit_or_hack" in result.negative_catalyst_types

    def test_warning_negative_does_not_set_blocking(self):
        scorer = CatalystScorer()
        ev = CatalystEvent(
            symbol="BTCUSDT",
            source="test",
            title="unlock",
            event_type="token_unlock_large",
            published_at=datetime.utcnow().isoformat(),
            credibility_score=1.0,
            materiality_score=1.0,
            relevance_score=1.0,
            novelty_score=1.0,
            market_attention_score=1.0,
        )
        result = scorer.aggregate("BTCUSDT", [ev], {})
        assert result.is_negative_catalyst is True
        assert result.has_blocking_negative_catalyst is False
        assert "token_unlock_large" in result.negative_catalyst_types
        assert "warning" in result.negative_catalyst_severities

    def test_mixed_positive_and_blocking_negative(self):
        scorer = CatalystScorer()
        positive = CatalystEvent(
            symbol="BTCUSDT",
            source="test",
            title="listing",
            event_type="major_exchange_listing",
            published_at=datetime.utcnow().isoformat(),
            credibility_score=1.0,
            materiality_score=1.0,
            relevance_score=1.0,
            novelty_score=1.0,
            market_attention_score=1.0,
        )
        negative = CatalystEvent(
            symbol="BTCUSDT",
            source="test",
            title="hack",
            event_type="exploit_or_hack",
            published_at=datetime.utcnow().isoformat(),
            credibility_score=1.0,
            materiality_score=1.0,
            relevance_score=1.0,
            novelty_score=1.0,
            market_attention_score=1.0,
        )
        result = scorer.aggregate("BTCUSDT", [positive, negative], {})
        assert result.is_negative_catalyst is True
        assert result.has_blocking_negative_catalyst is True
        assert result.score > 0  # positive still contributes

    def test_dominant_positive_does_not_hide_negative(self):
        scorer = CatalystScorer()
        positive = CatalystEvent(
            symbol="BTCUSDT",
            source="test",
            title="listing",
            event_type="binance_listing_or_launchpool",
            published_at=datetime.utcnow().isoformat(),
            credibility_score=1.0,
            materiality_score=1.0,
            relevance_score=1.0,
            novelty_score=1.0,
            market_attention_score=1.0,
        )
        negative = CatalystEvent(
            symbol="BTCUSDT",
            source="test",
            title="hack",
            event_type="exploit_or_hack",
            published_at=datetime.utcnow().isoformat(),
            credibility_score=1.0,
            materiality_score=1.0,
            relevance_score=1.0,
            novelty_score=1.0,
            market_attention_score=1.0,
        )
        result = scorer.aggregate("BTCUSDT", [positive, negative], {})
        assert result.dominant_event_type == "binance_listing_or_launchpool"
        assert result.is_negative_catalyst is True
        assert result.has_blocking_negative_catalyst is True


class TestFetchCatalystData:
    def test_returns_list(self):
        events = fetch_catalyst_data("BTCUSDT", {}, {})
        assert isinstance(events, list)


class TestCatalystLogging:
    def test_api_exception_logs_source_not_secret(self, caplog, monkeypatch):
        """API failure should log source name but not leak API keys."""
        import logging
        import os
        os.environ["CRYPTOPANIC_API_KEY"] = "super_secret_api_key_12345"
        with caplog.at_level(logging.WARNING):
            from src.catalysts import _log_catalyst_source_error
            class FakeExc(Exception):
                pass
            exc = FakeExc("Request failed with token=super_secret_api_key_12345")
            _log_catalyst_source_error("cryptopanic", "BTCUSDT", exc)
        assert "cryptopanic" in caplog.text
        assert "BTCUSDT" in caplog.text
        assert "super_secret_api_key_12345" not in caplog.text
        assert "<REDACTED>" in caplog.text

    def test_missing_api_key_does_not_crash_pipeline(self, monkeypatch):
        """Missing API key should not crash the pipeline."""
        monkeypatch.setattr("src.catalysts.CRYPTOPANIC_API_KEY", "")
        monkeypatch.setattr("src.catalysts.COINMARKETCAL_API_KEY", "")
        events = fetch_catalyst_data("BTCUSDT", {}, {})
        assert isinstance(events, list)

    def test_source_failure_count_tracked(self, caplog, monkeypatch):
        """Source failures should be counted and logged in summary."""
        import logging
        monkeypatch.setattr(
            "src.catalysts.check_defillama_metrics",
            lambda slug: (_ for _ in ()).throw(Exception("defillama down")),
        )
        with caplog.at_level(logging.INFO):
            events = fetch_catalyst_data("BTCUSDT", {}, {})
        assert isinstance(events, list)
        assert "defillama" in caplog.text
        assert "failed" in caplog.text.lower()
