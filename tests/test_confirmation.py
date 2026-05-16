"""Unit tests for src/confirmation.py confirmation logic."""

import os
os.environ["TELEGRAM_BOT_TOKEN"] = "test:token"
os.environ["TELEGRAM_CHAT_ID"] = "12345"

from unittest.mock import MagicMock, patch

from src.confirmation import _rolling_zscore, ConfirmationChecker


# ---------------------------------------------------------------------------
# Helpers for ConfirmationChecker tests
# ---------------------------------------------------------------------------

def make_candle(close=100.0, volume=1000.0, high=101.0, low=99.0):
    return {"c": close, "v": volume, "h": high, "l": low}


def make_taker_candle(ratio=0.5, ts=1000000):
    return {"buySellRatio": ratio, "timestamp": ts}


class TestRollingZscore:
    def test_basic_zscore(self):
        """Verify z-score computation with a simple sequence."""
        values = [2, 3, 2, 3, 10, 2, 3, 2, 3]
        zscores = _rolling_zscore(values, window=4)
        assert len(zscores) > 0
        assert zscores[4] > 0

    def test_insufficient_data(self):
        """Empty or short list should return empty."""
        assert _rolling_zscore([], window=5) == []
        assert _rolling_zscore([1, 2], window=5) == []

    def test_flat_values_zero_zscore(self):
        """Constant values should produce z-scores of 0."""
        values = [3] * 10
        zscores = _rolling_zscore(values, window=5)
        for z in zscores:
            assert z == 0.0

    def test_negative_zscore(self):
        """Below-mean values should have negative z-scores."""
        values = [10, 10, 10, 1, 10, 10, 10]
        zscores = _rolling_zscore(values, window=3)
        assert any(z < 0 for z in zscores)


# ---------------------------------------------------------------------------
# ConfirmationChecker tests
# Note: confirmation.py imports get_klines and get_taker_ratio_history
# at MODULE level (from src.binance import ...), so we must patch
# src.confirmation.get_klines / src.confirmation.get_taker_ratio_history.
# The _check_order_book method imports get_order_book/compute_order_book_imbalance
# INSIDE the function body, so we must patch src.binance.get_order_book.
# ---------------------------------------------------------------------------

class TestConfirmationCheckerCheckPriceAction:
    """Tests for ConfirmationChecker._check_price_action()."""

    def test_price_bounce_confirmed(self):
        """Price bouncing above threshold should be confirmed."""
        checker = ConfirmationChecker(MagicMock())
        closes = [100.0] * 12 + [105.0]  # bounce 5% from low
        candles = [make_candle(c) for c in closes]
        with patch("src.confirmation.get_klines", return_value=candles):
            result = checker._check_price_action("BTCUSDT")
        assert result["confirmed"] is True
        assert "bounced" in result["reason"]

    def test_price_barely_moves_not_confirmed(self):
        """Price barely moving should not be confirmed."""
        checker = ConfirmationChecker(MagicMock())
        closes = [100.0] * 24 + [100.3]  # bounce 0.3% < 0.5% threshold
        candles = [make_candle(c) for c in closes]
        with patch("src.confirmation.get_klines", return_value=candles):
            result = checker._check_price_action("BTCUSDT")
        assert result["confirmed"] is False
        assert "below" in result["reason"]

    def test_insufficient_candles(self):
        """Fewer than 12 candles should return not confirmed."""
        checker = ConfirmationChecker(MagicMock())
        candles = [make_candle(100.0) for _ in range(5)]
        with patch("src.confirmation.get_klines", return_value=candles):
            result = checker._check_price_action("BTCUSDT")
        assert result["confirmed"] is False
        assert "insufficient" in result["reason"].lower()

    def test_api_exception_returns_not_confirmed(self):
        """API exception should return not confirmed."""
        checker = ConfirmationChecker(MagicMock())
        with patch("src.confirmation.get_klines",
                   side_effect=Exception("API error")):
            result = checker._check_price_action("BTCUSDT")
        assert result["confirmed"] is False
        assert "no klines" in result["reason"]

    def test_missing_close_prices(self):
        """Missing close prices should return not confirmed."""
        checker = ConfirmationChecker(MagicMock())
        candles = [{"c": None, "v": 1000}] * 20
        with patch("src.confirmation.get_klines", return_value=candles):
            result = checker._check_price_action("BTCUSDT")
        assert result["confirmed"] is False
        assert "no close prices" in result["reason"]


