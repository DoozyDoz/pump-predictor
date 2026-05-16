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
                            lambda sm, catalyst_results=None: mock_checker)

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
                            lambda sm, catalyst_results=None: mock_checker)

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
                            lambda sm, catalyst_results=None: mock_checker)

        from src.pipeline import run_phase2_confirmation
        results = run_phase2_confirmation()
        assert results == []


class TestRunPhase2DBCatalystLoading:
    """Tests for DB catalyst loading and reconstructed CatalystResult flags."""

    def test_db_catalyst_loading_reconstructs_flags(self, monkeypatch, tmp_path):
        """Watchlist rows with URGENT_CATALYST and negative event types
        should be reconstructed with correct is_major_catalyst / is_negative_catalyst flags.
        """
        import sqlite3
        from contextlib import contextmanager
        import src.db as db_module
        import src.stages as stages_module

        temp_db = str(tmp_path / "test_pipeline.db")
        conn = sqlite3.connect(temp_db)
        conn.row_factory = sqlite3.Row
        conn.executescript(db_module.SCHEMA)

        # Apply watchlist migrations that init_db would run
        for col, col_def in [
            ("catalyst_score", "REAL DEFAULT 0"),
            ("catalyst_event_type", "TEXT DEFAULT ''"),
            ("catalyst_title", "TEXT DEFAULT ''"),
            ("catalyst_source", "TEXT DEFAULT ''"),
            ("catalyst_published_at", "TEXT DEFAULT ''"),
            ("final_alpha_score", "REAL DEFAULT 0"),
            ("priority", "TEXT DEFAULT ''"),
            ("setup_type", "TEXT DEFAULT ''"),
            ("is_negative_catalyst", "INTEGER DEFAULT 0"),
            ("has_blocking_negative_catalyst", "INTEGER DEFAULT 0"),
            ("negative_catalyst_types", "TEXT DEFAULT '[]'"),
            ("negative_catalyst_severities", "TEXT DEFAULT '[]'"),
            ("negative_catalyst_reasons", "TEXT DEFAULT '[]'"),
            ("catalyst_event_ids", "TEXT DEFAULT '[]'"),
            ("price_change_1h", "REAL"),
            ("price_change_4h", "REAL"),
            ("price_change_24h", "REAL"),
        ]:
            try:
                conn.execute(f"ALTER TABLE watchlist ADD COLUMN {col} {col_def}")
            except sqlite3.OperationalError:
                pass

        # Seed token and watchlist with catalyst columns
        conn.execute(
            "INSERT INTO tokens (symbol, exchange, market) VALUES (?, 'B', 'spot')",
            ("BTCUSDT",),
        )
        token_id = conn.execute(
            "SELECT id FROM tokens WHERE symbol = ?", ("BTCUSDT",)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO watchlist (token_id, symbol, score, signals_fired, "
            "catalyst_score, catalyst_event_type, priority, "
            "is_negative_catalyst, has_blocking_negative_catalyst, expired) "
            "VALUES (?, ?, 2, 'funding_extreme', 0.95, 'exploit_or_hack', 'URGENT_CATALYST', 1, 1, FALSE)",
            (token_id, "BTCUSDT"),
        )
        wl_id = conn.execute(
            "SELECT id FROM watchlist WHERE symbol = ?", ("BTCUSDT",)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO stage_progression (watchlist_id, token_id, stage) VALUES (?, ?, 'watchlist')",
            (wl_id, token_id),
        )
        conn.commit()

        @contextmanager
        def mock_db_session():
            try:
                yield conn
            finally:
                pass

        monkeypatch.setattr(db_module, "db_session", mock_db_session)
        monkeypatch.setattr(stages_module, "db_session", mock_db_session)

        captured = {}

        def mock_checker(stage_mgr, catalyst_results=None):
            captured["catalyst_results"] = catalyst_results
            mock_instance = MagicMock()
            mock_instance.run_confirmation.return_value = []
            return mock_instance

        monkeypatch.setattr("src.confirmation.ConfirmationChecker", mock_checker)

        from src.pipeline import run_phase2_confirmation
        run_phase2_confirmation()

        assert "catalyst_results" in captured
        cr = captured["catalyst_results"]["BTCUSDT"]
        assert cr.is_major_catalyst is True
        assert cr.is_negative_catalyst is True
        assert abs(cr.score - 0.95) < 0.001


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


class TestGetPriceChanges:
    """Tests for _get_price_changes zero-division guard."""

    def test_zero_close_returns_none_not_crash(self, monkeypatch):
        """Zero close price should return None for that window, not raise."""
        monkeypatch.setattr(
            "src.binance.get_klines",
            lambda sym, interval, limit, market: [
                {"c": 100.0}, {"c": 0.0}, {"c": 50.0}
            ],
        )
        from src.pipeline import _get_price_changes
        result = _get_price_changes("BTCUSDT")
        assert result["1h"] is None
        assert result["4h"] is None
        assert result["24h"] is None

    def test_normal_close_returns_values(self, monkeypatch):
        """Normal close prices should return computed percentages."""
        # Build 25 candles with close = 100, then 101 (1% change)
        candles = [{"c": 100.0} for _ in range(24)] + [{"c": 101.0}]
        monkeypatch.setattr(
            "src.binance.get_klines",
            lambda sym, interval, limit, market: candles,
        )
        from src.pipeline import _get_price_changes
        result = _get_price_changes("BTCUSDT")
        assert result["1h"] == 1.0
        assert result["4h"] == 1.0
        assert result["24h"] == 1.0

    def test_missing_candles_returns_none(self, monkeypatch):
        """Insufficient candles should return all None."""
        monkeypatch.setattr(
            "src.binance.get_klines",
            lambda sym, interval, limit, market: [{"c": 100.0}],
        )
        from src.pipeline import _get_price_changes
        result = _get_price_changes("BTCUSDT")
        assert result["1h"] is None
        assert result["4h"] is None
        assert result["24h"] is None

    def test_api_exception_returns_none(self, monkeypatch):
        """API exception should return all None."""
        def _raise(*args, **kwargs):
            raise Exception("API error")
        monkeypatch.setattr(
            "src.binance.get_klines",
            _raise,
        )
        from src.pipeline import _get_price_changes
        result = _get_price_changes("BTCUSDT")
        assert result["1h"] is None
        assert result["4h"] is None
        assert result["24h"] is None
