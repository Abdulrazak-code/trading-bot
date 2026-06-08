import numpy as np
import pandas as pd
import ta


def _supertrend(high, low, close, period: int = 10, multiplier: float = 3.0):
    """Supertrend indicator. Returns (line_value, is_uptrend) for the latest candle.
    Standard intraday setting: 10-period ATR, 3x multiplier."""
    atr = ta.volatility.AverageTrueRange(high=high, low=low, close=close, window=period).average_true_range()
    hl2 = (high + low) / 2
    upper_basic = hl2 + multiplier * atr
    lower_basic = hl2 - multiplier * atr

    n = len(close)
    final_upper = [0.0] * n
    final_lower = [0.0] * n
    st = [0.0] * n
    up = [True] * n

    c = close.values
    ub = upper_basic.values
    lb = lower_basic.values

    for i in range(n):
        if i == 0 or np.isnan(ub[i]) or np.isnan(lb[i]):
            final_upper[i] = ub[i] if not np.isnan(ub[i]) else c[i]
            final_lower[i] = lb[i] if not np.isnan(lb[i]) else c[i]
            st[i] = final_upper[i]
            up[i] = True
            continue

        final_upper[i] = ub[i] if (ub[i] < final_upper[i-1] or c[i-1] > final_upper[i-1]) else final_upper[i-1]
        final_lower[i] = lb[i] if (lb[i] > final_lower[i-1] or c[i-1] < final_lower[i-1]) else final_lower[i-1]

        if st[i-1] == final_upper[i-1]:
            if c[i] <= final_upper[i]:
                st[i] = final_upper[i]; up[i] = False
            else:
                st[i] = final_lower[i]; up[i] = True
        else:
            if c[i] >= final_lower[i]:
                st[i] = final_lower[i]; up[i] = True
            else:
                st[i] = final_upper[i]; up[i] = False

    return round(float(st[-1]), 2), bool(up[-1])


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
    # How many of the last 5 candles closed above VWAP (0–5); ≥3 = sustained bullish positioning
    vwap_above_count = int(sum(1 for c, v in zip(close.iloc[-5:], vwap.iloc[-5:]) if c > v))

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

    # EMA 8 / 13 / 21 crossover
    ema8  = float(close.ewm(span=8,  adjust=False).mean().iloc[-1])
    ema13 = float(close.ewm(span=13, adjust=False).mean().iloc[-1])
    ema21 = float(close.ewm(span=21, adjust=False).mean().iloc[-1])
    ema_bullish = ema8 > ema13 > ema21          # full bullish stack
    ema_bearish = ema8 < ema13 < ema21          # full bearish stack
    ema_spread_pct = (ema8 - ema21) / ema21 * 100 if ema21 > 0 else 0.0

    # ADX (10-period) — measures trend strength; > 25 = trending, < 20 = choppy
    adx_ind = ta.trend.ADXIndicator(high=high, low=low, close=close, window=10)
    adx_series = adx_ind.adx()
    adx = float(adx_series.iloc[-1]) if not adx_series.isna().all() else 0.0
    if np.isnan(adx):
        adx = 0.0

    # OHL signal: first candle's open == its low → buyers absorbed all selling at open
    ohl_buy = False
    if "open" in df.columns and len(df) >= 1:
        first = df.iloc[0]
        tolerance = float(first["open"]) * 0.001   # within 0.1%
        ohl_buy = abs(float(first["open"]) - float(first["low"])) <= tolerance

    # Momentum signal (BB + RSI + ADX combined)
    momentum_long = bb_position > 0.95 and rsi > 50 and adx > 25

    # Supertrend (10-period, 3x ATR) — direction + line value (acts as trailing stop reference)
    st_line, st_uptrend = _supertrend(high, low, close, period=10, multiplier=3.0)

    candle = detect_candle_patterns(df)

    return {
        "rsi": rsi,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
        "vwap_pct": vwap_pct,
        "vwap_above_count": vwap_above_count,
        "bb_position": bb_position,
        "atr": atr,
        "volume_spike": volume_spike,
        "price": current_price,
        "day_high": float(high.max()),
        "day_low": float(low.min()),
        "last_green": last_green,
        "price_slope": round(price_slope, 4),
        "consecutive_red": consecutive_red,
        "candle_pattern": candle["candle_pattern"],
        "candle_signal": candle["candle_signal"],
        "ema8": round(ema8, 2),
        "ema13": round(ema13, 2),
        "ema21": round(ema21, 2),
        "ema_bullish": ema_bullish,
        "ema_bearish": ema_bearish,
        "ema_spread_pct": round(ema_spread_pct, 3),
        "adx": round(adx, 1),
        "ohl_buy": ohl_buy,
        "momentum_long": momentum_long,
        "supertrend": st_line,
        "supertrend_up": st_uptrend,
    }


