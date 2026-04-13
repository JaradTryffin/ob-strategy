# ═══════════════════════════════════════════════════════════════
#  DATA — Fetch 1H candles from Binance Demo Futures
# ═══════════════════════════════════════════════════════════════

from __future__ import annotations
import time
import pandas as pd
import numpy as np
from binance.um_futures import UMFutures
from config import API_KEY, API_SECRET, BASE_URL, SYMBOL, TIMEFRAME, CANDLES_NEEDED


def get_client():
    return UMFutures(key=API_KEY, secret=API_SECRET, base_url=BASE_URL)


def fetch_candles(client=None) -> pd.DataFrame:
    """Fetch the latest CANDLES_NEEDED 1H candles from Binance and return as DataFrame."""
    if client is None:
        client = get_client()

    raw = client.klines(SYMBOL, TIMEFRAME, limit=CANDLES_NEEDED)

    df = pd.DataFrame(raw, columns=[
        'open_time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_volume', 'trades', 'taker_buy_base',
        'taker_buy_quote', 'ignore'
    ])

    df['open_time']  = pd.to_datetime(df['open_time'], unit='ms')
    df['close_time'] = pd.to_numeric(df['close_time'])
    df.set_index('open_time', inplace=True)
    df.index.name = 'datetime'

    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df[col])

    # Drop the last candle only if it is still forming.
    # Checking close_time avoids the boundary bug where the new candle hasn't
    # appeared in the API response yet and we accidentally drop the last
    # closed candle instead.
    now_ms = int(time.time() * 1000)
    if df['close_time'].iloc[-1] > now_ms:
        df = df.iloc[:-1]

    return df[['open', 'high', 'low', 'close', 'volume']]


def get_account_balance(client=None) -> float:
    """Return current USDT balance from futures account."""
    if client is None:
        client = get_client()
    balances = client.balance()
    for b in balances:
        if b['asset'] == 'USDT':
            return float(b['availableBalance'])
    return 0.0


def get_open_position(client=None) -> dict | None:
    """Return open BTCUSDT position or None if flat."""
    if client is None:
        client = get_client()
    positions = client.get_position_risk(symbol=SYMBOL)
    for p in positions:
        if p['symbol'] == SYMBOL and float(p['positionAmt']) != 0:
            return {
                'side'         : 'long' if float(p['positionAmt']) > 0 else 'short',
                'size'         : abs(float(p['positionAmt'])),
                'entry_price'  : float(p['entryPrice']),
                'unrealized_pnl': float(p['unRealizedProfit']),
            }
    return None
