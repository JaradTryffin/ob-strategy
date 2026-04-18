from __future__ import annotations
# ═══════════════════════════════════════════════════════════════
#  DATA — Bybit REST backfill + WebSocket candle feed
# ═══════════════════════════════════════════════════════════════

import time
import logging
import pandas as pd
from typing import Callable, Optional
from pybit.unified_trading import HTTP, WebSocket
from config import API_KEY, API_SECRET, DEMO, SYMBOL, TIMEFRAME, CANDLES_NEEDED

logger = logging.getLogger(__name__)

BAR_SECONDS = int(TIMEFRAME) * 60   # 3600 for 1H


def get_http_client() -> HTTP:
    """Authenticated HTTP client for account/order calls."""
    return HTTP(
        demo       = DEMO,
        testnet    = False,
        api_key    = API_KEY,
        api_secret = API_SECRET,
    )


def _public_http() -> HTTP:
    """Unauthenticated client for market data (always mainnet — demo uses real data)."""
    return HTTP(testnet=False)


def _parse_kline(raw: list) -> dict:
    """Parse Bybit REST kline list → internal dict. REST returns newest-first."""
    return {
        "timestamp": int(raw[0]),
        "open":      float(raw[1]),
        "high":      float(raw[2]),
        "low":       float(raw[3]),
        "close":     float(raw[4]),
        "volume":    float(raw[5]),
    }


def fetch_candles(n: int = CANDLES_NEEDED) -> pd.DataFrame:
    """
    Fetch the last n *closed* 1H bars from Bybit REST.
    Returns a DataFrame indexed by open_time (UTC), oldest first.
    """
    client  = _public_http()
    resp    = client.get_kline(
        category = "linear",
        symbol   = SYMBOL,
        interval = TIMEFRAME,
        limit    = n + 1,   # +1 in case the current bar is still open
    )
    raw_bars = resp["result"]["list"]   # newest-first

    now_ms  = int(time.time() * 1000)
    closed  = [b for b in raw_bars if int(b[0]) + BAR_SECONDS * 1000 <= now_ms]
    bars    = [_parse_kline(b) for b in closed]
    bars.reverse()          # oldest-first
    bars = bars[-n:]        # trim to requested count

    df = pd.DataFrame(bars)
    df["open_time"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("open_time", inplace=True)
    df.index.name = "datetime"
    df = df[["open", "high", "low", "close", "volume"]]

    logger.info("Fetched %d closed 1H bars", len(df))
    return df


def bars_to_df(bars: list[dict]) -> pd.DataFrame:
    """Convert a list of bar dicts (oldest-first) to a DataFrame."""
    df = pd.DataFrame(bars)
    df["open_time"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("open_time", inplace=True)
    df.index.name = "datetime"
    return df[["open", "high", "low", "close", "volume"]]


def get_account_balance() -> float:
    """Return available USDT wallet balance."""
    client = get_http_client()
    resp   = client.get_wallet_balance(accountType="UNIFIED", coin="USDT")
    coins  = resp["result"]["list"][0]["coin"]
    for c in coins:
        if c["coin"] == "USDT":
            return float(c["availableToWithdraw"])
    return 0.0


def get_open_position() -> dict | None:
    """Return open BTCUSDT position or None if flat."""
    client    = get_http_client()
    resp      = client.get_positions(category="linear", symbol=SYMBOL)
    positions = resp["result"]["list"]
    for p in positions:
        if float(p.get("size", 0)) != 0.0:
            return {
                "side"          : "long" if p["side"] == "Buy" else "short",
                "size"          : abs(float(p["size"])),
                "entry_price"   : float(p["avgPrice"]),
                "unrealized_pnl": float(p["unrealisedPnl"]),
                "sl"            : float(p["stopLoss"]) if p.get("stopLoss") else None,
                "tp"            : float(p["takeProfit"]) if p.get("takeProfit") else None,
            }
    return None


class KlineFeed:
    """
    Bybit WebSocket candle feed.
    Calls on_bar_close(bar: dict) on every confirmed closed 1H bar.
    """

    def __init__(self) -> None:
        self._ws: Optional[WebSocket] = None
        self._on_bar_close: Optional[Callable] = None

    def _ws_callback(self, msg: dict) -> None:
        try:
            data = msg.get("data", [])
            for item in data:
                if item.get("confirm", False):
                    bar = {
                        "timestamp": int(item["start"]),
                        "open":      float(item["open"]),
                        "high":      float(item["high"]),
                        "low":       float(item["low"]),
                        "close":     float(item["close"]),
                        "volume":    float(item["volume"]),
                    }
                    logger.info("Bar confirmed: ts=%s close=%.2f",
                                bar["timestamp"], bar["close"])
                    if self._on_bar_close:
                        self._on_bar_close(bar)
        except Exception as exc:
            logger.error("WS callback error: %s", exc, exc_info=True)

    def start(self, on_bar_close: Callable) -> None:
        """Connect WebSocket and subscribe to 1H klines."""
        self._on_bar_close = on_bar_close
        # Public stream — always mainnet (demo uses real market data)
        self._ws = WebSocket(testnet=False, channel_type="linear")
        self._ws.kline_stream(
            interval = int(TIMEFRAME),
            symbol   = SYMBOL,
            callback = self._ws_callback,
        )
        logger.info("WebSocket subscribed: kline.%s.%s", TIMEFRAME, SYMBOL)

    def stop(self) -> None:
        if self._ws:
            try:
                self._ws.exit()
            except Exception:
                pass
