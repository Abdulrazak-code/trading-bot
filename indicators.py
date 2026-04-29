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

    return {
        "rsi": rsi,
        "macd_signal": macd_signal,
        "vwap_pct": vwap_pct,
        "bb_position": bb_position,
        "atr": atr,
        "volume_spike": volume_spike,
        "price": current_price,
    }


def score_and_rank(candidates: dict, n: int = 50) -> dict:
    scored = []
    for symbol, data in candidates.items():
        rsi_score = abs(data["rsi"] - 50) / 50
        volume_score = min(data["volume_spike"] / 5.0, 1.0)
        macd_score = min(abs(data["macd_signal"]) / 2.0, 1.0)
        vwap_score = min(abs(data["vwap_pct"]) / 2.0, 1.0)
        spread_penalty = min(data["spread_pct"] / 0.5, 1.0)
        composite = (volume_score * 0.4 + rsi_score * 0.2 + macd_score * 0.2 + vwap_score * 0.2) * (1 - spread_penalty * 0.3)
        scored.append((symbol, composite, data))
    scored.sort(key=lambda x: x[1], reverse=True)
    return {sym: data for sym, _, data in scored[:n]}


def compress_packet(symbol: str, data: dict, headlines: list) -> str:
    news_str = " | ".join(headlines[:2]) if headlines else "no news"
    return (
        f"{symbol}: Rs{data['price']:.1f} vol_spike={data['volume_spike']:.1f}x "
        f"RSI={data['rsi']:.0f} MACD={data['macd_signal']:+.2f} "
        f"VWAP={data['vwap_pct']:+.1f}% BB={data['bb_position']:.2f} "
        f"spread={data['spread_pct']:.2f}% | {news_str}"
    )