class TestConfirmationCheckerCheckVolume:
    """Tests for ConfirmationChecker._check_volume_confirmation()."""

    def test_volume_surge_confirmed(self):
        """Volume surge above 50% should be confirmed."""
        checker = ConfirmationChecker(MagicMock())
        # 24 candles: 21 with vol 1000, last 3 with vol 2000 = 100% surge
        vols = [1000.0] * 21 + [2000.0] * 3
        candles = [make_candle(100.0, v) for v in vols]
        with patch("src.confirmation.get_klines", return_value=candles):
            result = checker._check_volume_confirmation("BTCUSDT")
        assert result["confirmed"] is True
        assert "surge" in result["reason"]

    def test_volume_flat_not_confirmed(self):
        """Flat volume should not be confirmed."""
        checker = ConfirmationChecker(MagicMock())
        vols = [1000.0] * 24
        candles = [make_candle(100.0, v) for v in vols]
        with patch("src.confirmation.get_klines", return_value=candles):
            result = checker._check_volume_confirmation("BTCUSDT")
        assert result["confirmed"] is False
        assert "below" in result["reason"]

    def test_insufficient_volume_candles(self):
        """Fewer than 24 candles should return not confirmed."""
        checker = ConfirmationChecker(MagicMock())
        candles = [make_candle(100.0) for _ in range(10)]
        with patch("src.confirmation.get_klines", return_value=candles):
            result = checker._check_volume_confirmation("BTCUSDT")
        assert result["confirmed"] is False

    def test_api_exception_volume(self):
        """API exception should return not confirmed."""
        checker = ConfirmationChecker(MagicMock())
        with patch("src.confirmation.get_klines",
                   side_effect=Exception("API error")):
            result = checker._check_volume_confirmation("BTCUSDT")
        assert result["confirmed"] is False
        assert "no klines" in result["reason"]

    def test_missing_volume_data(self):
        """Missing volume data should return not confirmed."""
        checker = ConfirmationChecker(MagicMock())
        candles = [{"c": 100.0, "v": None}] * 24
        with patch("src.confirmation.get_klines", return_value=candles):
            result = checker._check_volume_confirmation("BTCUSDT")
        assert result["confirmed"] is False
        assert "no volume data" in result["reason"]


class TestConfirmationCheckerCheckTakerFlip:
    """Tests for ConfirmationChecker._check_taker_flip()."""

    def test_taker_flip_detected(self):
        """Z-score was extreme and now improving should detect flip."""
        checker = ConfirmationChecker(MagicMock())
        base_ts = 1000000
        # Need 48+ candles. Build: 30 normal (0.5), 4 extreme (0.05), 16 normal (0.5).
        # This puts the extreme dip within the last 24 z-scores.
        ratios = [0.5] * 30 + [0.05] * 4 + [0.5] * 16
        candles = [make_taker_candle(r, base_ts + i * 3600_000)
                   for i, r in enumerate(ratios)]
        with patch("src.confirmation.get_taker_ratio_history",
                   return_value=candles):
            result = checker._check_taker_flip("BTCUSDT")
        assert result["confirmed"] is True
        assert "taker flip" in result["reason"].lower()

    def test_no_flip_when_not_extreme(self):
        """No flip when z-score was never extreme."""
        checker = ConfirmationChecker(MagicMock())
        base_ts = 1000000
        ratios = [0.5] * 48
        candles = [make_taker_candle(r, base_ts + i * 3600_000)
                   for i, r in enumerate(ratios)]
        with patch("src.confirmation.get_taker_ratio_history",
                   return_value=candles):
            result = checker._check_taker_flip("BTCUSDT")
        assert result["confirmed"] is False
        assert "no flip" in result["reason"]

    def test_insufficient_taker_data(self):
        """Fewer than 48 candles should return not confirmed."""
        checker = ConfirmationChecker(MagicMock())
        candles = [make_taker_candle(0.5, 1000000 + i * 3600_000)
                   for i in range(20)]
        with patch("src.confirmation.get_taker_ratio_history",
                   return_value=candles):
            result = checker._check_taker_flip("BTCUSDT")
        assert result["confirmed"] is False
        assert "insufficient" in result["reason"].lower()

    def test_api_exception_taker(self):
        """API exception should return not confirmed."""
        checker = ConfirmationChecker(MagicMock())
        with patch("src.confirmation.get_taker_ratio_history",
                   side_effect=Exception("API error")):
            result = checker._check_taker_flip("BTCUSDT")
        assert result["confirmed"] is False
        assert "no taker data" in result["reason"]

    def test_empty_pairs_after_filter(self):
        """When pairs list is empty after filter, should not confirm."""
        checker = ConfirmationChecker(MagicMock())
        candles = [{"timestamp": 1000000 + i * 3600_000} for i in range(48)]
        with patch("src.confirmation.get_taker_ratio_history",
                   return_value=candles):
            result = checker._check_taker_flip("BTCUSDT")
        assert result["confirmed"] is False


