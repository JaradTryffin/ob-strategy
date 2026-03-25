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
5. Manage any open position (BE / trailing stop)
6. Check for new OB entry signal
7. Sleep until the next candle close
"""

import time
from datetime import datetime, timezone
from data       import get_client, fetch_candles, get_account_balance, get_open_position
from indicators import add_indicators
from ob_engine  import OBEngine
from risk       import RiskManager
from trader     import set_leverage, open_position, manage_position, close_all_orders
from logger     import log_message, log_trade
from config     import (SYMBOL, MAX_TRADES_DAY, SESSION_START, SESSION_END,
                        SL_BUFFER_MULT, RR_RATIO, INITIAL_CAPITAL)


def seconds_until_next_candle() -> int:
    """Returns seconds until the next 1H candle closes."""
    now     = datetime.now(timezone.utc)
    seconds = 3600 - (now.minute * 60 + now.second)
    return seconds


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

    # Track the current open position locally so we can manage BE/trail
    local_position = None

    while True:
        try:
            now_utc = datetime.now(timezone.utc)
            log_message(f"\n── Candle Check @ {now_utc.strftime('%Y-%m-%d %H:%M')} UTC ──")

            # ── 1. Fetch data + calculate indicators ──────────
            df      = fetch_candles(client)
            df      = add_indicators(df)
            last    = df.iloc[-1]
            price   = last['close']
            atr     = last['atr']
            bar_high = last['high']
            bar_low  = last['low']
            in_sess  = last['in_session']

            log_message(f"  BTC: ${price:,.2f} | ATR: ${atr:.2f} | "
                        f"{'IN SESSION' if in_sess else 'OUT OF SESSION'}")

            # ── 2. Check if Binance position still open ────────
            binance_pos = get_open_position(client)

            # ── 3. Sync local position with Binance ───────────
            if binance_pos is None and local_position is not None:
                # Position was closed by SL or TP on exchange
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
                local_position = manage_position(client, local_position, price, atr, bar_high, bar_low)

            # ── 5. Check balance + daily limits ───────────────
            balance = get_account_balance(client)
            log_message(f"  Balance: ${balance:,.2f} | Daily P&L: ${risk_mgr.daily_pnl:.2f}")

            # ── 6. Look for new OB entry ───────────────────────
            triggered_obs = ob_eng.update(df)

            if (local_position is None
                    and in_sess
                    and risk_mgr.can_trade(balance, MAX_TRADES_DAY)
                    and triggered_obs):

                ob = triggered_obs[0]   # take first valid OB

                direction = ob['dir']
                entry     = ob['ob_high'] if direction == 'long' else ob['ob_low']
                sl_buffer = atr * SL_BUFFER_MULT
                sl        = (ob['wick_low']  - sl_buffer if direction == 'long'
                             else ob['wick_high'] + sl_buffer)
                sl_dist   = abs(entry - sl)
                tp        = (entry + sl_dist * RR_RATIO if direction == 'long'
                             else entry - sl_dist * RR_RATIO)

                if sl_dist < 10:    # minimum $10 SL distance on BTC
                    log_message(f"  [SKIP] SL distance too tight (${sl_dist:.2f})")
                else:
                    quantity = risk_mgr.calc_quantity(balance, entry, sl)
                    log_message(f"  [SIGNAL] {direction.upper()} OB | "
                                f"Entry: {entry:.2f} | SL: {sl:.2f} | "
                                f"TP: {tp:.2f} | Qty: {quantity}")

                    pos = open_position(client, direction, quantity, entry, sl, tp)
                    if pos is not None:
                        pos['entry_time'] = now_utc.isoformat()
                        local_position    = pos
                        ob_eng.mark_mitigated(ob)
                        risk_mgr.trades_today += 1

            elif not triggered_obs:
                log_message("  No OB signal this candle.")

            # ── 7. Sleep until next candle close ──────────────
            wait = seconds_until_next_candle()
            log_message(f"  Sleeping {wait}s until next candle close...\n")
            time.sleep(wait + 2)   # +2s buffer for candle to fully close on exchange

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