def detect_candle_patterns(df: pd.DataFrame) -> dict:
    """Detect key candlestick patterns from the last 3 candles. Returns pattern name and BULLISH/BEARISH/NONE signal."""
    if "open" not in df.columns or len(df) < 3:
        return {"candle_pattern": "none", "candle_signal": "NONE"}

    c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]

    def _body(c):    return abs(float(c["close"]) - float(c["open"]))
    def _lo(c):      return float(min(c["open"], c["close"])) - float(c["low"])
    def _hi(c):      return float(c["high"]) - float(max(c["open"], c["close"]))
    def _green(c):   return float(c["close"]) > float(c["open"])
    def _red(c):     return float(c["close"]) < float(c["open"])

    b1, b2, b3 = _body(c1), _body(c2), _body(c3)
    lw3, uw3 = _lo(c3), _hi(c3)

    # ── 3-candle patterns (highest priority) ────────────────────────────────
    if _green(c1) and _green(c2) and _green(c3) and b1 > 0 and b2 > 0 and b3 > 0:
        if float(c2["close"]) > float(c1["close"]) and float(c3["close"]) > float(c2["close"]):
            return {"candle_pattern": "three_white_soldiers", "candle_signal": "BULLISH"}

    if _red(c1) and _red(c2) and _red(c3) and b1 > 0 and b2 > 0 and b3 > 0:
        if float(c2["close"]) < float(c1["close"]) and float(c3["close"]) < float(c2["close"]):
            return {"candle_pattern": "three_black_crows", "candle_signal": "BEARISH"}

    # Morning Star: big red → small body → big green closing above c1 midpoint
    if _red(c1) and b1 > 0 and b2 < b1 * 0.3 and _green(c3) and b3 > 0:
        if float(c3["close"]) > (float(c1["open"]) + float(c1["close"])) / 2:
            return {"candle_pattern": "morning_star", "candle_signal": "BULLISH"}

    # Evening Star: big green → small body → big red closing below c1 midpoint
    if _green(c1) and b1 > 0 and b2 < b1 * 0.3 and _red(c3) and b3 > 0:
        if float(c3["close"]) < (float(c1["open"]) + float(c1["close"])) / 2:
            return {"candle_pattern": "evening_star", "candle_signal": "BEARISH"}

    # ── 2-candle patterns ────────────────────────────────────────────────────
    if _red(c2) and _green(c3) and b3 > b2:
        if float(c3["open"]) <= float(c2["close"]) and float(c3["close"]) >= float(c2["open"]):
            return {"candle_pattern": "bullish_engulfing", "candle_signal": "BULLISH"}

    if _green(c2) and _red(c3) and b3 > b2:
        if float(c3["open"]) >= float(c2["close"]) and float(c3["close"]) <= float(c2["open"]):
            return {"candle_pattern": "bearish_engulfing", "candle_signal": "BEARISH"}

    # ── 1-candle patterns ────────────────────────────────────────────────────
    # Bullish Marubozu: full green candle, wicks < 5% of body
    if _green(c3) and b3 > 0 and lw3 <= b3 * 0.05 and uw3 <= b3 * 0.05:
        return {"candle_pattern": "bullish_marubozu", "candle_signal": "BULLISH"}

    # Bearish Marubozu: full red candle, wicks < 5% of body
    if _red(c3) and b3 > 0 and lw3 <= b3 * 0.05 and uw3 <= b3 * 0.05:
        return {"candle_pattern": "bearish_marubozu", "candle_signal": "BEARISH"}

    # Hammer: small body, long lower wick (>= 2x body), tiny upper wick
    if b3 > 0 and lw3 >= 2 * b3 and uw3 <= b3 * 0.5:
        return {"candle_pattern": "hammer", "candle_signal": "BULLISH"}

    # Inverted Hammer (green): long upper wick, tiny lower wick — bullish reversal at bottom
    if _green(c3) and b3 > 0 and uw3 >= 2 * b3 and lw3 <= b3 * 0.5:
        return {"candle_pattern": "inverted_hammer", "candle_signal": "BULLISH"}

    # Shooting Star (red): long upper wick, tiny lower wick — bearish at top
    if _red(c3) and b3 > 0 and uw3 >= 2 * b3 and lw3 <= b3 * 0.5:
        return {"candle_pattern": "shooting_star", "candle_signal": "BEARISH"}

    return {"candle_pattern": "none", "candle_signal": "NONE"}


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


