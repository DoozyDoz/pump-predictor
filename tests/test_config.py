"""Unit tests for src/config.py configuration constants."""

import os
os.environ["TELEGRAM_BOT_TOKEN"] = "test:token"
os.environ["TELEGRAM_CHAT_ID"] = "12345"

from src import config


class TestSignalThresholds:
    def test_alert_threshold(self):
        assert config.ALERT_THRESHOLD == 2

    def test_watchlist_threshold(self):
        assert config.WATCHLIST_THRESHOLD == 1

    def test_confirmation_threshold(self):
        assert config.CONFIRMATION_THRESHOLD == 2

    def test_entry_threshold(self):
        assert config.ENTRY_THRESHOLD == 3


class TestATRParams:
    def test_atr_period(self):
        assert config.ATR_PERIOD == 14

    def test_atr_stop_multiplier(self):
        assert config.ATR_STOP_MULTIPLIER == 2.0

    def test_atr_risk_per_trade_pct(self):
        assert config.ATR_RISK_PER_TRADE_PCT == 0.01


class TestRegimeParams:
    def test_regime_enabled(self):
        assert config.REGIME_ENABLED is True

    def test_regime_btc_dom_upper(self):
        assert config.REGIME_BTC_DOM_UPPER == 60.0

    def test_regime_volatility_upper(self):
        assert config.REGIME_VOLATILITY_UPPER == 80.0


class TestConfirmationParams:
    def test_confirmation_poll_minutes(self):
        assert config.CONFIRMATION_POLL_MINUTES == 30

    def test_confirmation_price_move_pct(self):
        assert config.CONFIRMATION_PRICE_MOVE_PCT == 0.5

    def test_confirmation_volume_surge_pct(self):
        assert config.CONFIRMATION_VOLUME_SURGE_PCT == 50.0


class TestDataQuality:
    def test_stale_data_hours(self):
        assert config.STALE_DATA_HOURS == 4

    def test_min_data_points(self):
        assert config.MIN_DATA_POINTS == 10


class TestStageTTL:
    def test_watchlist_ttl_hours(self):
        assert config.WATCHLIST_TTL_HOURS == 72

    def test_confirmation_ttl_hours(self):
        assert config.CONFIRMATION_TTL_HOURS == 24


class TestLegacyFlag:
    def test_legacy_immediate_alerts_is_false(self):
        # The staged workflow is the default; legacy must opt in
        assert config.LEGACY_IMMEDIATE_ALERTS is False


class TestDBPath:
    def test_db_path_is_string(self):
        assert isinstance(config.DB_PATH, str)
        assert config.DB_PATH.endswith("pump.db")
