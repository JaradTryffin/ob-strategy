"""
╔══════════════════════════════════════════════════════════════╗
║   OB TRADING BOT — BTCUSDT Perpetual Futures                ║
║   Strategy: Structure + Order Block (Smart Money)           ║
║   Exchange: Bybit (Demo / Live)                             ║
╚══════════════════════════════════════════════════════════════╝

HOW TO RUN:
    python3 main.py

The bot will:
1. Backfill 300 closed 1H candles via REST
2. Warm up the OB engine with historical data
3. Place pending limit orders at each active OB zone (SL+TP attached)
4. Subscribe to Bybit WebSocket — fires on every confirmed candle close
5. On each candle close:
   - Detect new OBs → place limit orders immediately
   - Detect expired/mitigated OBs → cancel their limits
   - Detect filled limits → manage position (BE / trailing)
   - Manage open position BE and trailing stop
"""

import time
import logging
import threading
from datetime import datetime, timezone

import pandas as pd

from data       import fetch_candles, bars_to_df, get_account_balance, get_open_position, KlineFeed
from indicators import add_indicators
from ob_engine  import OBEngine
from risk       import RiskManager
from trader     import (set_leverage, place_limit_order, cancel_order,
                        cancel_all_orders, get_open_orders,
                        update_sl, manage_position)
from logger     import log_message, log_trade
from config     import (MAX_TRADES_DAY, SL_BUFFER_MULT, RR_RATIO)

logging.basicConfig(level=logging.WARNING)


# ── Helpers ───────────────────────────────────────────────────────────────────

def build_ob_params(ob: dict, atr: float, balance: float, risk_mgr) -> dict:
    """Derive entry / sl / tp / quantity from an OB dict."""
    direction = "long" if ob["dir"] == "bull" else "short"
    entry     = ob["ob_high"] if direction == "long" else ob["ob_low"]
    sl_buffer = atr * SL_BUFFER_MULT
    sl        = (ob["wick_low"]  - sl_buffer if direction == "long"
                 else ob["wick_high"] + sl_buffer)
    sl_dist   = abs(entry - sl)
    tp        = (entry + sl_dist * RR_RATIO if direction == "long"
                 else entry - sl_dist * RR_RATIO)
    quantity  = risk_mgr.calc_quantity(balance, entry, sl)
    return dict(direction=direction, entry=entry, sl=sl, tp=tp,
                sl_dist=sl_dist, quantity=quantity)


