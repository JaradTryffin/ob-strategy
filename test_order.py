"""
Order execution test — opens a small LONG then immediately closes it.
Uses minimum quantity (0.001 BTC) to keep it small.
"""
import os, time
from dotenv import load_dotenv
load_dotenv()

from binance.um_futures import UMFutures
from binance.error import ClientError

API_KEY    = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
BASE_URL   = "https://demo-fapi.binance.com"
SYMBOL     = "BTCUSDT"
QTY        = 0.002       # min notional is $100 — 0.002 BTC @ $70k = ~$142

client = UMFutures(key=API_KEY, secret=API_SECRET, base_url=BASE_URL)

print("\n── Order Execution Test ─────────────────────────")

try:
    # ── Current price ─────────────────────────────────
    price = float(client.ticker_price(symbol=SYMBOL)['price'])
    print(f"  BTC price:  ${price:,.2f}")
    print(f"  Order size: {QTY} BTC (≈ ${price * QTY:,.2f})")

    # ── Set leverage to 1x ────────────────────────────
    client.change_leverage(symbol=SYMBOL, leverage=1)
    print("  Leverage:   1x set")

    # ── Open LONG (BUY) ───────────────────────────────
    print("\n  Placing BUY order...")
    buy_order = client.new_order(
        symbol   = SYMBOL,
        side     = "BUY",
        type     = "MARKET",
        quantity = QTY,
    )
    print(f"  ✓ BUY filled — Order ID: {buy_order['orderId']}")

    # ── Wait 2 seconds ────────────────────────────────
    print("  Waiting 2s...")
    time.sleep(2)

    # ── Close LONG (SELL) ─────────────────────────────
    print("  Placing SELL order to close...")
    sell_order = client.new_order(
        symbol        = SYMBOL,
        side          = "SELL",
        type          = "MARKET",
        quantity      = QTY,
        reduceOnly    = "true",
    )
    print(f"  ✓ SELL filled — Order ID: {sell_order['orderId']}")

    # ── Final balance ─────────────────────────────────
    time.sleep(1)
    balances = client.balance()
    usdt = next((b for b in balances if b['asset'] == 'USDT'), None)
    if usdt:
        print(f"\n  USDT balance: ${float(usdt['balance']):,.2f}")

    print("  ✓ Order execution working correctly!\n")

except ClientError as e:
    print(f"  ❌ Order failed: {e.error_message} (code: {e.error_code})\n")
except Exception as e:
    print(f"  ❌ Error: {e}\n")