def compute_pivots(prev_high: float, prev_low: float, prev_close: float) -> dict:
    """Classic floor-trader pivot points from previous day's H/L/C."""
    p = (prev_high + prev_low + prev_close) / 3
    rng = prev_high - prev_low
    return {
        "pivot": round(p, 2),
        "r1": round(2 * p - prev_low, 2),
        "s1": round(2 * p - prev_high, 2),
        "r2": round(p + rng, 2),
        "s2": round(p - rng, 2),
    }


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
    candle_pattern = data.get("candle_pattern", "none")
    candle_str = f" candle={candle_pattern}" if candle_pattern != "none" else ""
    ema_str = (
        f"EMA8/13/21={data.get('ema8',0):.1f}/{data.get('ema13',0):.1f}/{data.get('ema21',0):.1f}"
        f"({'BULL' if data.get('ema_bullish') else 'BEAR' if data.get('ema_bearish') else 'MIX'}"
        f" spread={data.get('ema_spread_pct',0):+.2f}%)"
    )
    adx_str = f"ADX={data.get('adx', 0):.0f}({'trend' if data.get('adx', 0) >= 25 else 'chop'})"
    ohl_str = " OHL-BUY" if data.get("ohl_buy") else ""
    mom_str = " MOMENTUM-LONG" if data.get("momentum_long") else ""
    st_line = data.get("supertrend")
    st_str = ""
    if st_line:
        st_str = f" ST={st_line}({'UP' if data.get('supertrend_up') else 'DOWN'})"

    gap_pct = data.get("gap_pct")
    gap_str = ""
    if gap_pct is not None and abs(gap_pct) >= 0.5:
        gap_str = f" GAP={gap_pct:+.1f}%({'UP' if gap_pct > 0 else 'DOWN'})"

    pivot = data.get("pivot")
    piv_str = ""
    if pivot:
        # which level is price reacting to?
        r1, s1 = data.get("r1", 0), data.get("s1", 0)
        if price > r1:
            loc = f"above R1({r1})"
        elif price > pivot:
            loc = f"P({pivot})-R1({r1})"
        elif price > s1:
            loc = f"S1({s1})-P({pivot})"
        else:
            loc = f"below S1({s1})"
        piv_str = f" PIVOT[{loc}]"

    return (
        f"{symbol}: Rs{price:.1f} vol_spike={data['volume_spike']:.1f}x "
        f"RSI={data['rsi']:.0f} MACD_hist={data['macd_hist']:+.3f} "
        f"VWAP={data['vwap_pct']:+.1f}% BB={data['bb_position']:.2f} "
        f"ATR={data['atr']:.3f} H={data['day_high']:.1f} L={data['day_low']:.1f}({pct_from_high:+.1f}%from_high) "
        f"OR={or_status}(init={or_dir}) {trend_str}{candle_str} "
        f"{ema_str} {adx_str}{st_str}{ohl_str}{mom_str}{gap_str}{piv_str} "
        f"spread={data['spread_pct']:.2f}% | {news_str}"
    )
