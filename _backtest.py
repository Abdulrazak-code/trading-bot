"""
Backtest: replay logged trades through the new strategy rules.
Fetches 1-min OHLCV for the actual date of each trade.
"""
import csv
import math
import requests
from datetime import datetime, timedelta, timezone
from indicators import compute_indicators
from order_executor import calculate_charges
import config

_IST = timezone(timedelta(hours=5, minutes=30))
_BASE = "https://api.upstox.com/v2"

def _headers():
    return {"Authorization": f"Bearer {config.UPSTOX_ACCESS_TOKEN}", "Accept": "application/json"}

def get_ohlcv_for_date(instrument_key: str, date_str: str):
    """Fetch 1-min intraday candles for a specific date."""
    import pandas as pd
    resp = requests.get(
        f"{_BASE}/historical-candle/intraday/{instrument_key}/1minute",
        headers=_headers(),
        params={"to_date": date_str},
        timeout=15,
    )
    if not resp.ok:
        return pd.DataFrame()
    candles = resp.json().get("data", {}).get("candles", [])
    if not candles:
        return pd.DataFrame()
    df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df.set_index("timestamp", inplace=True)
    return df[["open", "high", "low", "close", "volume"]].astype(float)

# Load BUY/SELL pairs
pairs = []
with open("trades.csv") as f:
    rows = list(csv.DictReader(f))

buys  = [r for r in rows if r["action"] == "BUY"  and r["coin"] and float(r.get("price",0)) > 0]
sells = [r for r in rows if r["action"] == "SELL" and r["coin"] and float(r.get("price",0)) > 0]

for buy in buys:
    coin      = buy["coin"]
    buy_price = float(buy["price"])
    buy_time  = datetime.strptime(buy["timestamp"], "%Y-%m-%d %H:%M:%S")
    sell = next((s for s in sells
                 if s["coin"] == coin
                 and datetime.strptime(s["timestamp"], "%Y-%m-%d %H:%M:%S") > buy_time
                 and float(s["price"]) > 0), None)
    if sell:
        pairs.append({
            "coin":        coin,
            "buy_price":   buy_price,
            "buy_time":    buy_time,
            "sell_price":  float(sell["price"]),
            "sell_time":   datetime.strptime(sell["timestamp"], "%Y-%m-%d %H:%M:%S"),
        })

# Get instrument keys
from data_fetcher import get_instruments_nse
instruments  = get_instruments_nse()
sym_to_key   = {i["trading_symbol"]: i["instrument_key"] for i in instruments}

print(f"\n{'='*100}")
print(f"{'Stock':<12} {'Entry':>7} {'ATR':>6} {'Stop':>7} {'TP':>7} {'ActExit':>8} {'SimExit':>8} {'ActNet':>9} {'SimNet':>9} {'SimReason'}")
print(f"{'='*100}")

total_actual = 0
total_sim    = 0

for p in pairs:
    coin      = p["coin"]
    ikey      = sym_to_key.get(coin)
    trade_date = p["buy_time"].strftime("%Y-%m-%d")

    if not ikey:
        print(f"{coin:<12} — instrument key not found")
        continue

    df = get_ohlcv_for_date(ikey, trade_date)
    if df.empty or len(df) < 10:
        print(f"{coin:<12} {trade_date} — no OHLCV data for this date")
        continue

    entry_price = p["buy_price"]
    qty         = math.floor(20000 / entry_price)
    if qty < 10:
        print(f"{coin:<12} qty={qty} < 10, skipped")
        continue

    ind = compute_indicators(df)
    atr = ind["atr"]

    actual_net = (p["sell_price"] - entry_price) * qty - calculate_charges(entry_price*qty, p["sell_price"]*qty)
    total_actual += actual_net

    # ATR viability check
    if atr > 0 and (atr * qty) < 81:
        print(f"{coin:<12} {entry_price:>7.2f} {atr:>6.3f} {'--':>7} {'--':>7} {p['sell_price']:>8.2f} {'BLOCKED':>8} {actual_net:>+9.2f} {'0.00':>9} ATR×qty=Rs{atr*qty:.1f} < 81")
        continue

    stop_price = round(entry_price - atr, 2)
    tp_price   = round(entry_price + atr * 2, 2)

    # Filter candles from entry time onward (same day only)
    entry_ts = p["buy_time"].replace(tzinfo=_IST)
    eod_ts   = entry_ts.replace(hour=15, minute=15, second=0)
    candles  = df[(df.index > entry_ts) & (df.index <= eod_ts)].copy()

    sim_exit_price = None
    sim_reason     = "EOD close"

    for ts, row in candles.iterrows():
        minutes_held = (ts - entry_ts).total_seconds() / 60

        if row["low"] <= stop_price:
            sim_exit_price = stop_price
            sim_reason     = f"stop({minutes_held:.0f}m)"
            break

        if row["high"] >= tp_price:
            sim_exit_price = tp_price
            sim_reason     = f"TP({minutes_held:.0f}m)"
            break

        if minutes_held >= 30:
            gross = (row["close"] - entry_price) * qty
            if gross - calculate_charges(entry_price*qty, row["close"]*qty) <= 0:
                sim_exit_price = row["close"]
                sim_reason     = f"time-exit({minutes_held:.0f}m)"
                break

    if sim_exit_price is None:
        if not candles.empty:
            sim_exit_price = float(candles.iloc[-1]["close"])
        else:
            sim_exit_price = entry_price
        sim_reason = "EOD"

    sim_net = (sim_exit_price - entry_price) * qty - calculate_charges(entry_price*qty, sim_exit_price*qty)
    total_sim += sim_net

    print(f"{coin:<12} {entry_price:>7.2f} {atr:>6.3f} {stop_price:>7.2f} {tp_price:>7.2f} {p['sell_price']:>8.2f} {sim_exit_price:>8.2f} {actual_net:>+9.2f} {sim_net:>+9.2f} {sim_reason}")

print(f"{'='*100}")
print(f"{'TOTAL':<12} {'':>7} {'':>6} {'':>7} {'':>7} {'':>8} {'':>8} {total_actual:>+9.2f} {total_sim:>+9.2f}")
print(f"\nActual P&L   : Rs{total_actual:+.2f}")
print(f"Strategy P&L : Rs{total_sim:+.2f}")
print(f"Improvement  : Rs{total_sim - total_actual:+.2f}")
