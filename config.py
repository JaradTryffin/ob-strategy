# ═══════════════════════════════════════════════════════════════
#  OB TRADING BOT — CONFIG
#  Strategy: Structure + Order Block (Smart Money)
#  Instrument: BTCUSDT Perpetual Futures (Bybit)
# ═══════════════════════════════════════════════════════════════

import os
from dotenv import load_dotenv
load_dotenv()

# ── Bybit API ─────────────────────────────────────────────────
# DEMO=true  → api-demo.bybit.com (paper trading, real market data)
# DEMO=false → api.bybit.com      (live)
API_KEY    = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
DEMO: bool = os.getenv("DEMO", "true").lower() == "true"

# ── Instrument ────────────────────────────────────────────────
SYMBOL     = "BTCUSDT"
TIMEFRAME  = "60"          # Bybit interval in minutes (60 = 1H)
LEVERAGE   = 1

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
# 9:00-16:00 EST = 14:00-21:00 UTC
SESSION_START     = 14
SESSION_END       = 21

# ── Risk Management ───────────────────────────────────────────
INITIAL_CAPITAL   = 10_000
RISK_PCT          = 0.75    # % of account risked per trade
MAX_TRADES_DAY    = 2
BREAKEVEN_AT_1R   = True
TRAILING_AFTER_BE = True
TRAILING_ATR_MULT = 1.5

# ── Daily Loss Guard ──────────────────────────────────────────
DAILY_LOSS_LIMIT_PCT = 3.0

# ── Candle History ────────────────────────────────────────────
CANDLES_NEEDED    = 300     # 1H candles for indicator warmup
