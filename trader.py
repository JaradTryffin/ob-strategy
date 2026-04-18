from __future__ import annotations
# ═══════════════════════════════════════════════════════════════
#  TRADER — Bybit order management via pybit
# ═══════════════════════════════════════════════════════════════

import math
import logging
from pybit.unified_trading import HTTP
from config import (SYMBOL, LEVERAGE, BREAKEVEN_AT_1R,
                    TRAILING_AFTER_BE, TRAILING_ATR_MULT)
from data import get_http_client
from logger import log_message

logger = logging.getLogger(__name__)


def _round_qty(qty: float, step: float) -> float:
    """Round qty down to nearest qtyStep."""
    decimals = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return round(math.floor(qty / step) * step, decimals)


def get_qty_step(client: HTTP) -> float:
    resp  = client.get_instruments_info(category="linear", symbol=SYMBOL)
    info  = resp["result"]["list"][0]["lotSizeFilter"]
    return float(info["qtyStep"])


def set_leverage() -> None:
    client = get_http_client()
    try:
        client.set_leverage(
            category     = "linear",
            symbol       = SYMBOL,
            buyLeverage  = str(LEVERAGE),
            sellLeverage = str(LEVERAGE),
        )
        log_message(f"Leverage set to {LEVERAGE}x for {SYMBOL}")
    except Exception as e:
        log_message(f"[WARN] Could not set leverage: {e}")


def place_limit_order(direction: str, quantity: float,
                      entry: float, sl: float, tp: float) -> str | None:
    """
    Place a GTC limit order with SL and TP attached in a single API call.
    SL/TP are native Bybit levels — they survive a bot crash.
    Returns orderId string or None on failure.
    """
    client   = get_http_client()
    step     = get_qty_step(client)
    qty      = _round_qty(quantity, step)
    side     = "Buy" if direction == "long" else "Sell"

    try:
        resp = client.place_order(
            category    = "linear",
            symbol      = SYMBOL,
            side        = side,
            orderType   = "Limit",
            qty         = str(qty),
            price       = str(round(entry, 2)),
            stopLoss    = str(round(sl, 2)),
            takeProfit  = str(round(tp, 2)),
            timeInForce = "GTC",
            slTriggerBy = "LastPrice",
            tpTriggerBy = "LastPrice",
        )
        order_id = resp["result"]["orderId"]
        log_message(f"[PENDING] {direction.upper()} limit @ {entry:.2f} | "
                    f"SL: {sl:.2f} | TP: {tp:.2f} | Qty: {qty} | OrderId: {order_id}")
        return order_id
    except Exception as e:
        log_message(f"[ERROR] Failed to place limit order: {e}")
        return None


def cancel_order(order_id: str) -> None:
    """Cancel a single order by ID."""
    client = get_http_client()
    try:
        client.cancel_order(category="linear", symbol=SYMBOL, orderId=order_id)
        log_message(f"[CANCEL] Order {order_id} cancelled")
    except Exception as e:
        log_message(f"[WARN] Could not cancel order {order_id}: {e}")


def cancel_all_orders() -> None:
    """Cancel all open orders for SYMBOL."""
    client = get_http_client()
    try:
        client.cancel_all_orders(category="linear", symbol=SYMBOL)
        log_message("[CANCEL] All open orders cancelled")
    except Exception as e:
        log_message(f"[WARN] Could not cancel all orders: {e}")


def get_open_orders() -> list[dict]:
    """Return all open orders for SYMBOL."""
    client = get_http_client()
    try:
        resp = client.get_open_orders(category="linear", symbol=SYMBOL)
        return resp["result"]["list"]
    except Exception as e:
        log_message(f"[WARN] Could not fetch open orders: {e}")
        return []


def update_sl(new_sl: float, position: dict) -> None:
    """Move SL to a new level using Bybit's native trading-stop endpoint."""
    client = get_http_client()
    try:
        client.set_trading_stop(
            category    = "linear",
            symbol      = SYMBOL,
            stopLoss    = str(round(new_sl, 2)),
            tpslMode    = "Full",
            slTriggerBy = "LastPrice",
            positionIdx = 0,
        )
        log_message(f"[SL UPDATE] New SL: {new_sl:.2f}")
    except Exception as e:
        log_message(f"[ERROR] Failed to update SL: {e}")


def manage_position(position: dict, current_price: float,
                    current_atr: float,
                    bar_high: float | None = None,
                    bar_low:  float | None = None) -> dict:
    """
    Check breakeven and trailing stop conditions.
    Uses Bybit's set_trading_stop to move SL natively.
    Returns updated position dict.
    """
    if position is None:
        return None

    direction = position["dir"]
    entry     = position["entry"]
    sl_dist   = position["sl_dist"]
    check_high = bar_high if bar_high is not None else current_price
    check_low  = bar_low  if bar_low  is not None else current_price

    if direction == "long":
        if BREAKEVEN_AT_1R and not position["be"]:
            if check_high >= entry + sl_dist:
                new_sl = entry + 0.5
                update_sl(new_sl, position)
                position["sl"] = new_sl
                position["be"] = True
                log_message(f"[BE] Stop moved to breakeven: {new_sl:.2f}")

        if TRAILING_AFTER_BE and position["be"]:
            new_sl = current_price - current_atr * TRAILING_ATR_MULT
            if new_sl > position["sl"]:
                update_sl(new_sl, position)
                position["sl"] = new_sl
                log_message(f"[TRAIL] Stop trailed to: {new_sl:.2f}")

    else:  # short
        if BREAKEVEN_AT_1R and not position["be"]:
            if check_low <= entry - sl_dist:
                new_sl = entry - 0.5
                update_sl(new_sl, position)
                position["sl"] = new_sl
                position["be"] = True
                log_message(f"[BE] Stop moved to breakeven: {new_sl:.2f}")

        if TRAILING_AFTER_BE and position["be"]:
            new_sl = current_price + current_atr * TRAILING_ATR_MULT
            if new_sl < position["sl"]:
                update_sl(new_sl, position)
                position["sl"] = new_sl
                log_message(f"[TRAIL] Stop trailed to: {new_sl:.2f}")

    return position
