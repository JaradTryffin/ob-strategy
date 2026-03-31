# ═══════════════════════════════════════════════════════════════
#  LOGGER — Console + CSV trade log
# ═══════════════════════════════════════════════════════════════

import csv
import os
from datetime import datetime

LOG_FILE   = "ob_bot_log.txt"
TRADE_FILE = "ob_bot_trades.csv"

TRADE_HEADERS = [
    'entry_time', 'exit_time', 'direction', 'entry_price',
    'exit_price', 'sl', 'tp', 'quantity', 'pnl_usd', 'reason', 'be_moved'
]


def log_message(msg: str):
    timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    line      = f"[{timestamp} UTC] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')


def log_trade(trade: dict):
    file_exists = os.path.exists(TRADE_FILE)
    with open(TRADE_FILE, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=TRADE_HEADERS)
        if not file_exists:
            writer.writeheader()
        writer.writerow({k: trade.get(k, '') for k in TRADE_HEADERS})
    log_message(
        f"[TRADE CLOSED] {trade.get('direction','').upper()} | "
        f"Entry: {trade.get('entry_price')} → Exit: {trade.get('exit_price')} | "
        f"P&L: ${trade.get('pnl_usd', 0):.2f} | Reason: {trade.get('reason')}"
    )
