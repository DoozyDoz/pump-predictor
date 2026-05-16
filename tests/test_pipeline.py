"""Unit tests for src/pipeline.py staged workflow functions."""

import os
os.environ["TELEGRAM_BOT_TOKEN"] = "test:token"
os.environ["TELEGRAM_CHAT_ID"] = "12345"

from unittest.mock import MagicMock



def _raising(exc_msg="error"):
    """Helper to create a function that raises an exception (monkeypatch
    setattr doesn't support side_effect directly)."""
    def _fn(*args, **kwargs):
        raise Exception(exc_msg)
    return _fn


class TestRunPhase1Watchlist:
    """Tests for run_phase1_watchlist()."""

    def test_generates_candidates(self, monkeypatch):
        """Should generate watchlist candidates when signals are present."""
        monkeypatch.setattr("src.pipeline.init_db", lambda: None)
        monkeypatch.setattr("src.pipeline.refresh_universe",
                            lambda: ["BTCUSDT", "ETHUSDT"])
        monkeypatch.setattr("src.pipeline.daily_volume_check",
                            lambda syms: syms)
        monkeypatch.setattr("src.pipeline.get_24h_tickers",
                            lambda: [{"symbol": "BTCUSDT", "quoteVolume": "1000",
                                      "highPrice": "100", "lowPrice": "90",
                                      "lastPrice": "95", "priceChangePercent": "0",
                                      "count": "100"}])
        monkeypatch.setattr("src.pipeline._compute_signals",
                            lambda syms: (
                                [], [], [], [], [],
                                "FULL", [], [], {},
                            ))
        monkeypatch.setattr("src.pipeline._store_snapshots",
                            lambda *args, **kwargs: None)
        monkeypatch.setattr("src.pipeline._build_qualitative",
                            lambda *args, **kwargs: {})

        # Use a class to mock the regime enum
        class MockRegime:
            value = "favorable"

        monkeypatch.setattr("src.pipeline.detect_regime",
                            lambda: MockRegime())
        monkeypatch.setattr("src.pipeline.is_suppressed",
                            lambda r: False)

        monkeypatch.setattr(
            "src.pipeline.generate_watchlist",
            lambda *args, **kwargs: [
                {"symbol": "BTCUSDT", "score": 2,
                 "fired_signals": "funding_extreme", "catalyst_boost": 0.0,
                 "adjusted_score": 2},
            ],
        )
        monkeypatch.setattr("src.pipeline._send_telegram_stage",
                            lambda msg, stage: None)

        from src.pipeline import run_phase1_watchlist
        candidates = run_phase1_watchlist()
        assert len(candidates) >= 1
        assert candidates[0]["symbol"] == "BTCUSDT"

    def test_no_candidates_sends_message(self, monkeypatch):
        """When no candidates, Telegram message should say no candidates."""
        monkeypatch.setattr("src.pipeline.init_db", lambda: None)
        monkeypatch.setattr("src.pipeline.refresh_universe",
                            lambda: ["BTCUSDT"])
        monkeypatch.setattr("src.pipeline.daily_volume_check",
                            lambda syms: syms)
        monkeypatch.setattr("src.pipeline.get_24h_tickers",
                            lambda: [{"symbol": "BTCUSDT", "quoteVolume": "1000",
                                      "highPrice": "100", "lowPrice": "90",
                                      "lastPrice": "95", "priceChangePercent": "0",
                                      "count": "100"}])
        monkeypatch.setattr("src.pipeline._compute_signals",
                            lambda syms: (
                                [], [], [], [], [],
                                "FULL", [], [], {},
                            ))
        monkeypatch.setattr("src.pipeline._store_snapshots",
                            lambda *args, **kwargs: None)
        monkeypatch.setattr("src.pipeline._build_qualitative",
                            lambda *args, **kwargs: {})

        class MockRegime:
            value = "favorable"

        monkeypatch.setattr("src.pipeline.detect_regime",
                            lambda: MockRegime())
        monkeypatch.setattr("src.pipeline.is_suppressed",
                            lambda r: False)
        monkeypatch.setattr("src.pipeline.generate_watchlist",
                            lambda *args, **kwargs: [])

        sent_messages = []

        def fake_send(msg, stage):
            sent_messages.append((msg, stage))

        monkeypatch.setattr("src.pipeline._send_telegram_stage", fake_send)

        from src.pipeline import run_phase1_watchlist
        candidates = run_phase1_watchlist()
        assert candidates == []
        assert len(sent_messages) >= 1
        assert "No candidates" in sent_messages[0][0]

    def test_regime_suppressed_returns_empty(self, monkeypatch):
        """When regime is unfavorable, should return empty and not generate watchlist."""
        monkeypatch.setattr("src.pipeline.init_db", lambda: None)
        monkeypatch.setattr("src.pipeline.refresh_universe",
                            lambda: ["BTCUSDT"])
        monkeypatch.setattr("src.pipeline.daily_volume_check",
                            lambda syms: syms)
        monkeypatch.setattr("src.pipeline.get_24h_tickers",
                            lambda: [])

        class MockRegime:
            value = "unfavorable"

        monkeypatch.setattr("src.pipeline.detect_regime",
                            lambda: MockRegime())
        monkeypatch.setattr("src.pipeline.is_suppressed",
                            lambda r: True)
        monkeypatch.setattr("src.pipeline._send_telegram_stage",
                            lambda msg, stage: None)

        from src.pipeline import run_phase1_watchlist
        candidates = run_phase1_watchlist(symbols=["BTCUSDT"])
        assert candidates == []

    def test_api_failure_tickers_returns_empty(self, monkeypatch):
        """When 24h tickers API fails, should return empty."""
        monkeypatch.setattr("src.pipeline.init_db", lambda: None)
        monkeypatch.setattr("src.pipeline.refresh_universe",
                            lambda: ["BTCUSDT"])
        monkeypatch.setattr("src.pipeline.daily_volume_check",
                            lambda syms: syms)
        monkeypatch.setattr("src.pipeline.get_24h_tickers",
                            _raising("API error"))
        monkeypatch.setattr("src.pipeline.is_suppressed", lambda r: False)

        from src.pipeline import run_phase1_watchlist
        candidates = run_phase1_watchlist(symbols=["BTCUSDT"])
        assert candidates == []


