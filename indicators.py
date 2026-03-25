# ═══════════════════════════════════════════════════════════════
#  INDICATORS — Exact logic from the backtest
# ═══════════════════════════════════════════════════════════════

import pandas as pd
import numpy as np
from config import ATR_LEN, HTF_EMA, SESSION_START, SESSION_END, SWING_LOOKBACK


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c, h, l, o = df['close'], df['high'], df['low'], df['open']

    # ATR
    tr        = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    df['atr'] = tr.rolling(ATR_LEN).mean()

    # HTF bias EMA
    df['htf_ema']   = c.ewm(span=HTF_EMA, adjust=False).mean()
    df['bull_bias'] = c > df['htf_ema']
    df['bear_bias'] = c < df['htf_ema']

    # Candle properties
    df['body']     = (c - o).abs()
    df['bull_bar'] = c > o
    df['bear_bar'] = c < o

    # Session filter (UTC hours)
    df['hour_utc']   = df.index.hour
    df['in_session'] = ((df['hour_utc'] >= SESSION_START) &
                        (df['hour_utc'] <  SESSION_END))

    # Swing highs/lows — confirmed n bars after they form
    n  = SWING_LOOKBACK
    sh = pd.Series(False, index=df.index)
    sl = pd.Series(False, index=df.index)
    for i in range(n, len(df) - n):
        if h.iloc[i] == h.iloc[i - n: i + n + 1].max():
            sh.iloc[i] = True
        if l.iloc[i] == l.iloc[i - n: i + n + 1].min():
            sl.iloc[i] = True
    df['is_swing_high'] = sh
    df['is_swing_low']  = sl

    return df.dropna(subset=['atr', 'htf_ema'])
