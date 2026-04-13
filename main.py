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
from config     import (SYMBOL, MAX_TRADES_DAY, SESSION_START, SESSION_END,
                        SL_BUFFER_MULT, RR_RATIO, INITIAL_CAPITAL)


def seconds_until_next_candle() -> int:
    """Returns seconds until the next 1H candle closes."""
    now     = datetime.now(timezone.utc)
    seconds = 3600 - (now.minute * 60 + now.second)
    return seconds


def build_ob_params(ob: dict, atr: float, balance: float, risk_mgr) -> dict:
    """
    Derive entry / sl / tp / quantity from an OB dict.
    Returns a dict ready to pass to place_limit_order.
    """
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
                  existing_order_ids: set) -> bool:
    """
    Place a limit order for an OB if one isn't already live on Binance.
    Stores the orderId on the OB dict.  Returns True if an order was placed.
    """
    if ob.get('order_id') and ob['order_id'] in existing_order_ids:
        return False   # already live

    p = build_ob_params(ob, atr, balance, risk_mgr)

    if p['sl_dist'] < 10:
        log_message(f"  [SKIP OB] SL distance too tight (${p['sl_dist']:.2f})")
        return False

    order_id = place_limit_order(
        client, p['direction'], p['quantity'],
        p['entry'], p['sl'], p['tp'],
    )
    if order_id:
        ob['order_id'] = order_id
        ob['params']   = p      # store for later when we need to attach SL/TP
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

    # ── Place pending limits for all OBs found in warmup ──────
    balance           = get_account_balance(client)
    existing_order_ids = get_open_order_ids(client)
    last_row          = df_init.iloc[-1]

    if ob_eng.active_obs and risk_mgr.can_trade(balance, MAX_TRADES_DAY):
        log_message(f"  Placing pending limits for {len(ob_eng.active_obs)} warmup OBs...")
        for ob in ob_eng.active_obs:
            place_pending(client, ob, last_row['atr'], balance, risk_mgr,
                          existing_order_ids)

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
                # Position closed by SL or TP on exchange
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

            # ── 5. Update OB engine — get new + triggered OBs ─
            _, new_obs = ob_eng.update(df)

            # ── 6. Place limits for any newly created OBs ─────
            if new_obs and risk_mgr.can_trade(balance, MAX_TRADES_DAY):
                for ob in new_obs:
                    log_message(f"  [NEW OB] {ob['dir'].upper()} OB formed | "
                                f"Zone: {ob['ob_low']:.2f} – {ob['ob_high']:.2f}")
                    place_pending(client, ob, atr, balance, risk_mgr,
                                  existing_order_ids)
                # Refresh open orders after placing
                existing_order_ids = get_open_order_ids(client)

            # ── 7. Check if a pending limit just filled ────────
            if local_position is None and binance_pos is not None:
                # Find which OB's limit was filled
                filled_ob = None
                for ob in ob_eng.active_obs:
                    oid = ob.get('order_id')
                    if oid and oid not in existing_order_ids:
                        # Order is gone from open orders — it filled
                        filled_ob = ob
                        break

                if filled_ob is not None:
                    p = filled_ob.get('params', {})
                    direction = p.get('direction', binance_pos['side'])
                    entry     = float(binance_pos['entry_price'])
                    sl        = p.get('sl', 0)
                    tp        = p.get('tp', 0)
                    quantity  = float(binance_pos['size'])

                    log_message(f"  [FILLED] {direction.upper()} limit filled @ {entry:.2f}")

                    # Attach SL + TP now that we have a real position
                    attach_sl_tp(client, direction, sl, tp)

                    # Cancel all other pending limits
                    for ob in ob_eng.active_obs:
                        oid = ob.get('order_id')
                        if oid and oid != filled_ob['order_id'] \
                                and oid in existing_order_ids:
                            cancel_order(client, oid)
                            ob.pop('order_id', None)

                    ob_eng.mark_mitigated(filled_ob)

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

            # ── 8. Cancel limits for expired / mitigated OBs ──
            obs_to_expire = [ob for ob in ob_eng.active_obs
                             if ob.get('mitigated') or ob.get('age', 0) >= 80]
            for ob in obs_to_expire:
                oid = ob.get('order_id')
                if oid and oid in existing_order_ids:
                    cancel_order(client, oid)

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
