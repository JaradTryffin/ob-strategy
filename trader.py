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


def place_limit_order(client: UMFutures, direction: str, quantity: float,
                      entry: float, sl: float, tp: float):
    """
    Place a pending limit order at the OB level.
    Returns the Binance orderId (int) or None on failure.
    """
    side = 'BUY' if direction == 'long' else 'SELL'
    try:
        order = client.new_order(
            symbol      = SYMBOL,
            side        = side,
            type        = 'LIMIT',
            price       = round(entry, 2),
            quantity    = quantity,
            timeInForce = 'GTC',
        )
        log_message(f"[PENDING] {direction.upper()} limit @ {entry:.2f} | "
                    f"SL: {sl:.2f} | TP: {tp:.2f} | Qty: {quantity} | "
                    f"OrderId: {order['orderId']}")
        return order['orderId']
    except ClientError as e:
        log_message(f"[ERROR] Failed to place limit order: {e}")
        return None


def attach_sl_tp(client: UMFutures, direction: str, sl: float, tp: float):
    """
    Place SL and TP orders after a limit entry has been filled.
    """
    close_side = 'SELL' if direction == 'long' else 'BUY'
    try:
        client.new_order(
            symbol        = SYMBOL,
            side          = close_side,
            type          = 'STOP_MARKET',
            stopPrice     = round(sl, 2),
            closePosition = 'true',
        )
        client.new_order(
            symbol        = SYMBOL,
            side          = close_side,
            type          = 'TAKE_PROFIT_MARKET',
            stopPrice     = round(tp, 2),
            closePosition = 'true',
        )
        log_message(f"[SL/TP] Attached — SL: {sl:.2f} | TP: {tp:.2f}")
    except ClientError as e:
        log_message(f"[ERROR] Failed to attach SL/TP: {e}")


def cancel_order(client: UMFutures, order_id: int):
    """Cancel a single order by ID."""
    try:
        client.cancel_order(symbol=SYMBOL, orderId=order_id)
        log_message(f"[CANCEL] Order {order_id} cancelled")
    except ClientError as e:
        log_message(f"[WARN] Could not cancel order {order_id}: {e}")


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
