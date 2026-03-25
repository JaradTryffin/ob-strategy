"""Quick test — confirms API keys work and fetches account balance + BTC price."""
import sys
import os
from dotenv import load_dotenv
load_dotenv()

from binance.um_futures import UMFutures

API_KEY    = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
BASE_URL   = "https://demo-fapi.binance.com"

if not API_KEY or not API_SECRET:
    print("❌  API keys not found — check your .env file")
    sys.exit(1)

print("\n── Binance Demo Futures — Connection Test ───────")

try:
    client = UMFutures(key=API_KEY, secret=API_SECRET, base_url=BASE_URL)

    # 1. Server time
    client.time()
    print("  ✓ Server connection OK")

    # 2. BTC price
    price = client.ticker_price(symbol="BTCUSDT")
    print(f"  ✓ BTCUSDT price:  ${float(price['price']):,.2f}")

    # 3. Account balance
    balances = client.balance()
    usdt = next((b for b in balances if b['asset'] == 'USDT'), None)
    if usdt:
        print(f"  ✓ USDT balance:   ${float(usdt['balance']):,.2f}")
    else:
        print("  ⚠ No USDT balance found")

    print("  ✓ Bot is ready to run!\n")

except Exception as e:
    print(f"  ❌ Connection failed: {e}\n")
