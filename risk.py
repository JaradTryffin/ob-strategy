# ═══════════════════════════════════════════════════════════════
#  RISK MANAGER — Lot sizing + daily loss guard
# ═══════════════════════════════════════════════════════════════

from config import RISK_PCT, DAILY_LOSS_LIMIT_PCT
from datetime import date


class RiskManager:

    def __init__(self):
        self.daily_pnl    = 0.0
        self.trades_today = 0
        self.last_date    = None

    def new_day_check(self):
        today = date.today()
        if self.last_date != today:
            self.daily_pnl    = 0.0
            self.trades_today = 0
            self.last_date    = today

    def record_trade(self, pnl_usd: float):
        self.new_day_check()
        self.daily_pnl    += pnl_usd
        self.trades_today += 1

    def is_daily_limit_breached(self, account_balance: float) -> bool:
        self.new_day_check()
        limit = account_balance * (DAILY_LOSS_LIMIT_PCT / 100)
        return self.daily_pnl <= -limit

    def can_trade(self, account_balance: float, max_trades_day: int) -> bool:
        self.new_day_check()
        if self.is_daily_limit_breached(account_balance):
            print(f"  [RISK] Daily loss limit reached (${self.daily_pnl:.2f}). No more trades today.")
            return False
        if self.trades_today >= max_trades_day:
            print(f"  [RISK] Max trades for today reached ({self.trades_today}).")
            return False
        return True

    def calc_quantity(self, account_balance: float, entry: float,
                      sl: float, min_qty: float = 0.001, qty_step: float = 0.001) -> float:
        """
        Calculate BTC quantity to risk RISK_PCT of account balance.
        Rounds down to nearest qty_step (Binance minimum for BTCUSDT = 0.001).
        """
        risk_usd  = account_balance * (RISK_PCT / 100)
        sl_dist   = abs(entry - sl)
        if sl_dist == 0:
            return min_qty
        raw_qty   = risk_usd / sl_dist
        # Round down to nearest step
        qty       = max(min_qty, (raw_qty // qty_step) * qty_step)
        return round(qty, 3)