class TestRunPhase2Confirmation:
    """Tests for run_phase2_confirmation()."""

    def test_confirmed_results_logged(self, monkeypatch):
        """Confirmed results should be returned."""
        mock_checker = MagicMock()
        mock_checker.run_confirmation.return_value = [
            {"symbol": "BTCUSDT", "confirmed": True, "denied": False,
             "reason": "all good", "promoted_to_entry": False},
        ]

        monkeypatch.setattr("src.pipeline.StageManager",
                            lambda: MagicMock())
        # ConfirmationChecker is imported inside run_phase2_confirmation,
        # so we patch at its source module
        monkeypatch.setattr("src.confirmation.ConfirmationChecker",
                            lambda sm: mock_checker)

        from src.pipeline import run_phase2_confirmation
        results = run_phase2_confirmation()
        assert len(results) >= 1
        assert results[0]["confirmed"]

    def test_denied_results_logged(self, monkeypatch):
        """Denied results should be returned."""
        mock_checker = MagicMock()
        mock_checker.run_confirmation.return_value = [
            {"symbol": "SOLUSDT", "confirmed": False, "denied": True,
             "reason": "no checks passed"},
        ]

        monkeypatch.setattr("src.pipeline.StageManager",
                            lambda: MagicMock())
        monkeypatch.setattr("src.confirmation.ConfirmationChecker",
                            lambda sm: mock_checker)

        from src.pipeline import run_phase2_confirmation
        results = run_phase2_confirmation()
        assert len(results) >= 1
        assert results[0]["denied"]

    def test_empty_results_returns_empty(self, monkeypatch):
        """Empty results from checker should return empty."""
        mock_checker = MagicMock()
        mock_checker.run_confirmation.return_value = []

        monkeypatch.setattr("src.pipeline.StageManager",
                            lambda: MagicMock())
        monkeypatch.setattr("src.confirmation.ConfirmationChecker",
                            lambda sm: mock_checker)

        from src.pipeline import run_phase2_confirmation
        results = run_phase2_confirmation()
        assert results == []


class TestRunPhase3Entry:
    """Tests for run_phase3_entry()."""

    def test_computes_atr_and_sends_entry(self, monkeypatch):
        """Should compute ATR and return entry signals."""
        entries = [
            {"symbol": "BTCUSDT", "score": 2, "signals_fired": "funding_extreme"},
        ]

        monkeypatch.setattr("src.pipeline.StageManager",
                            lambda: MagicMock())
        monkeypatch.setattr("src.pipeline.compute_atr",
                            lambda sym: 3.0 if sym == "BTCUSDT" else None)
        monkeypatch.setattr("src.pipeline.risk_position_size",
                            lambda atr, pf: 50.0)
        monkeypatch.setattr("src.pipeline._send_telegram_stage",
                            lambda msg, stage: None)

        from src.pipeline import run_phase3_entry
        results = run_phase3_entry(confirmed_entries=entries, portfolio_usd=1000)
        assert len(results) >= 1
        assert results[0]["symbol"] == "BTCUSDT"
        assert "3.00%" in results[0]["atr_pct"]

    def test_atr_none_uses_fallback(self, monkeypatch):
        """When ATR returns None, should use fallback 2.0."""
        entries = [
            {"symbol": "BTCUSDT", "score": 1, "signals_fired": ""},
        ]

        monkeypatch.setattr("src.pipeline.StageManager",
                            lambda: MagicMock())
        monkeypatch.setattr("src.pipeline.compute_atr",
                            lambda sym: None)
        monkeypatch.setattr("src.pipeline.risk_position_size",
                            lambda atr, pf: 50.0)
        monkeypatch.setattr("src.pipeline._send_telegram_stage",
                            lambda msg, stage: None)

        from src.pipeline import run_phase3_entry
        results = run_phase3_entry(confirmed_entries=entries, portfolio_usd=1000)
        assert len(results) >= 1
        assert "2.00%" in results[0]["atr_pct"]

    def test_no_entries_returns_empty(self, monkeypatch):
        """Empty entries should return empty list."""
        monkeypatch.setattr("src.pipeline.StageManager",
                            lambda: MagicMock())

        from src.pipeline import run_phase3_entry
        results = run_phase3_entry(confirmed_entries=[], portfolio_usd=1000)
        assert results == []

    def test_entries_from_db_when_none_given(self, monkeypatch):
        """When confirmed_entries is None, should load from DB."""
        mock_stage_mgr = MagicMock()
        mock_stage_mgr.get_by_stage.return_value = [
            {"symbol": "BTCUSDT", "score": 2, "signals_fired": "funding_extreme"},
        ]

        monkeypatch.setattr("src.pipeline.StageManager",
                            lambda: mock_stage_mgr)
        monkeypatch.setattr("src.pipeline.compute_atr",
                            lambda sym: 2.5)
        monkeypatch.setattr("src.pipeline.risk_position_size",
                            lambda atr, pf: 50.0)
        monkeypatch.setattr("src.pipeline._send_telegram_stage",
                            lambda msg, stage: None)

        from src.pipeline import run_phase3_entry
        results = run_phase3_entry(confirmed_entries=None, portfolio_usd=1000)
        assert len(results) >= 1
        mock_stage_mgr.get_by_stage.assert_called_once_with("entry")


