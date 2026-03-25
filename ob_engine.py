# ═══════════════════════════════════════════════════════════════
#  OB ENGINE — Detect BOS, build and track Order Blocks
#  Mirrors the backtest logic exactly
# ═══════════════════════════════════════════════════════════════

import pandas as pd
from config import SWING_LOOKBACK, OB_MAX_AGE, OB_LOOKBACK, MIN_OB_BODY_MULT


def find_ob_candle(df: pd.DataFrame, from_idx: int, ob_type: str, min_body: float) -> dict | None:
    """
    Search backwards from from_idx for last candle of ob_type
    ('bear' for bullish OB, 'bull' for bearish OB).
    """
    col  = 'bear_bar' if ob_type == 'bear' else 'bull_bar'
    stop = max(0, from_idx - OB_LOOKBACK)
    for j in range(from_idx, stop, -1):
        if df[col].iloc[j] and df['body'].iloc[j] >= min_body:
            ob_open  = df['open'].iloc[j]
            ob_close = df['close'].iloc[j]
            return {
                'ob_high'    : max(ob_open, ob_close),
                'ob_low'     : min(ob_open, ob_close),
                'wick_high'  : df['high'].iloc[j],
                'wick_low'   : df['low'].iloc[j],
                'formed_idx' : j,
                'formed_time': df.index[j],
            }
    return None


class OBEngine:
    """
    Stateful OB tracker. Call update() on each new closed candle.
    Returns any active OBs that price is currently touching.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.last_sh_price = None
        self.last_sh_idx   = -1
        self.last_sl_price = None
        self.last_sl_idx   = -1
        self.last_bull_bos = None
        self.last_bear_bos = None
        self.active_obs    = []

    def warmup(self, df: pd.DataFrame):
        """
        Replay all historical bars except the last one to build up
        swing/BOS/OB state before the live loop starts.
        Must be called once on startup after fetching history.
        """
        n = SWING_LOOKBACK
        for i in range(n * 2 + 10, len(df) - 1):
            self._process_bar(df, i)

    def update(self, df: pd.DataFrame) -> list[dict]:
        """
        Process the latest closed bar and return OBs being touched.
        Call once per candle close in the main loop.
        """
        n         = SWING_LOOKBACK
        last_i    = len(df) - 1
        row       = df.iloc[last_i]
        min_body  = row['atr'] * MIN_OB_BODY_MULT
        self._process_bar(df, last_i)
        # Return OBs touched by the latest bar
        triggered = []
        for ob in self.active_obs:
            if ob['dir'] == 'bull' and row['bull_bias']:
                if row['low'] <= ob['ob_high'] and row['close'] >= ob['ob_low']:
                    triggered.append(ob)
            elif ob['dir'] == 'bear' and row['bear_bias']:
                if row['high'] >= ob['ob_low'] and row['close'] <= ob['ob_high']:
                    triggered.append(ob)
        return triggered

    def _process_bar(self, df: pd.DataFrame, last_i: int):
        """Core per-bar logic — updates swings, BOS and OB list."""
        n         = SWING_LOOKBACK
        row       = df.iloc[last_i]
        min_body  = row['atr'] * MIN_OB_BODY_MULT

        # ── Update confirmed swing (confirmed n bars ago) ─────
        conf_i = last_i - n
        if conf_i >= 0:
            if df['is_swing_high'].iloc[conf_i]:
                new_sh = df['high'].iloc[conf_i]
                if self.last_sh_price is None or new_sh != self.last_sh_price:
                    self.last_sh_price = new_sh
                    self.last_sh_idx   = conf_i
                    self.last_bull_bos = None
            if df['is_swing_low'].iloc[conf_i]:
                new_sl = df['low'].iloc[conf_i]
                if self.last_sl_price is None or new_sl != self.last_sl_price:
                    self.last_sl_price = new_sl
                    self.last_sl_idx   = conf_i
                    self.last_bear_bos = None

        # ── Detect BOS and create OBs ─────────────────────────
        if (self.last_sh_price is not None
                and row['close'] > self.last_sh_price
                and self.last_bull_bos != self.last_sh_price):
            ob = find_ob_candle(df, self.last_sh_idx, 'bear', min_body)
            if ob is not None:
                ob.update({'dir': 'bull', 'age': 0, 'mitigated': False,
                            'bos_price': self.last_sh_price})
                self.active_obs.append(ob)
            self.last_bull_bos = self.last_sh_price

        if (self.last_sl_price is not None
                and row['close'] < self.last_sl_price
                and self.last_bear_bos != self.last_sl_price):
            ob = find_ob_candle(df, self.last_sl_idx, 'bull', min_body)
            if ob is not None:
                ob.update({'dir': 'bear', 'age': 0, 'mitigated': False,
                            'bos_price': self.last_sl_price})
                self.active_obs.append(ob)
            self.last_bear_bos = self.last_sl_price

        # ── Age OBs and check mitigation ─────────────────────
        for ob in self.active_obs:
            ob['age'] += 1
            if ob['dir'] == 'bull' and row['close'] < ob['ob_low']:
                ob['mitigated'] = True
            elif ob['dir'] == 'bear' and row['close'] > ob['ob_high']:
                ob['mitigated'] = True
        self.active_obs = [ob for ob in self.active_obs
                           if not ob['mitigated'] and ob['age'] < OB_MAX_AGE]

    def mark_mitigated(self, ob: dict):
        """Call after entering a trade on an OB — one entry per OB."""
        ob['mitigated'] = True