class TestConfirmationCheckerCheckOrderBook:
    """Tests for ConfirmationChecker._check_order_book().
    Note: _check_order_book imports get_order_book and
    compute_order_book_imbalance INSIDE the method body, so we patch
    src.binance.get_order_book and src.binance.compute_order_book_imbalance.
    """

    def test_bid_dominance_above_threshold(self):
        """Bid dominance above ORDER_BOOK_MIN_BID_DOM should confirm."""
        checker = ConfirmationChecker(MagicMock())
        with patch("src.binance.get_order_book",
                   return_value={"bids": [["100", "1.0"]] * 10,
                                 "asks": [["101", "0.5"]] * 10}):
            with patch("src.binance.compute_order_book_imbalance",
                       return_value=0.75):
                result = checker._check_order_book("BTCUSDT")
        assert result["confirmed"] is True
        assert "bid dominance" in result["reason"]

    def test_bid_dominance_below_threshold(self):
        """Bid dominance below threshold should not confirm."""
        checker = ConfirmationChecker(MagicMock())
        with patch("src.binance.get_order_book",
                   return_value={"bids": [["100", "0.5"]] * 10,
                                 "asks": [["101", "1.0"]] * 10}):
            with patch("src.binance.compute_order_book_imbalance",
                       return_value=0.40):
                result = checker._check_order_book("BTCUSDT")
        assert result["confirmed"] is False
        assert "below" in result["reason"]

    def test_api_error_order_book(self):
        """API error should return not confirmed."""
        checker = ConfirmationChecker(MagicMock())
        with patch("src.binance.get_order_book",
                   side_effect=Exception("network error")):
            result = checker._check_order_book("BTCUSDT")
        assert result["confirmed"] is False
        assert "error" in result["reason"]


class TestConfirmationCheckerCheckEntry:
    """Tests for ConfirmationChecker._check_entry()."""

    def test_entry_confirmed_2_of_3_pass(self):
        """2/3 checks passing should confirm entry."""
        checker = ConfirmationChecker(MagicMock())
        with patch.object(checker, "_check_negative_catalyst",
                          return_value={"denied": False}):
            with patch.object(checker, "_check_premove",
                              return_value={"denied": False}):
                with patch.object(checker, "_check_liquidity_and_spread",
                                  return_value={"denied": False}):
                    with patch.object(checker, "_check_price_action",
                                      return_value={"confirmed": True, "reason": "pa"}):
                        with patch.object(checker, "_check_order_book",
                                          return_value={"confirmed": True, "reason": "ob"}):
                            with patch.object(checker, "_check_taker_flip",
                                              return_value={"confirmed": False, "reason": "tf"}):
                                result = checker._check_entry("BTCUSDT")
        assert result["confirmed"] is True
        assert "entry conditions met" in result["reason"]

    def test_entry_denied_0_of_3_pass(self):
        """0/3 checks passing should deny entry."""
        checker = ConfirmationChecker(MagicMock())
        with patch.object(checker, "_check_negative_catalyst",
                          return_value={"denied": False}):
            with patch.object(checker, "_check_premove",
                              return_value={"denied": False}):
                with patch.object(checker, "_check_liquidity_and_spread",
                                  return_value={"denied": False}):
                    with patch.object(checker, "_check_price_action",
                                      return_value={"confirmed": False}):
                        with patch.object(checker, "_check_order_book",
                                          return_value={"confirmed": False}):
                            with patch.object(checker, "_check_taker_flip",
                                              return_value={"confirmed": False}):
                                result = checker._check_entry("BTCUSDT")
        assert result["confirmed"] is False
        assert "not met" in result["reason"]

    def test_entry_denied_1_of_3_pass(self):
        """1/3 checks passing should deny entry."""
        checker = ConfirmationChecker(MagicMock())
        with patch.object(checker, "_check_negative_catalyst",
                          return_value={"denied": False}):
            with patch.object(checker, "_check_premove",
                              return_value={"denied": False}):
                with patch.object(checker, "_check_liquidity_and_spread",
                                  return_value={"denied": False}):
                    with patch.object(checker, "_check_price_action",
                                      return_value={"confirmed": True}):
                        with patch.object(checker, "_check_order_book",
                                          return_value={"confirmed": False}):
                            with patch.object(checker, "_check_taker_flip",
                                              return_value={"confirmed": False}):
                                result = checker._check_entry("BTCUSDT")
        assert result["confirmed"] is False