class TestComputeSignals:
    """Tests for _compute_signals() helper."""

    def test_full_signal_computation(self, monkeypatch):
        """All 5 signals should be computed successfully."""
        monkeypatch.setattr("src.pipeline.compute_all_funding_signals",
                            lambda syms: [MagicMock(symbol="BTCUSDT", fired=True)])
        monkeypatch.setattr("src.pipeline.get_bulk_funding_rates",
                            lambda syms: {"BTCUSDT": 0.001})
        monkeypatch.setattr("src.pipeline.compute_oi_divergence_signal",
                            lambda sym: MagicMock(symbol="BTCUSDT", fired=True))
        monkeypatch.setattr("src.pipeline.compute_ls_ratio_signal",
                            lambda sym: MagicMock(symbol="BTCUSDT", fired=True))
        monkeypatch.setattr("src.pipeline.get_taker_ratio_history",
                            lambda sym, period, limit: [])
        monkeypatch.setattr("src.pipeline.TakerHistory",
                            lambda c: MagicMock())
        monkeypatch.setattr("src.pipeline.compute_taker_ratio_signal",
                            lambda sym, hist: MagicMock(symbol="BTCUSDT",
                                                       fired=True,
                                                       current_ratio=0.5))
        monkeypatch.setattr("src.pipeline.finalize_oi_divergence_signals",
                            lambda sigs: sigs)
        monkeypatch.setattr("src.pipeline.finalize_ls_ratio_signals",
                            lambda sigs: sigs)
        monkeypatch.setattr("src.pipeline.finalize_taker_signals",
                            lambda sigs: sigs)
        monkeypatch.setattr("src.pipeline.compute_order_book_signal",
                            lambda sym: MagicMock(symbol="BTCUSDT", fired=True))
        monkeypatch.setattr("src.pipeline.finalize_order_book_signals",
                            lambda sigs: sigs)

        from src.pipeline import _compute_signals
        result = _compute_signals(["BTCUSDT"])
        assert result is not None
        (fund, oi, ls, taker, book, status, errors, deep_check, rates) = result
        assert status == "FULL"
        assert len(fund) >= 1

    def test_funding_failure_returns_none(self, monkeypatch):
        """Funding computation failure should return None."""
        monkeypatch.setattr("src.pipeline.compute_all_funding_signals",
                            _raising("funding error"))

        from src.pipeline import _compute_signals
        result = _compute_signals(["BTCUSDT"])
        assert result is None

    def test_partial_oi_failure(self, monkeypatch):
        """OI failure should result in PARTIAL status."""
        monkeypatch.setattr("src.pipeline.compute_all_funding_signals",
                            lambda syms: [])
        monkeypatch.setattr("src.pipeline.get_bulk_funding_rates",
                            lambda syms: {"BTCUSDT": 0.001})
        monkeypatch.setattr("src.pipeline.compute_oi_divergence_signal",
                            lambda sym: None)

        from src.pipeline import _compute_signals
        result = _compute_signals(["BTCUSDT"])
        assert result is not None
        (fund, oi, ls, taker, book, status, errors, deep_check, rates) = result
        assert status == "PARTIAL"

    def test_empty_funding_rates_returns_none(self, monkeypatch):
        """Empty funding rate response should return None."""
        monkeypatch.setattr("src.pipeline.compute_all_funding_signals",
                            lambda syms: [])
        monkeypatch.setattr("src.pipeline.get_bulk_funding_rates",
                            lambda syms: {})

        from src.pipeline import _compute_signals
        result = _compute_signals(["BTCUSDT"])
        assert result is None
