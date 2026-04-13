"""
╔══════════════════════════════════════════════════════════════╗
║   OB TRADING BOT — BTCUSDT Perpetual Futures                ║
║   Strategy: Structure + Order Block (Smart Money)           ║
║   Exchange: Binance Demo Futures                            ║
╚══════════════════════════════════════════════════════════════╝

HOW TO RUN:
    python3 main.py

The bot will:
1. Wait for the current 1H candle to close
2. Fetch the latest 300 candles
3. Recalculate indicators + swing structure
4. Update the OB tracker
5. For each new OB: place a pending limit order at the OB level
6. Check if any pending limit filled → attach SL + TP, cancel others
7. Manage any open position (BE / trailing stop)
8. Cancel limits for expired / mitigated OBs
9. Sleep until the next candle close
"""

import time
from datetime import datetime, timezone
from data       import get_client, fetch_candles, get_account_balance, get_open_position
from indicators import add_indicators
from ob_engine  import OBEngine
from risk       import RiskManager
from trader     import (set_leverage, place_limit_order, attach_sl_tp,
                        cancel_order, manage_position, close_all_orders)
from logger     import log_message, log_trade
from config     import (SYMBOL, MAX_TRADES_DAY, SL_BUFFER_MULT, RR_RATIO)


def seconds_until_next_candle() -> int:
    now     = datetime.now(timezone.utc)
    seconds = 3600 - (now.minute * 60 + now.second)
    return seconds


def build_ob_params(ob: dict, atr: float, balance: float, risk_mgr) -> dict:
    """Derive entry / sl / tp / quantity from an OB dict."""
    direction = 'long' if ob['dir'] == 'bull' else 'short'
    entry     = ob['ob_high'] if direction == 'long' else ob['ob_low']
    sl_buffer = atr * SL_BUFFER_MULT
    sl        = (ob['wick_low']  - sl_buffer if direction == 'long'
                 else ob['wick_high'] + sl_buffer)
    sl_dist   = abs(entry - sl)
    tp        = (entry + sl_dist * RR_RATIO if direction == 'long'
                 else entry - sl_dist * RR_RATIO)
    quantity  = risk_mgr.calc_quantity(balance, entry, sl)
    return dict(direction=direction, entry=entry, sl=sl, tp=tp,
                sl_dist=sl_dist, quantity=quantity)


def place_pending(client, ob: dict, atr: float, balance: float, risk_mgr,
                  existing_order_ids: set, pending_orders: dict,
                  current_price: float) -> bool:
    """
    Place a limit order for an OB if one isn't already live on Binance.
    Skips if price has already passed through the entry level (would fill
    immediately at the wrong price, making SL/TP invalid).
    Stores order_id on the OB dict and params in pending_orders.
    Returns True if an order was placed.
    """
    if ob.get('order_id') and ob['order_id'] in existing_order_ids:
        return False   # already live

    p = build_ob_params(ob, atr, balance, risk_mgr)

    if p['sl_dist'] < 10:
        log_message(f"  [SKIP OB] SL distance too tight (${p['sl_dist']:.2f})")
        return False

    # Proximity check — don't place if price already blew past the entry.
    # A LONG limit at ob_high needs price to be ABOVE ob_high (coming back down).
    # A SHORT limit at ob_low needs price to be BELOW ob_low (coming back up).
    if p['direction'] == 'long' and current_price < p['entry']:
        log_message(f"  [SKIP OB] Price ${current_price:.2f} already below LONG entry "
                    f"${p['entry']:.2f} — limit would fill immediately at wrong price")
        return False
    if p['direction'] == 'short' and current_price > p['entry']:
        log_message(f"  [SKIP OB] Price ${current_price:.2f} already above SHORT entry "
                    f"${p['entry']:.2f} — limit would fill immediately at wrong price")
        return False

    order_id = place_limit_order(
        client, p['direction'], p['quantity'],
        p['entry'], p['sl'], p['tp'],
    )
    if order_id:
        ob['order_id']           = order_id
        pending_orders[order_id] = p      # survives OB removal from active_obs
        return True
    return False


