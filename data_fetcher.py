import gzip
import io
import json
import threading
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

import config

_BASE = "https://api.upstox.com/v2"
_IST = timezone(timedelta(hours=5, minutes=30))
_price_cache: dict = {}
_cache_lock = threading.Lock()


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {config.UPSTOX_ACCESS_TOKEN}",
        "Accept": "application/json",
    }


def get_funds() -> float:
    """Return available cash (INR). Falls back to configured capital if API unavailable."""
    try:
        resp = requests.get(f"{_BASE}/user/fund-and-margin", headers=_headers(), timeout=10)
        if resp.ok:
            data = resp.json().get("data", [])
            for segment in data:
                if segment.get("segment") == "SEC":
                    return float(segment.get("available_margin", 0))
    except Exception:
        pass
    return float(config.TRADING_CAPITAL_INR)


def get_instruments_nse() -> list:
    """Download NSE EQ instruments from Upstox instrument master."""
    resp = requests.get(
        "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz",
        timeout=30,
    )
    resp.raise_for_status()
    with gzip.open(io.BytesIO(resp.content)) as f:
        instruments = json.load(f)
    return [i for i in instruments if i.get("segment") == "NSE_EQ" and i.get("instrument_type") == "EQ"]


def get_market_quotes_ltp(instrument_keys: list) -> dict:
    """Fetch LTP + depth for up to 500 instruments at once."""
    chunk_size = 500
    result = {}
    for i in range(0, len(instrument_keys), chunk_size):
        chunk = instrument_keys[i:i + chunk_size]
        params = {"instrument_key": ",".join(chunk)}
        resp = requests.get(
            f"{_BASE}/market-quote/ltp",
            headers=_headers(),
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        result.update(resp.json().get("data", {}))
    return result


def get_ohlcv(instrument_key: str, interval: str = "1minute") -> pd.DataFrame:
    """Fetch intraday OHLCV candles for a single instrument."""
    to_date = datetime.now(_IST).strftime("%Y-%m-%d")
    resp = requests.get(
        f"{_BASE}/historical-candle/intraday/{instrument_key}/{interval}",
        headers=_headers(),
        params={"to_date": to_date},
        timeout=15,
    )
    resp.raise_for_status()
    candles = resp.json().get("data", {}).get("candles", [])
    if not candles:
        return pd.DataFrame()
    df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df.set_index("timestamp", inplace=True)
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def apply_liquidity_filter(quotes: dict) -> list:
    """Keep only instruments passing daily traded value filter."""
    passing = []
    for key, q in quotes.items():
        volume = float(q.get("volume", 0))
        price = float(q.get("last_price", 0))
        daily_value_cr = (volume * price) / 1e7
        if daily_value_cr >= config.MIN_DAILY_TRADED_VALUE_CR:
            passing.append(key)
    return passing


def apply_spread_filter(quotes: dict) -> list:
    """Keep only instruments with bid-ask spread <= MAX_BID_ASK_SPREAD_PCT."""
    passing = []
    for key, q in quotes.items():
        price = float(q.get("last_price", 0))
        if price <= 0:
            continue
        depth = q.get("depth", {})
        buys = depth.get("buy", [])
        sells = depth.get("sell", [])
        if not buys or not sells:
            continue  # reject stocks with no market depth
        best_bid = float(buys[0].get("price", 0))
        best_ask = float(sells[0].get("price", 0))
        if best_bid <= 0 or best_ask <= 0:
            continue
        spread_pct = (best_ask - best_bid) / price * 100
        if spread_pct <= config.MAX_BID_ASK_SPREAD_PCT:
            passing.append(key)
    return passing


def get_cached_price(instrument_key: str):
    with _cache_lock:
        return _price_cache.get(instrument_key)


def update_price_cache(instrument_key: str, price: float):
    with _cache_lock:
        _price_cache[instrument_key] = price
