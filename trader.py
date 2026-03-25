# ═══════════════════════════════════════════════════════════════
#  TRADER — Place orders and manage open positions
# ═══════════════════════════════════════════════════════════════

from binance.um_futures import UMFutures
from binance.error import ClientError
from config import SYMBOL, LEVERAGE, BREAKEVEN_AT_1R, TRAILING_AFTER_BE, TRAILING_ATR_MULT
from logger import log_trade, log_message
import math


def set_leverage(client: UMFutures):
    try:
        client.change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        log_message(f"Leverage set to {LEVERAGE}x for {SYMBOL}")
    except ClientError as e:
        log_message(f"[WARN] Could not set leverage: {e}")


def open_position(client: UMFutures, direction: str, quantity: float,
                  entry: float, sl: float, tp: float) -> dict | None:
    """
    Open a market order and attach SL + TP as separate orders.
    direction: 'long' or 'short'
    """
    side       = 'BUY'  if direction == 'long'  else 'SELL'
    close_side = 'SELL' if direction == 'long'  else 'BUY'

    try:
        # ── Market entry ─────────────────────────────────────
        order = client.new_order(
            symbol   = SYMBOL,
            side     = side,
            type     = 'MARKET',
            quantity = quantity,
        )
        log_message(f"[ENTRY] {direction.upper()} {quantity} BTC @ ~{entry:.2f} | SL: {sl:.2f} | TP: {tp:.2f}")

        # ── Stop Loss ─────────────────────────────────────────
        client.new_order(
            symbol        = SYMBOL,
            side          = close_side,
            type          = 'STOP_MARKET',
            stopPrice     = round(sl, 2),
            closePosition = 'true',
        )

        # ── Take Profit ───────────────────────────────────────
        client.new_order(
            symbol        = SYMBOL,
            side          = close_side,
            type          = 'TAKE_PROFIT_MARKET',
            stopPrice     = round(tp, 2),
            closePosition = 'true',
        )

        return {
            'dir'      : direction,
            'entry'    : entry,
            'sl'       : sl,
            'tp'       : tp,
            'sl_dist'  : abs(entry - sl),
            'quantity' : quantity,
            'be'       : False,
        }

    except ClientError as e:
        log_message(f"[ERROR] Failed to open position: {e}")
        return None


def manage_position(client: UMFutures, position: dict, current_price: float,
                    current_atr: float, bar_high: float = None, bar_low: float = None) -> dict:
    """
    Check breakeven and trailing stop conditions.
    Cancels existing SL order and replaces with updated level when needed.
    Returns updated position dict.
    """
    if position is None:
        return None

    direction = position['dir']
    entry     = position['entry']
    sl_dist   = position['sl_dist']

    # Use bar high/low for BE trigger (matches backtest) — fall back to close if not provided
    check_high = bar_high if bar_high is not None else current_price
    check_low  = bar_low  if bar_low  is not None else current_price

    if direction == 'long':
        # ── Breakeven ─────────────────────────────────────────
        if BREAKEVEN_AT_1R and not position['be']:
            if check_high >= entry + sl_dist:
                new_sl = entry + 0.5
                _replace_sl(client, direction, new_sl, position)
                position['sl'] = new_sl
                position['be'] = True
                log_message(f"[BE] Stop moved to breakeven: {new_sl:.2f}")

        # ── Trailing Stop ─────────────────────────────────────
        if TRAILING_AFTER_BE and position['be']:
            new_sl = current_price - current_atr * TRAILING_ATR_MULT
            if new_sl > position['sl']:
                _replace_sl(client, direction, new_sl, position)
                position['sl'] = new_sl
                log_message(f"[TRAIL] Stop trailed to: {new_sl:.2f}")

    else:  # short
        if BREAKEVEN_AT_1R and not position['be']:
            if check_low <= entry - sl_dist:
                new_sl = entry - 0.5
                _replace_sl(client, direction, new_sl, position)
                position['sl'] = new_sl
                position['be'] = True
                log_message(f"[BE] Stop moved to breakeven: {new_sl:.2f}")

        if TRAILING_AFTER_BE and position['be']:
            new_sl = current_price + current_atr * TRAILING_ATR_MULT
            if new_sl < position['sl']:
                _replace_sl(client, direction, new_sl, position)
                position['sl'] = new_sl
                log_message(f"[TRAIL] Stop trailed to: {new_sl:.2f}")

    return position


def _replace_sl(client: UMFutures, direction: str, new_sl: float, position: dict):
    """Cancel existing stop loss and place a new one."""
    close_side = 'SELL' if direction == 'long' else 'BUY'
    try:
        # Cancel all open stop orders for this symbol
        open_orders = client.get_orders(symbol=SYMBOL)
        for o in open_orders:
            if o['type'] == 'STOP_MARKET':
                client.cancel_order(symbol=SYMBOL, orderId=o['orderId'])

        # Place new SL
        client.new_order(
            symbol        = SYMBOL,
            side          = close_side,
            type          = 'STOP_MARKET',
            stopPrice     = round(new_sl, 2),
            closePosition = 'true',
        )
    except ClientError as e:
        log_message(f"[ERROR] Failed to replace SL: {e}")


def close_all_orders(client: UMFutures):
    """Cancel all open orders for the symbol."""
    try:
        client.cancel_open_orders(symbol=SYMBOL)
    except ClientError as e:
        log_message(f"[WARN] Could not cancel orders: {e}")
