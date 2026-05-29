import numpy as np
import pandas as pd
import ta


def compute_indicators(df: pd.DataFrame) -> dict:
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    rsi_series = ta.momentum.RSIIndicator(close=close, window=14).rsi()
    rsi = float(rsi_series.iloc[-1]) if not rsi_series.isna().all() else 50.0
    if np.isnan(rsi):
        rsi = 50.0

    macd = ta.trend.MACD(close=close, window_slow=26, window_fast=12, window_sign=9)
    macd_signal_series = macd.macd_signal()
    macd_signal = float(macd_signal_series.iloc[-1]) if not macd_signal_series.isna().all() else 0.0
    if np.isnan(macd_signal):
        macd_signal = 0.0
    macd_hist_series = macd.macd_diff()
    macd_hist = float(macd_hist_series.iloc[-1]) if not macd_hist_series.isna().all() else 0.0
    if np.isnan(macd_hist):
        macd_hist = 0.0

    typical_price = (high + low + close) / 3
    vwap = (typical_price * volume).cumsum() / volume.cumsum()
    vwap_val = float(vwap.iloc[-1])
    current_price = float(close.iloc[-1])
    vwap_pct = (current_price - vwap_val) / vwap_val * 100 if vwap_val > 0 else 0.0

    bb = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)
    upper = bb.bollinger_hband()
    lower = bb.bollinger_lband()
    upper_val = float(upper.iloc[-1])
    lower_val = float(lower.iloc[-1])
    if np.isnan(upper_val) or np.isnan(lower_val) or upper_val == lower_val:
        bb_position = 0.5
    else:
        bb_position = (current_price - lower_val) / (upper_val - lower_val)

    atr_series = ta.volatility.AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range()
    atr = float(atr_series.iloc[-1]) if not atr_series.isna().all() else 0.0
    if np.isnan(atr):
        atr = 0.0

    avg_volume = float(volume.iloc[:-1].mean()) if len(volume) > 1 else float(volume.iloc[-1])
    current_volume = float(volume.iloc[-1])
    volume_spike = current_volume / avg_volume if avg_volume > 0 else 1.0

    # Price direction: is the stock still falling or has it started to turn?
    # last_green = latest candle is bullish (buying pressure appeared)
    last_open = float(df["open"].iloc[-1]) if "open" in df.columns else current_price
    last_green = current_price > last_open

    # price_slope = change in close over last 3 candles (positive = rising, negative = still falling)
    if len(close) >= 3:
        price_slope = float(close.iloc[-1] - close.iloc[-3])
    elif len(close) >= 2:
        price_slope = float(close.iloc[-1] - close.iloc[-2])
    else:
        price_slope = 0.0

    # consecutive_red = how many candles in a row have been falling (close < open)
    consecutive_red = 0
    if "open" in df.columns:
        for i in range(1, min(6, len(df)) + 1):
            c = float(df["close"].iloc[-i])
            o = float(df["open"].iloc[-i])
            if c < o:
                consecutive_red += 1
            else:
                break

    return {
        "rsi": rsi,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
        "vwap_pct": vwap_pct,
        "bb_position": bb_position,
        "atr": atr,
        "volume_spike": volume_spike,
        "price": current_price,
        "day_high": float(high.max()),
        "day_low": float(low.min()),
        "last_green": last_green,
        "price_slope": round(price_slope, 4),
        "consecutive_red": consecutive_red,
    }


def score_and_rank(candidates: dict, n: int = 50) -> dict:
    scored = []
    for symbol, data in candidates.items():
        volume_score = min(data["volume_spike"] / 5.0, 1.0)
        # ATR as % of price — higher ATR means more room to clear break-even costs
        atr_score = min(data["atr"] / (data["price"] * 0.005), 1.0) if data["price"] > 0 else 0.0
        spread_penalty = min(data["spread_pct"] / 0.5, 1.0)
        composite = (volume_score * 0.6 + atr_score * 0.4) * (1 - spread_penalty * 0.3)
        scored.append((symbol, composite, data))
    scored.sort(key=lambda x: x[1], reverse=True)
    return {sym: data for sym, _, data in scored[:n]}


def compute_opening_range(df: pd.DataFrame) -> dict:
    """Extract the 9:15–9:30 opening range from intraday 1-min candles."""
    from datetime import time as _time
    idx = df.index
    if hasattr(idx, "tz") and idx.tz is not None:
        times = pd.Series([ts.time() for ts in idx], index=idx)
    else:
        times = pd.Series([pd.Timestamp(ts).time() for ts in idx], index=idx)
    mask = (times >= _time(9, 15)) & (times < _time(9, 30))
    or_bars = df[mask]
    if len(or_bars) < 3:
        return {"or_high": 0.0, "or_low": 0.0, "or_direction": "UNKNOWN"}
    or_high = float(or_bars["high"].max())
    or_low = float(or_bars["low"].min())
    or_open = float(or_bars["open"].iloc[0])
    or_close = float(or_bars["close"].iloc[-1])
    move_pct = (or_close - or_open) / or_open * 100 if or_open > 0 else 0.0
    direction = "UP" if move_pct > 0.1 else "DOWN" if move_pct < -0.1 else "FLAT"
    return {"or_high": round(or_high, 2), "or_low": round(or_low, 2), "or_direction": direction}


def compress_packet(symbol: str, data: dict, headlines: list) -> str:
    news_str = " | ".join(headlines[:2]) if headlines else "no news"
    pct_from_high = (data['price'] - data['day_high']) / data['day_high'] * 100 if data['day_high'] > 0 else 0.0
    price = data['price']
    or_high = data.get('or_high', 0.0)
    or_low = data.get('or_low', 0.0)
    or_dir = data.get('or_direction', 'UNKNOWN')
    if or_high > 0 and price > or_high:
        or_status = f"BROKE-UP(+{(price - or_high) / or_high * 100:.2f}%)"
    elif or_low > 0 and price < or_low:
        or_status = f"BROKE-DOWN(-{(or_low - price) / or_low * 100:.2f}%)"
    elif or_high > 0:
        or_status = f"IN-RANGE(H={or_high} L={or_low})"
    else:
        or_status = "UNKNOWN"
    slope = data.get("price_slope", 0.0)
    red_count = data.get("consecutive_red", 0)
    last_green = data.get("last_green", True)
    trend_str = (
        f"trend={'UP' if (slope > 0 and last_green) else 'TURNING' if last_green else 'DOWN'}"
        f"(slope={slope:+.3f} red={red_count})"
    )
    return (
        f"{symbol}: Rs{price:.1f} vol_spike={data['volume_spike']:.1f}x "
        f"RSI={data['rsi']:.0f} MACD_hist={data['macd_hist']:+.3f} "
        f"VWAP={data['vwap_pct']:+.1f}% BB={data['bb_position']:.2f} "
        f"ATR={data['atr']:.3f} H={data['day_high']:.1f} L={data['day_low']:.1f}({pct_from_high:+.1f}%from_high) "
        f"OR={or_status}(init={or_dir}) {trend_str} "
        f"spread={data['spread_pct']:.2f}% | {news_str}"
    )