def place_pending(ob: dict, atr: float, balance: float, risk_mgr,
                  existing_order_ids: set, pending_orders: dict,
                  current_price: float) -> bool:
    """
    Place a limit order for an OB if not already live and price hasn't
    already blown through the entry level.
    """
    if ob.get("order_id") and ob["order_id"] in existing_order_ids:
        return False

    p = build_ob_params(ob, atr, balance, risk_mgr)

    if p["sl_dist"] < 10:
        log_message(f"  [SKIP OB] SL distance too tight (${p['sl_dist']:.2f})")
        return False

    # Proximity check
    if p["direction"] == "long" and current_price < p["entry"]:
        log_message(f"  [SKIP OB] Price ${current_price:.2f} already below "
                    f"LONG entry ${p['entry']:.2f}")
        return False
    if p["direction"] == "short" and current_price > p["entry"]:
        log_message(f"  [SKIP OB] Price ${current_price:.2f} already above "
                    f"SHORT entry ${p['entry']:.2f}")
        return False

    order_id = place_limit_order(p["direction"], p["quantity"],
                                 p["entry"], p["sl"], p["tp"])
    if order_id:
        ob["order_id"]           = order_id
        pending_orders[order_id] = p
        return True
    return False


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    log_message("═" * 55)
    log_message("  OB BOT STARTING — BTCUSDT Futures (Bybit)")
    log_message("═" * 55)

    set_leverage()

    # ── Backfill + warmup ─────────────────────────────────────
    log_message("  Backfilling candles and warming up OB engine...")
    df      = fetch_candles()
    df      = add_indicators(df)
    ob_eng  = OBEngine()
    ob_eng.warmup(df)
    log_message(f"  Warm-up complete — {len(ob_eng.active_obs)} active OBs loaded")

    risk_mgr       = RiskManager()
    pending_orders: dict = {}   # {order_id: params} — survives OB removal
    local_position = None

    # ── Orphan position recovery ──────────────────────────────
    startup_pos = get_open_position()
    if startup_pos is not None:
        direction = startup_pos["side"]
        entry     = startup_pos["entry_price"]
        quantity  = startup_pos["size"]
        sl        = startup_pos.get("sl") or 0.0
        tp        = startup_pos.get("tp") or 0.0
        log_message(f"  [RECOVER] Orphaned {direction.upper()} position @ {entry:.2f} "
                    f"SL: {sl:.2f} | TP: {tp:.2f}")
        local_position = {
            "dir"       : direction,
            "entry"     : entry,
            "sl"        : sl,
            "tp"        : tp,
            "sl_dist"   : abs(entry - sl) if sl else df.iloc[-1]["atr"] * 1.5,
            "quantity"  : quantity,
            "be"        : False,
            "entry_time": "",
        }

    # ── Place limits for warmup OBs ───────────────────────────
    balance            = get_account_balance()
    existing_order_ids = {o["orderId"] for o in get_open_orders()}
    last               = df.iloc[-1]

    if ob_eng.active_obs and local_position is None \
            and risk_mgr.can_trade(balance, MAX_TRADES_DAY):
        log_message(f"  Placing limits for {len(ob_eng.active_obs)} warmup OBs...")
        for ob in ob_eng.active_obs:
            place_pending(ob, last["atr"], balance, risk_mgr,
                          existing_order_ids, pending_orders, float(last["close"]))

    # ── Rolling bar list (maintained for indicators + OB engine) ─
    # Keep a list of bar dicts (oldest-first); rebuilt into DataFrame each candle
    bar_list: list[dict] = []
    for ts, row in df.iterrows():
        bar_list.append({
            "timestamp": int(ts.timestamp() * 1000),
            "open":  row["open"],  "high": row["high"],
            "low":   row["low"],   "close": row["close"],
            "volume": row["volume"],
        })

    # ── Per-candle logic (called by WebSocket callback) ───────
    def on_candle_close(bar: dict) -> None:
        nonlocal df, local_position

        now_utc = datetime.now(timezone.utc)

        # Append new bar and rebuild DataFrame
        bar_list.append(bar)
        if len(bar_list) > 400:
            bar_list.pop(0)
        df_new = bars_to_df(bar_list)
        df_new = add_indicators(df_new)
        df     = df_new

        last     = df.iloc[-1]
        price    = float(last["close"])
        atr      = float(last["atr"])
        bar_high = float(last["high"])
        bar_low  = float(last["low"])
        in_sess  = bool(last["in_session"])

        log_message(f"\n── Candle Close @ {now_utc.strftime('%Y-%m-%d %H:%M')} UTC ──")
        log_message(f"  BTC: ${price:,.2f} | ATR: ${atr:.2f} | "
                    f"{'IN SESSION' if in_sess else 'OUT OF SESSION'}")

        # ── Fetch current Binance state ───────────────────────
        binance_pos        = get_open_position()
        open_orders        = get_open_orders()
        existing_order_ids = {o["orderId"] for o in open_orders}
        balance            = get_account_balance()
        log_message(f"  Balance: ${balance:,.2f} | Daily P&L: ${risk_mgr.daily_pnl:.2f}")

        # ── Sync local position ───────────────────────────────
        if binance_pos is None and local_position is not None:
            pnl = (price - local_position["entry"]) if local_position["dir"] == "long" \
                  else (local_position["entry"] - price)
            log_trade({
                "entry_time"  : local_position.get("entry_time", ""),
                "exit_time"   : now_utc.isoformat(),
                "direction"   : local_position["dir"],
                "entry_price" : local_position["entry"],
                "exit_price"  : price,
                "sl"          : local_position["sl"],
                "tp"          : local_position["tp"],
                "quantity"    : local_position["quantity"],
                "pnl_usd"     : pnl * local_position["quantity"],
                "reason"      : "SL/TP (exchange)",
                "be_moved"    : local_position["be"],
            })
            risk_mgr.record_trade(pnl * local_position["quantity"])
            local_position = None

        # ── Manage open position ──────────────────────────────
        if local_position is not None and binance_pos is not None:
            local_position = manage_position(
                local_position, price, atr, bar_high, bar_low)

        # ── Update OB engine ──────────────────────────────────
        obs_before    = {id(ob): ob for ob in ob_eng.active_obs}
        _, new_obs    = ob_eng.update(df_new)
        obs_after_ids = {id(ob) for ob in ob_eng.active_obs}
        removed_obs   = [ob for oid, ob in obs_before.items()
                         if oid not in obs_after_ids]

        # ── Place limits for new OBs ──────────────────────────
        if new_obs and local_position is None \
                and risk_mgr.can_trade(balance, MAX_TRADES_DAY):
            for ob in new_obs:
                log_message(f"  [NEW OB] {ob['dir'].upper()} OB | "
                            f"Zone: {ob['ob_low']:.2f} – {ob['ob_high']:.2f}")
                place_pending(ob, atr, balance, risk_mgr,
                              existing_order_ids, pending_orders, price)
            existing_order_ids = {o["orderId"] for o in get_open_orders()}

        # ── Detect filled limit ───────────────────────────────
        if local_position is None and binance_pos is not None:
            filled_order_id = None
            filled_params   = None
            for order_id, params in list(pending_orders.items()):
                if order_id not in existing_order_ids:
                    filled_order_id = order_id
                    filled_params   = params
                    break

            if filled_params is not None:
                direction = filled_params["direction"]
                entry     = float(binance_pos["entry_price"])
                quantity  = float(binance_pos["size"])
                sl        = filled_params["sl"]
                tp        = filled_params["tp"]

                # Validate SL is on correct side of actual fill
                sl_valid = (sl < entry if direction == "long" else sl > entry)
                if not sl_valid:
                    sl_dist = atr * 1.5
                    sl = (entry - sl_dist if direction == "long"
                          else entry + sl_dist)
                    tp = (entry + sl_dist * RR_RATIO if direction == "long"
                          else entry - sl_dist * RR_RATIO)
                    log_message(f"  [WARN] SL wrong side — recalculated: "
                                f"SL {sl:.2f} | TP {tp:.2f}")
                    # Update Bybit native SL/TP
                    update_sl(sl, {"dir": direction})

                log_message(f"  [FILLED] {direction.upper()} @ {entry:.2f} | "
                            f"SL: {sl:.2f} | TP: {tp:.2f}")

                # Cancel all other pending limits
                for other_id in list(pending_orders.keys()):
                    if other_id != filled_order_id \
                            and other_id in existing_order_ids:
                        cancel_order(other_id)
                pending_orders.clear()

                if in_sess:
                    risk_mgr.trades_today += 1

                local_position = {
                    "dir"       : direction,
                    "entry"     : entry,
                    "sl"        : sl,
                    "tp"        : tp,
                    "sl_dist"   : abs(entry - sl),
                    "quantity"  : quantity,
                    "be"        : False,
                    "entry_time": now_utc.isoformat(),
                }

        # ── Cancel limits for removed OBs ─────────────────────
        for ob in removed_obs:
            oid = ob.get("order_id")
            if oid:
                if oid in existing_order_ids:
                    cancel_order(oid)
                pending_orders.pop(oid, None)

    # ── Start WebSocket feed ──────────────────────────────────
    feed = KlineFeed()
    feed.start(on_candle_close)
    log_message("  WebSocket live — waiting for candle closes...")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log_message("\n  Bot stopped by user.")
        feed.stop()
        cancel_all_orders()


if __name__ == "__main__":
    run()