class TestConfirmationCheckerCheckSingle:
    """Tests for ConfirmationChecker._check_single()."""

    def test_all_checks_pass_confirmed(self):
        """All 4 checks passing should return confirmed."""
        checker = ConfirmationChecker(MagicMock())
        with patch.object(checker, "_check_negative_catalyst",
                          return_value={"denied": False}):
            with patch.object(checker, "_check_premove",
                              return_value={"denied": False}):
                with patch.object(checker, "_check_liquidity_and_spread",
                                  return_value={"denied": False}):
                    with patch.object(checker, "_check_price_action",
                                      return_value={"confirmed": True, "reason": "pa"}):
                        with patch.object(checker, "_check_volume_confirmation",
                                          return_value={"confirmed": True, "reason": "vc"}):
                            with patch.object(checker, "_check_order_book",
                                              return_value={"confirmed": True, "reason": "ob"}):
                                with patch.object(checker, "_check_taker_flip",
                                                  return_value={"confirmed": True, "reason": "tf"}):
                                    result = checker._check_single("BTCUSDT")
        assert result["confirmed"] is True
        assert result["denied"] is False
        assert result["checks_passed"] == 4
        assert result["checks_total"] == 4

    def test_half_pass_confirmed(self):
        """2/4 checks passing should still confirm (>= half)."""
        checker = ConfirmationChecker(MagicMock())
        with patch.object(checker, "_check_negative_catalyst",
                          return_value={"denied": False}):
            with patch.object(checker, "_check_premove",
                              return_value={"denied": False}):
                with patch.object(checker, "_check_liquidity_and_spread",
                                  return_value={"denied": False}):
                    with patch.object(checker, "_check_price_action",
                                      return_value={"confirmed": True, "reason": "pa"}):
                        with patch.object(checker, "_check_volume_confirmation",
                                          return_value={"confirmed": True, "reason": "vc"}):
                            with patch.object(checker, "_check_order_book",
                                              return_value={"confirmed": False}):
                                with patch.object(checker, "_check_taker_flip",
                                                  return_value={"confirmed": False}):
                                    result = checker._check_single("BTCUSDT")
        assert result["confirmed"] is True
        assert result["denied"] is False
        assert result["checks_passed"] == 2

    def test_zero_pass_denied(self):
        """0/4 checks passing should return denied."""
        checker = ConfirmationChecker(MagicMock())
        with patch.object(checker, "_check_negative_catalyst",
                          return_value={"denied": False}):
            with patch.object(checker, "_check_premove",
                              return_value={"denied": False}):
                with patch.object(checker, "_check_liquidity_and_spread",
                                  return_value={"denied": False}):
                    with patch.object(checker, "_check_price_action",
                                      return_value={"confirmed": False}):
                        with patch.object(checker, "_check_volume_confirmation",
                                          return_value={"confirmed": False}):
                            with patch.object(checker, "_check_order_book",
                                              return_value={"confirmed": False}):
                                with patch.object(checker, "_check_taker_flip",
                                                  return_value={"confirmed": False}):
                                    result = checker._check_single("BTCUSDT")
        assert result["confirmed"] is False
        assert result["denied"] is True
        assert result["checks_passed"] == 0

    def test_some_pass_pending(self):
        """1/4 checks passing should return pending."""
        checker = ConfirmationChecker(MagicMock())
        with patch.object(checker, "_check_negative_catalyst",
                          return_value={"denied": False}):
            with patch.object(checker, "_check_premove",
                              return_value={"denied": False}):
                with patch.object(checker, "_check_liquidity_and_spread",
                                  return_value={"denied": False}):
                    with patch.object(checker, "_check_price_action",
                                      return_value={"confirmed": True, "reason": "pa"}):
                        with patch.object(checker, "_check_volume_confirmation",
                                          return_value={"confirmed": False}):
                            with patch.object(checker, "_check_order_book",
                                              return_value={"confirmed": False}):
                                with patch.object(checker, "_check_taker_flip",
                                                  return_value={"confirmed": False}):
                                    result = checker._check_single("BTCUSDT")
        assert result["confirmed"] is False
        assert result["denied"] is False
        assert result["checks_passed"] == 1
        assert "partial confirmation" in result["reason"]


class TestConfirmationCheckerRunConfirmation:
    """Tests for ConfirmationChecker.run_confirmation()."""

    def test_full_flow_confirmed_and_promoted(self):
        """Candidate passing checks should be promoted to confirmation and entry."""
        stage_mgr = MagicMock()
        stage_mgr.get_watchlist_candidates.return_value = [
            {"id": 1, "symbol": "BTCUSDT", "score": 2, "signals_fired": "funding_extreme"}
        ]
        checker = ConfirmationChecker(stage_mgr)
        with patch.object(checker, "_check_single",
                          return_value={
                              "confirmed": True, "denied": False,
                              "symbol": "BTCUSDT", "reason": "all good",
                          }):
            with patch.object(checker, "_check_entry",
                              return_value={
                                  "confirmed": True, "symbol": "BTCUSDT",
                                  "reason": "entry ok",
                              }):
                results = checker.run_confirmation()
        assert len(results) == 1
        assert results[0]["confirmed"]
        assert results[0].get("promoted_to_entry")
        stage_mgr.promote_to_confirmation.assert_called_once()
        stage_mgr.promote_to_entry.assert_called_once()

    def test_denied_candidate_expired(self):
        """Candidate failing all checks should be expired."""
        stage_mgr = MagicMock()
        stage_mgr.get_watchlist_candidates.return_value = [
            {"id": 2, "symbol": "ETHUSDT", "score": 1, "signals_fired": ""}
        ]
        checker = ConfirmationChecker(stage_mgr)
        with patch.object(checker, "_check_single",
                          return_value={
                              "confirmed": False, "denied": True,
                              "symbol": "ETHUSDT", "reason": "no checks passed",
                          }):
            results = checker.run_confirmation()
        assert len(results) == 1
        assert results[0]["denied"]
        stage_mgr.expire.assert_called_once()

    def test_partial_pass_no_action(self):
        """Candidate with partial pass should be left pending."""
        stage_mgr = MagicMock()
        stage_mgr.get_watchlist_candidates.return_value = [
            {"id": 3, "symbol": "SOLUSDT", "score": 2, "signals_fired": "funding_extreme"}
        ]
        checker = ConfirmationChecker(stage_mgr)
        with patch.object(checker, "_check_single",
                          return_value={
                              "confirmed": False, "denied": False,
                              "symbol": "SOLUSDT", "reason": "partial confirmation",
                          }):
            results = checker.run_confirmation()
        assert len(results) == 1
        assert not results[0]["confirmed"]
        assert not results[0]["denied"]
        stage_mgr.promote_to_confirmation.assert_not_called()
        stage_mgr.expire.assert_not_called()

    def test_symbols_filter(self):
        """Filtering by symbols should only check matching candidates."""
        stage_mgr = MagicMock()
        stage_mgr.get_watchlist_candidates.return_value = [
            {"id": 1, "symbol": "BTCUSDT", "score": 2, "signals_fired": ""},
            {"id": 2, "symbol": "ETHUSDT", "score": 2, "signals_fired": ""},
        ]
        checker = ConfirmationChecker(stage_mgr)
        with patch.object(checker, "_check_single",
                          return_value={
                              "confirmed": True, "denied": False,
                              "symbol": "checked", "reason": "",
                          }):
            with patch.object(checker, "_check_entry",
                              return_value={"confirmed": False, "symbol": "checked"}):
                results = checker.run_confirmation(symbols=["BTCUSDT"])
        assert len(results) == 1

    def test_empty_candidates_returns_empty(self):
        """Empty candidates list should return empty list."""
        stage_mgr = MagicMock()
        stage_mgr.get_watchlist_candidates.return_value = []
        checker = ConfirmationChecker(stage_mgr)
        results = checker.run_confirmation()
        assert results == []

    def test_expire_stale_called(self):
        """run_confirmation should call expire_stale at the end."""
        stage_mgr = MagicMock()
        stage_mgr.get_watchlist_candidates.return_value = [
            {"id": 1, "symbol": "BTCUSDT", "score": 2, "signals_fired": ""}
        ]
        checker = ConfirmationChecker(stage_mgr)
        with patch.object(checker, "_check_single",
                          return_value={
                              "confirmed": False, "denied": True,
                              "symbol": "BTCUSDT", "reason": "no",
                          }):
            checker.run_confirmation()
        stage_mgr.expire_stale.assert_called_once()