def get_open_order_ids(client) -> set:
    """Return set of orderId ints currently open on Binance for SYMBOL."""
    from binance.error import ClientError
    try:
        orders = client.get_orders(symbol=SYMBOL)
        return {o['orderId'] for o in orders if o['status'] == 'NEW'}
    except ClientError as e:
        log_message(f"[WARN] Could not fetch open orders: {e}")
        return set()


def run():
    log_message("═" * 55)
    log_message("  OB BOT STARTING — BTCUSDT Futures (Binance Demo)")
    log_message("═" * 55)

    client   = get_client()
    ob_eng   = OBEngine()
    risk_mgr = RiskManager()

    set_leverage(client)

    # ── Warm up OBEngine with full candle history ──────────────
    log_message("  Warming up OB engine with historical candles...")
    df_init = fetch_candles(client)
    df_init = add_indicators(df_init)
    ob_eng.warmup(df_init)
    log_message(f"  Warm-up complete — {len(ob_eng.active_obs)} active OBs loaded")

    # pending_orders: {order_id: params}
    # Tracks all live limit orders independently of OB lifecycle.
    # An OB can be removed from active_obs (mitigated/expired) but its
    # order stays here until explicitly cancelled or filled.
    pending_orders: dict = {}

    # ── Place pending limits for all OBs found in warmup ──────
    balance            = get_account_balance(client)
    existing_order_ids = get_open_order_ids(client)
    last_row           = df_init.iloc[-1]

    warmup_price = float(df_init.iloc[-1]['close'])
    if ob_eng.active_obs and risk_mgr.can_trade(balance, MAX_TRADES_DAY):
        log_message(f"  Placing pending limits for {len(ob_eng.active_obs)} warmup OBs...")
        for ob in ob_eng.active_obs:
            place_pending(client, ob, last_row['atr'], balance, risk_mgr,
                          existing_order_ids, pending_orders, warmup_price)

    # Active position tracked locally for BE/trail management
    local_position = None

    while True:
        try:
            now_utc = datetime.now(timezone.utc)
            log_message(f"\n── Candle Check @ {now_utc.strftime('%Y-%m-%d %H:%M')} UTC ──")

            # ── 1. Fetch data + calculate indicators ──────────
            df       = fetch_candles(client)
            df       = add_indicators(df)
            last     = df.iloc[-1]
            price    = last['close']
            atr      = last['atr']
            bar_high = last['high']
            bar_low  = last['low']
            in_sess  = last['in_session']

            log_message(f"  BTC: ${price:,.2f} | ATR: ${atr:.2f} | "
                        f"{'IN SESSION' if in_sess else 'OUT OF SESSION'}")

            # ── 2. Fetch current state from Binance ───────────
            binance_pos        = get_open_position(client)
            existing_order_ids = get_open_order_ids(client)
            balance            = get_account_balance(client)
            log_message(f"  Balance: ${balance:,.2f} | Daily P&L: ${risk_mgr.daily_pnl:.2f}")

            # ── 3. Sync local position with Binance ───────────
            if binance_pos is None and local_position is not None:
                pnl = (price - local_position['entry']) if local_position['dir'] == 'long' \
                      else (local_position['entry'] - price)
                log_trade({
                    'entry_time'  : local_position.get('entry_time', ''),
                    'exit_time'   : now_utc.isoformat(),
                    'direction'   : local_position['dir'],
                    'entry_price' : local_position['entry'],
                    'exit_price'  : price,
                    'sl'          : local_position['sl'],
                    'tp'          : local_position['tp'],
                    'quantity'    : local_position['quantity'],
                    'pnl_usd'     : pnl * local_position['quantity'],
                    'reason'      : 'SL/TP (exchange)',
                    'be_moved'    : local_position['be'],
                })
                risk_mgr.record_trade(pnl * local_position['quantity'])
                local_position = None

            # ── 4. Manage open position (BE + trailing) ───────
            if local_position is not None and binance_pos is not None:
                local_position = manage_position(
                    client, local_position, price, atr, bar_high, bar_low)

            # ── 5. Update OB engine ───────────────────────────
            obs_before    = {id(ob): ob for ob in ob_eng.active_obs}
            _, new_obs    = ob_eng.update(df)
            obs_after_ids = {id(ob) for ob in ob_eng.active_obs}
            removed_obs   = [ob for oid, ob in obs_before.items()
                             if oid not in obs_after_ids]

            # ── 6. Place limits for newly created OBs ─────────
            if new_obs and risk_mgr.can_trade(balance, MAX_TRADES_DAY):
                for ob in new_obs:
                    log_message(f"  [NEW OB] {ob['dir'].upper()} OB formed | "
                                f"Zone: {ob['ob_low']:.2f} – {ob['ob_high']:.2f}")
                    place_pending(client, ob, atr, balance, risk_mgr,
                                  existing_order_ids, pending_orders, price)
                existing_order_ids = get_open_order_ids(client)

            # ── 7. Check if a pending limit just filled ────────
            # Uses pending_orders (not active_obs) so a fill is detected
            # even if the OB was already removed due to mitigation.
            if local_position is None and binance_pos is not None:
                filled_order_id = None
                filled_params   = None

                for order_id, params in list(pending_orders.items()):
                    if order_id not in existing_order_ids:
                        filled_order_id = order_id
                        filled_params   = params
                        break

                if filled_params is not None:
                    direction = filled_params['direction']
                    entry     = float(binance_pos['entry_price'])
                    quantity  = float(binance_pos['size'])
                    sl        = filled_params['sl']
                    tp        = filled_params['tp']

                    # Validate SL is on the correct side of the actual fill.
                    # If fill price differs from OB level (e.g. immediate fill),
                    # recalculate SL/TP from the actual entry price.
                    sl_valid = (sl < entry if direction == 'long' else sl > entry)
                    if not sl_valid:
                        sl_dist = atr * 1.5
                        sl = (entry - sl_dist if direction == 'long'
                              else entry + sl_dist)
                        tp = (entry + sl_dist * RR_RATIO if direction == 'long'
                              else entry - sl_dist * RR_RATIO)
                        log_message(f"  [WARN] SL was wrong side of fill — "
                                    f"recalculated from entry: SL {sl:.2f} | TP {tp:.2f}")

                    log_message(f"  [FILLED] {direction.upper()} limit filled @ {entry:.2f}")

                    attach_sl_tp(client, direction, sl, tp, quantity)

                    # Cancel all other pending limits
                    for other_id in list(pending_orders.keys()):
                        if other_id != filled_order_id and other_id in existing_order_ids:
                            cancel_order(client, other_id)
                    pending_orders.clear()

                    if in_sess:
                        risk_mgr.trades_today += 1

                    local_position = {
                        'dir'        : direction,
                        'entry'      : entry,
                        'sl'         : sl,
                        'tp'         : tp,
                        'sl_dist'    : abs(entry - sl),
                        'quantity'   : quantity,
                        'be'         : False,
                        'entry_time' : now_utc.isoformat(),
                    }

            # ── 8. Cancel limits for removed (expired/mitigated) OBs ─
            for ob in removed_obs:
                oid = ob.get('order_id')
                if oid:
                    if oid in existing_order_ids:
                        cancel_order(client, oid)
                    pending_orders.pop(oid, None)

            # ── 9. Sleep until next candle close ──────────────
            wait = seconds_until_next_candle()
            log_message(f"  Sleeping {wait}s until next candle close...\n")
            time.sleep(wait + 2)

        except KeyboardInterrupt:
            log_message("\n  Bot stopped by user.")
            close_all_orders(client)
            break
        except Exception as e:
            log_message(f"[ERROR] {e}")
            log_message("  Retrying in 60 seconds...")
            time.sleep(60)


if __name__ == '__main__':
    run()
