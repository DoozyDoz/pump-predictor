import os
from dotenv import load_dotenv

load_dotenv()

COINGLASS_API_KEY = os.getenv("COINGLASS_API_KEY")
DUNE_API_KEY = os.getenv("DUNE_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "pump.db")

UNIVERSE_SIZE = 150
MIN_DAILY_VOLUME_USD = 1_000_000
UNIVERSE_REFRESH_DAY = "Monday"

PUMP_THRESHOLD_PCT = 15.0
PUMP_WINDOW_HOURS = 24
LOOKAHEAD_HOURS = 48

FUNDING_PERCENTILE = 2.0
FUNDING_CROSS_SECTIONAL_PCT = 5.0
FUNDING_HISTORY_DAYS = 90

OI_DIVERGENCE_LOOKBACK_DAYS = 7
OI_DIVERGENCE_HISTORY_DAYS = 30  # Binance OI history: ~1 month
OI_DIVERGENCE_PERCENTILE = 95  # top 5% of OI/price divergence
OI_DIVERGENCE_CROSS_SECTIONAL_PCT = 5.0
OI_PRICE_MAX_RISE_PCT = 5.0  # price must not have already pumped

LS_RATIO_HISTORY_DAYS = 30  # Binance LS ratio: ~30 days
LS_RATIO_PERCENTILE = 2.0  # bottom 2% = extreme bearish sentiment
LS_RATIO_CROSS_SECTIONAL_PCT = 5.0

TAKER_RATIO_HISTORY_MS = 21 * 86400_000  # 21 days in ms
TAKER_RATIO_PERCENTILE = 2.0  # bottom 2% = too many sellers = bullish
TAKER_RATIO_CROSS_SECTIONAL_PCT = 5.0

ORDER_BOOK_LEVELS = 10
ORDER_BOOK_CROSS_SECTIONAL_PCT = 5.0  # top 5% bid dominance = bullish
ORDER_BOOK_MIN_BID_DOM = 0.60   # absolute floor — bids must be 1.5x asks

# Legacy — now using OI divergence + LS ratio instead of on-chain
WALLET_GROWTH_PCT = 5.0
WALLET_MIN_BALANCE_USD = 1000
WALLET_WINDOW_HOURS = 48
CEX_OUTFLOW_STD = 2.0
CEX_RATIO_THRESHOLD = 2.0
CEX_WINDOW_DAYS = 30

ALERT_THRESHOLD = 2  # signals out of 5 (≥2 fires alert)

POSITION_SIZE_PCT = 0.10
MAX_CONCURRENT = 5
STOP_LOSS_PCT = -0.07
TAKE_PROFIT_1_PCT = 0.15
TAKE_PROFIT_1_PCT_SHARE = 0.50
TAKE_PROFIT_2_PCT = 0.25
TAKE_PROFIT_2_PCT_SHARE = 0.30
TRAILING_STOP_PCT = 0.03

GO_PRECISION = 0.50
GO_PROFIT_FACTOR = 1.5

BACKTEST_YEARS = 2
BACKTEST_TRAIN_MONTHS = 0   # no train window — fixed thresholds, no optimization
BACKTEST_TEST_MONTHS = 1    # 1 month test fits Binance 30d OI/LS window

# ---------------------------------------------------------------------------
# Stage thresholds (staged workflow)
# ---------------------------------------------------------------------------
WATCHLIST_THRESHOLD = 1       # signals needed for watchlist (lower = cast wider net)
CONFIRMATION_THRESHOLD = 2    # signals needed for confirmation stage
ENTRY_THRESHOLD = 3           # signals needed for full entry alert

# ---------------------------------------------------------------------------
# ATR-based risk parameters
# ---------------------------------------------------------------------------
ATR_PERIOD = 14
ATR_STOP_MULTIPLIER = 2.0
ATR_RISK_PER_TRADE_PCT = 0.01

# ---------------------------------------------------------------------------
# Market regime parameters
# ---------------------------------------------------------------------------
REGIME_ENABLED = True
REGIME_BTC_DOM_UPPER = 60.0   # BTC dominance above this = altcoin suppression
REGIME_VOLATILITY_UPPER = 80.0  # avg 24h range across top tokens above this = high vol

# ---------------------------------------------------------------------------
# Confirmation polling parameters
# ---------------------------------------------------------------------------
CONFIRMATION_POLL_MINUTES = 30
CONFIRMATION_PRICE_MOVE_PCT = 0.5   # minimum price bounce from recent low (%)
CONFIRMATION_VOLUME_SURGE_PCT = 50.0  # volume surge above 24h average (%)

# ---------------------------------------------------------------------------
# Data quality
# ---------------------------------------------------------------------------
STALE_DATA_HOURS = 4
MIN_DATA_POINTS = 10

# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------
LEGACY_IMMEDIATE_ALERTS = False  # set True to revert to old immediate-alert behavior

# ---------------------------------------------------------------------------
# Stage TTL
# ---------------------------------------------------------------------------
WATCHLIST_TTL_HOURS = 72
CONFIRMATION_TTL_HOURS = 24