class TestConfirmationCheckerSafetyGateBlocking:
    """Tests proving safety gates block entry and confirmation."""

    def test_entry_blocked_by_negative_catalyst(self):
        """Denied negative catalyst should block entry even if 2/3 entry checks pass."""
        checker = ConfirmationChecker(MagicMock())
        with patch.object(checker, "_check_negative_catalyst",
                          return_value={"denied": True, "reason": "negative catalyst blocks entry"}):
            with patch.object(checker, "_check_premove",
                              return_value={"denied": False}):
                with patch.object(checker, "_check_liquidity_and_spread",
                                  return_value={"denied": False}):
                    with patch.object(checker, "_check_price_action",
                                      return_value={"confirmed": True}):
                        with patch.object(checker, "_check_order_book",
                                          return_value={"confirmed": True}):
                            with patch.object(checker, "_check_taker_flip",
                                              return_value={"confirmed": False}):
                                result = checker._check_entry("BTCUSDT")
        assert result["confirmed"] is False
        assert "entry blocked: negative catalyst" in result["reason"]

    def test_entry_blocked_by_premove(self):
        """Denied pre-move should block entry even if 2/3 entry checks pass."""
        checker = ConfirmationChecker(MagicMock())
        with patch.object(checker, "_check_negative_catalyst",
                          return_value={"denied": False}):
            with patch.object(checker, "_check_premove",
                              return_value={"denied": True, "reason": "already pumped too far"}):
                with patch.object(checker, "_check_liquidity_and_spread",
                                  return_value={"denied": False}):
                    with patch.object(checker, "_check_price_action",
                                      return_value={"confirmed": True}):
                        with patch.object(checker, "_check_order_book",
                                          return_value={"confirmed": True}):
                            with patch.object(checker, "_check_taker_flip",
                                              return_value={"confirmed": False}):
                                result = checker._check_entry("BTCUSDT")
        assert result["confirmed"] is False
        assert "entry blocked: already pumped too far" in result["reason"]

    def test_entry_blocked_by_liquidity(self):
        """Denied liquidity/spread should block entry even if 2/3 entry checks pass."""
        checker = ConfirmationChecker(MagicMock())
        with patch.object(checker, "_check_negative_catalyst",
                          return_value={"denied": False}):
            with patch.object(checker, "_check_premove",
                              return_value={"denied": False}):
                with patch.object(checker, "_check_liquidity_and_spread",
                                  return_value={"denied": True, "reason": "spread too wide (0.60% > 0.5%)"}):
                    with patch.object(checker, "_check_price_action",
                                      return_value={"confirmed": True}):
                        with patch.object(checker, "_check_order_book",
                                          return_value={"confirmed": True}):
                            with patch.object(checker, "_check_taker_flip",
                                              return_value={"confirmed": False}):
                                result = checker._check_entry("BTCUSDT")
        assert result["confirmed"] is False
        assert "entry blocked: spread too wide" in result["reason"]

    def test_single_denied_by_negative_catalyst_before_main_checks(self):
        """Negative catalyst denial should happen before main checks (checks_total == 0)."""
        checker = ConfirmationChecker(MagicMock())
        with patch.object(checker, "_check_negative_catalyst",
                          return_value={"denied": True, "reason": "negative catalyst blocks entry"}):
            with patch.object(checker, "_check_premove",
                              return_value={"denied": False}):
                with patch.object(checker, "_check_liquidity_and_spread",
                                  return_value={"denied": False}):
                    with patch.object(checker, "_check_price_action",
                                      return_value={"confirmed": True}):
                        with patch.object(checker, "_check_volume_confirmation",
                                          return_value={"confirmed": True}):
                            with patch.object(checker, "_check_order_book",
                                              return_value={"confirmed": True}):
                                with patch.object(checker, "_check_taker_flip",
                                                  return_value={"confirmed": True}):
                                    result = checker._check_single("BTCUSDT")
        assert result["denied"] is True
        assert result["checks_total"] == 0
        assert "negative catalyst" in result["reason"]

    def test_single_denied_by_premove_before_main_checks(self):
        """Pre-move denial should happen before main checks (checks_total == 0)."""
        checker = ConfirmationChecker(MagicMock())
        with patch.object(checker, "_check_negative_catalyst",
                          return_value={"denied": False}):
            with patch.object(checker, "_check_premove",
                              return_value={"denied": True, "reason": "already pumped too far"}):
                with patch.object(checker, "_check_liquidity_and_spread",
                                  return_value={"denied": False}):
                    with patch.object(checker, "_check_price_action",
                                      return_value={"confirmed": True}):
                        with patch.object(checker, "_check_volume_confirmation",
                                          return_value={"confirmed": True}):
                            with patch.object(checker, "_check_order_book",
                                              return_value={"confirmed": True}):
                                with patch.object(checker, "_check_taker_flip",
                                                  return_value={"confirmed": True}):
                                    result = checker._check_single("BTCUSDT")
        assert result["denied"] is True
        assert result["checks_total"] == 0
        assert "already pumped too far" in result["reason"]

    def test_single_denied_by_liquidity_before_main_checks(self):
        """Liquidity denial should happen before main checks (checks_total == 0)."""
        checker = ConfirmationChecker(MagicMock())
        with patch.object(checker, "_check_negative_catalyst",
                          return_value={"denied": False}):
            with patch.object(checker, "_check_premove",
                              return_value={"denied": False}):
                with patch.object(checker, "_check_liquidity_and_spread",
                                  return_value={"denied": True, "reason": "spread too wide"}):
                    with patch.object(checker, "_check_price_action",
                                      return_value={"confirmed": True}):
                        with patch.object(checker, "_check_volume_confirmation",
                                          return_value={"confirmed": True}):
                            with patch.object(checker, "_check_order_book",
                                              return_value={"confirmed": True}):
                                with patch.object(checker, "_check_taker_flip",
                                                  return_value={"confirmed": True}):
                                    result = checker._check_single("BTCUSDT")
        assert result["denied"] is True
        assert result["checks_total"] == 0
        assert "spread too wide" in result["reason"]


