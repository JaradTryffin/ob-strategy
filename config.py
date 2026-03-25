# ═══════════════════════════════════════════════════════════════
#  OB TRADING BOT — CONFIG
#  Strategy: Structure + Order Block (Smart Money)
#  Instrument: BTCUSDT Perpetual Futures (Binance Demo)
# ═══════════════════════════════════════════════════════════════

# ── Binance Demo API ──────────────────────────────────────────
import os
from dotenv import load_dotenv
load_dotenv()

API_KEY    = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
BASE_URL   = "https://demo-fapi.binance.com"        # Binance demo futures endpoint

# ── Instrument ────────────────────────────────────────────────
SYMBOL     = "BTCUSDT"
TIMEFRAME  = "1h"
LEVERAGE   = 1              # keep at 1x for now — risk is managed via position sizing

# ── Strategy — Structure ──────────────────────────────────────
SWING_LOOKBACK    = 5       # bars each side to confirm a swing high/low
OB_MAX_AGE        = 80      # bars before an unvisited OB expires
OB_LOOKBACK       = 25      # bars back from swing to search for OB candle
MIN_OB_BODY_MULT  = 0.2     # OB candle body must be > ATR * this

# ── Strategy — Entry ──────────────────────────────────────────
HTF_EMA           = 50      # EMA used as higher-timeframe bias filter
ATR_LEN           = 14
RR_RATIO          = 3.0     # 1:3 risk/reward
SL_BUFFER_MULT    = 0.15    # ATR fraction added beyond OB wick for SL

# ── Session (UTC) ─────────────────────────────────────────────
# Matches BTC backtest: 9:00-16:00 EST = 14:00-21:00 UTC
SESSION_START     = 14      # 14:00 UTC (09:00 EST — NYSE open)
SESSION_END       = 21      # 21:00 UTC (16:00 EST — NYSE close)

# ── Risk Management ───────────────────────────────────────────
INITIAL_CAPITAL   = 10_000  # your demo account starting balance (update if different)
RISK_PCT          = 0.75    # % of account risked per trade (matched to backtest)
MAX_TRADES_DAY    = 2       # max entries per day
BREAKEVEN_AT_1R   = True    # move SL to breakeven after 1R profit
TRAILING_AFTER_BE = True    # trail stop after breakeven
TRAILING_ATR_MULT = 1.5     # ATR multiplier for trailing stop

# ── FTMO Daily Loss Guard ─────────────────────────────────────
# Bot will stop trading for the day if daily loss exceeds this
DAILY_LOSS_LIMIT_PCT = 3.0  # 3% self-imposed (FTMO limit is 5% — buffer for safety)

# ── Candle History ────────────────────────────────────────────
CANDLES_NEEDED    = 300     # how many 1H candles to fetch for indicator calculation
