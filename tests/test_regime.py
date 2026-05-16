"""Unit tests for src/regime.py market regime detection."""

import os
os.environ["TELEGRAM_BOT_TOKEN"] = "test:token"
os.environ["TELEGRAM_CHAT_ID"] = "12345"

from src.regime import MarketRegime, is_suppressed, detect_regime


def make_ticker(symbol, quote_volume, high=100, low=90, last=95,
                price_change_pct=0):
    return {
        "symbol": symbol,
        "quoteVolume": str(quote_volume),
        "highPrice": str(high),
        "lowPrice": str(low),
        "lastPrice": str(last),
        "priceChangePercent": str(price_change_pct),
        "count": "1000",
    }


class TestMarketRegime:
    def test_favorable_not_suppressed(self):
        assert not is_suppressed(MarketRegime.FAVORABLE)

    def test_neutral_not_suppressed(self):
        assert not is_suppressed(MarketRegime.NEUTRAL)

    def test_unfavorable_suppressed(self):
        assert is_suppressed(MarketRegime.UNFAVORABLE)

    def test_enum_values(self):
        assert MarketRegime.FAVORABLE.value == "favorable"
        assert MarketRegime.NEUTRAL.value == "neutral"
        assert MarketRegime.UNFAVORABLE.value == "unfavorable"


class TestDetectRegime:
    """Tests for detect_regime() with mocked ticker data.
    Note: detect_regime() calls get_24h_tickers from inside its body
    via 'from src.binance import get_24h_tickers', so we must patch
    src.binance.get_24h_tickers.
    """

    def _raising(self, msg="error"):
        def _fn(*args, **kwargs):
            raise Exception(msg)
        return _fn

    def test_favorable_regime(self, monkeypatch):
        """Low vol, no BTC dump -> no factors -> FAVORABLE."""
        tickers = [
            make_ticker("BTCUSDT", 500_000, high=100, low=96, last=98,
                        price_change_pct=-1),
            make_ticker("ETHUSDT", 300_000, high=100, low=96, last=98),
            make_ticker("SOLUSDT", 200_000, high=50, low=48, last=49),
        ]
        monkeypatch.setattr("src.binance.get_24h_tickers", lambda: tickers)
        assert detect_regime() == MarketRegime.FAVORABLE

    def test_neutral_by_high_volatility(self, monkeypatch):
        """Avg range > 80% -> one unfavorable factor -> NEUTRAL."""
        tickers = [
            make_ticker("BTCUSDT", 500_000, high=200, low=100, last=101,
                        price_change_pct=-1),
            make_ticker("ETHUSDT", 300_000, high=200, low=100, last=101),
            make_ticker("SOLUSDT", 200_000, high=200, low=100, last=101),
        ]
        monkeypatch.setattr("src.binance.get_24h_tickers", lambda: tickers)
        # ranges: (200-100)/101*100 = 99% each, avg = 99% > 80 => 1 factor
        assert detect_regime() == MarketRegime.NEUTRAL

    def test_neutral_by_btc_dump(self, monkeypatch):
        """BTC dumping > 5% -> one unfavorable factor -> NEUTRAL."""
        tickers = [
            make_ticker("BTCUSDT", 500_000, high=100, low=96, last=98,
                        price_change_pct=-6),
            make_ticker("ETHUSDT", 300_000, high=100, low=96, last=98),
            make_ticker("SOLUSDT", 200_000, high=50, low=48, last=49),
        ]
        monkeypatch.setattr("src.binance.get_24h_tickers", lambda: tickers)
        # BTC dump = -6% < -5 => 1 factor => NEUTRAL
        assert detect_regime() == MarketRegime.NEUTRAL

    def test_two_factors_unfavorable(self, monkeypatch):
        """High vol AND BTC dump -> UNFAVORABLE."""
        tickers = [
            make_ticker("BTCUSDT", 500_000, high=200, low=100, last=101,
                        price_change_pct=-6),
            make_ticker("ETHUSDT", 300_000, high=200, low=100, last=101),
            make_ticker("SOLUSDT", 200_000, high=200, low=100, last=101),
        ]
        monkeypatch.setattr("src.binance.get_24h_tickers", lambda: tickers)
        # ranges: 99% avg > 80 => factor
        # BTC dump = -6% < -5 => factor
        # 2 factors => UNFAVORABLE
        assert detect_regime() == MarketRegime.UNFAVORABLE

    def test_empty_tickers_returns_neutral(self, monkeypatch):
        """Empty tickers list should return NEUTRAL."""
        monkeypatch.setattr("src.binance.get_24h_tickers", lambda: [])
        assert detect_regime() == MarketRegime.NEUTRAL

    def test_no_btc_ticker_still_works(self, monkeypatch):
        """No BTC ticker means BTC factors skipped, vol check still runs."""
        tickers = [
            make_ticker("ETHUSDT", 500_000, high=100, low=96, last=98),
            make_ticker("SOLUSDT", 300_000, high=50, low=48, last=49),
        ]
        monkeypatch.setattr("src.binance.get_24h_tickers", lambda: tickers)
        # No BTC ticker, low vol, so FAVORABLE (0 factors)
        assert detect_regime() == MarketRegime.FAVORABLE

    def test_api_exception_returns_neutral(self, monkeypatch):
        """API exception should return NEUTRAL."""
        monkeypatch.setattr("src.binance.get_24h_tickers",
                            self._raising("API error"))
        assert detect_regime() == MarketRegime.NEUTRAL

    def test_no_top_tickers_returns_neutral(self, monkeypatch):
        """When all tickers have zero volume, return NEUTRAL."""
        tickers = [
            make_ticker("BTCUSDT", 0),
        ]
        monkeypatch.setattr("src.binance.get_24h_tickers", lambda: tickers)
        assert detect_regime() == MarketRegime.NEUTRAL