class TestConfirmationCheckerCheckPremove:
    """Tests for _check_premove actual math and error handling."""

    def test_premove_denied_1h_exceeded(self):
        """1h move exceeding threshold should deny."""
        checker = ConfirmationChecker(MagicMock())
        # Build 48 candles: base=100, last=115 -> 1h=15% > 12%
        closes = [100.0] * 46 + [100.0, 115.0]
        candles = [make_candle(c) for c in closes]
        with patch("src.confirmation.get_klines", return_value=candles):
            result = checker._check_premove("BTCUSDT")
        assert result["denied"] is True
        assert "already pumped too far" in result["reason"]
        assert "1h:" in result["reason"]

    def test_premove_denied_4h_exceeded(self):
        """4h move exceeding threshold should deny."""
        checker = ConfirmationChecker(MagicMock())
        # closes[-5]=100, current=125 -> 4h=25% > 20%
        closes = [100.0] * 43 + [100.0] + [125.0] * 4
        candles = [make_candle(c) for c in closes]
        with patch("src.confirmation.get_klines", return_value=candles):
            result = checker._check_premove("BTCUSDT")
        assert result["denied"] is True
        assert "already pumped too far" in result["reason"]
        assert "4h:" in result["reason"]

    def test_premove_denied_24h_exceeded(self):
        """24h move exceeding threshold should deny."""
        checker = ConfirmationChecker(MagicMock())
        # closes[-25]=100, current=140 -> 24h=40% > 35%
        # indices 0..23 = 100, indices 24..47 = 140
        closes = [100.0] * 24 + [140.0] * 24
        candles = [make_candle(c) for c in closes]
        with patch("src.confirmation.get_klines", return_value=candles):
            result = checker._check_premove("BTCUSDT")
        assert result["denied"] is True
        assert "already pumped too far" in result["reason"]
        assert "24h:" in result["reason"]

    def test_premove_allowed_within_threshold(self):
        """All moves within threshold should not deny."""
        checker = ConfirmationChecker(MagicMock())
        closes = [100.0] * 23 + [101.0] * 25
        candles = [make_candle(c) for c in closes]
        with patch("src.confirmation.get_klines", return_value=candles):
            result = checker._check_premove("BTCUSDT")
        assert result["denied"] is False
        assert result["reason"] == ""

    def test_premove_insufficient_candles(self):
        """Fewer than 24 candles should return non-blocking with reason."""
        checker = ConfirmationChecker(MagicMock())
        candles = [make_candle(100.0) for _ in range(23)]
        with patch("src.confirmation.get_klines", return_value=candles):
            result = checker._check_premove("BTCUSDT")
        assert result["denied"] is False
        assert "insufficient data" in result["reason"]

    def test_premove_api_exception(self):
        """API exception should return non-blocking."""
        checker = ConfirmationChecker(MagicMock())
        with patch("src.confirmation.get_klines", side_effect=Exception("API error")):
            result = checker._check_premove("BTCUSDT")
        assert result["denied"] is False
        assert "no klines" in result["reason"]

    def test_premove_missing_close_prices(self):
        """Candles without close prices should return non-blocking."""
        checker = ConfirmationChecker(MagicMock())
        candles = [{"v": 1000} for _ in range(48)]
        with patch("src.confirmation.get_klines", return_value=candles):
            result = checker._check_premove("BTCUSDT")
        assert result["denied"] is False
        assert "no close prices" in result["reason"]


class TestConfirmationCheckerCheckLiquidityAndSpread:
    """Tests for _check_liquidity_and_spread actual math and error handling."""

    def test_liquidity_denied_spread_too_wide(self):
        """Spread > 0.5% should deny."""
        checker = ConfirmationChecker(MagicMock())
        depth = {"bids": [["100", "1"]], "asks": [["100.6", "1"]]}
        with patch("src.binance.get_order_book", return_value=depth):
            with patch("src.binance.compute_order_book_imbalance", return_value=0.60):
                result = checker._check_liquidity_and_spread("BTCUSDT")
        assert result["denied"] is True
        assert "spread too wide" in result["reason"]
        assert "0.60%" in result["reason"]

    def test_liquidity_denied_bid_dominance_too_low(self):
        """Bid dominance < 0.55 should deny."""
        checker = ConfirmationChecker(MagicMock())
        depth = {"bids": [["100", "1"]], "asks": [["100.1", "1"]]}
        with patch("src.binance.get_order_book", return_value=depth):
            with patch("src.binance.compute_order_book_imbalance", return_value=0.50):
                result = checker._check_liquidity_and_spread("BTCUSDT")
        assert result["denied"] is True
        assert "bid dominance too low" in result["reason"]
        assert "0.500" in result["reason"]

    def test_liquidity_allowed_good_conditions(self):
        """Spread <= 0.5% and bid dominance >= 0.55 should allow."""
        checker = ConfirmationChecker(MagicMock())
        depth = {"bids": [["100", "1"]], "asks": [["100.1", "1"]]}
        with patch("src.binance.get_order_book", return_value=depth):
            with patch("src.binance.compute_order_book_imbalance", return_value=0.60):
                result = checker._check_liquidity_and_spread("BTCUSDT")
        assert result["denied"] is False
        assert result["reason"] == ""

    def test_liquidity_api_exception_non_blocking(self):
        """API exception should return non-blocking with error reason."""
        checker = ConfirmationChecker(MagicMock())
        with patch("src.binance.get_order_book", side_effect=Exception("network error")):
            result = checker._check_liquidity_and_spread("BTCUSDT")
        assert result["denied"] is False
        assert "liquidity check error" in result["reason"]


class TestConfirmationCatalystReduction:
    """Tests for catalyst-based confirmation requirement reduction."""

    def test_major_catalyst_reduces_confirmation(self):
        """Major catalyst should reduce required confirmations to 1."""
        from src.config import CATALYST_MAJOR_SCORE
        checker = ConfirmationChecker(MagicMock(), catalyst_results={
            "BTCUSDT": MagicMock(score=CATALYST_MAJOR_SCORE, is_negative_catalyst=False),
        })
        with patch.object(checker, "_check_negative_catalyst",
                          return_value={"denied": False}):
            with patch.object(checker, "_check_premove",
                              return_value={"denied": False}):
                with patch.object(checker, "_check_liquidity_and_spread",
                                  return_value={"denied": False}):
                    with patch.object(checker, "_check_price_action",
                                      return_value={"confirmed": True, "reason": "pa"}):
                        with patch.object(checker, "_check_volume_confirmation",
                                          return_value={"confirmed": False}):
                            with patch.object(checker, "_check_order_book",
                                              return_value={"confirmed": False}):
                                with patch.object(checker, "_check_taker_flip",
                                                  return_value={"confirmed": False}):
                                    result = checker._check_single("BTCUSDT")
        assert result["confirmed"] is True
        assert result["checks_passed"] == 1

    def test_strong_catalyst_reduces_confirmation(self):
        """Strong catalyst should reduce required confirmations to 2."""
        from src.config import CATALYST_STRONG_SCORE
        checker = ConfirmationChecker(MagicMock(), catalyst_results={
            "BTCUSDT": MagicMock(score=CATALYST_STRONG_SCORE, is_negative_catalyst=False),
        })
        with patch.object(checker, "_check_negative_catalyst",
                          return_value={"denied": False}):
            with patch.object(checker, "_check_premove",
                              return_value={"denied": False}):
                with patch.object(checker, "_check_liquidity_and_spread",
                                  return_value={"denied": False}):
                    with patch.object(checker, "_check_price_action",
                                      return_value={"confirmed": True, "reason": "pa"}):
                        with patch.object(checker, "_check_volume_confirmation",
                                          return_value={"confirmed": True, "reason": "vc"}):
                            with patch.object(checker, "_check_order_book",
                                              return_value={"confirmed": False}):
                                with patch.object(checker, "_check_taker_flip",
                                                  return_value={"confirmed": False}):
                                    result = checker._check_single("BTCUSDT")
        assert result["confirmed"] is True
        assert result["checks_passed"] == 2

    def test_default_confirmation_unchanged(self):
        """No catalyst should require default (2) confirmations."""
        checker = ConfirmationChecker(MagicMock(), catalyst_results={})
        with patch.object(checker, "_check_negative_catalyst",
                          return_value={"denied": False}):
            with patch.object(checker, "_check_premove",
                              return_value={"denied": False}):
                with patch.object(checker, "_check_liquidity_and_spread",
                                  return_value={"denied": False}):
                    with patch.object(checker, "_check_price_action",
                                      return_value={"confirmed": True, "reason": "pa"}):
                        with patch.object(checker, "_check_volume_confirmation",
                                          return_value={"confirmed": True, "reason": "vc"}):
                            with patch.object(checker, "_check_order_book",
                                              return_value={"confirmed": False}):
                                with patch.object(checker, "_check_taker_flip",
                                                  return_value={"confirmed": False}):
                                    result = checker._check_single("BTCUSDT")
        assert result["confirmed"] is True
        assert result["checks_passed"] == 2


class TestConfirmationTwoTierNegative:
    """Tests for two-tier negative catalyst handling in Phase 2."""

    def test_blocking_negative_blocks_entry(self):
        """Blocking negative catalyst must deny entry unconditionally."""
        checker = ConfirmationChecker(MagicMock(), catalyst_results={
            "BTCUSDT": MagicMock(
                score=0.95,
                is_negative_catalyst=True,
                has_blocking_negative_catalyst=True,
            ),
        })
        result = checker._check_negative_catalyst("BTCUSDT")
        assert result["denied"] is True
        assert "blocking negative catalyst" in result["reason"]

    def test_warning_negative_does_not_auto_block_entry(self):
        """Warning negative catalyst should not auto-deny entry."""
        checker = ConfirmationChecker(MagicMock(), catalyst_results={
            "BTCUSDT": MagicMock(
                score=0.60,
                is_negative_catalyst=True,
                has_blocking_negative_catalyst=False,
            ),
        })
        result = checker._check_negative_catalyst("BTCUSDT")
        assert result["denied"] is False

    def test_warning_negative_increases_confirmation_required(self):
        """Warning negative should increase required confirmations by 1."""
        checker = ConfirmationChecker(MagicMock(), catalyst_results={
            "BTCUSDT": MagicMock(
                score=0.0,
                is_negative_catalyst=True,
                has_blocking_negative_catalyst=False,
            ),
        })
        with patch.object(checker, "_check_negative_catalyst",
                          return_value={"denied": False}):
            with patch.object(checker, "_check_premove",
                              return_value={"denied": False}):
                with patch.object(checker, "_check_liquidity_and_spread",
                                  return_value={"denied": False}):
                    with patch.object(checker, "_check_price_action",
                                      return_value={"confirmed": True, "reason": "pa"}):
                        with patch.object(checker, "_check_volume_confirmation",
                                          return_value={"confirmed": True, "reason": "vc"}):
                            with patch.object(checker, "_check_order_book",
                                              return_value={"confirmed": False}):
                                with patch.object(checker, "_check_taker_flip",
                                                  return_value={"confirmed": False}):
                                    result = checker._check_single("BTCUSDT")
        # Default required = 2, warning negative adds 1 -> 3 needed.
        # Only 2 checks pass, so should be pending (not confirmed).
        assert result["confirmed"] is False
        assert result["denied"] is False
        assert "partial confirmation" in result["reason"]

    def test_phase2_preserves_negative_state_from_db(self):
        """Phase 2 must use persisted has_blocking_negative_catalyst, not infer from event_type."""
        checker = ConfirmationChecker(MagicMock(), catalyst_results={
            "BTCUSDT": MagicMock(
                score=0.85,
                is_negative_catalyst=True,
                has_blocking_negative_catalyst=True,
            ),
        })
        result = checker._check_single("BTCUSDT")
        assert result["denied"] is True
        assert result["checks_total"] == 0
