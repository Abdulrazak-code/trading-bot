import json
import math
import os
import time
from datetime import datetime, timezone, timedelta

_IST = timezone(timedelta(hours=5, minutes=30))

import requests

import config

_BASE = "https://api.upstox.com/v2"
_STOP_LOSS_PCT = config.STOP_LOSS_PCT / 100


def calculate_charges(buy_value: float, sell_value: float = None) -> float:
    """Return total Upstox round-trip charges based on actual Upstox rate card."""
    if sell_value is None:
        sell_value = buy_value
    brokerage = min(config.UPSTOX_FLAT_BROKERAGE_INR, buy_value * 0.001) + \
                min(config.UPSTOX_FLAT_BROKERAGE_INR, sell_value * 0.001)
    exchange = (buy_value + sell_value) * config.UPSTOX_EXCHANGE_CHARGE_PCT
    gst = (brokerage + exchange) * config.UPSTOX_GST_PCT
    stt = sell_value * config.UPSTOX_STT_SELL_PCT
    stamp_duty = buy_value * 0.00003  # 0.003% on buy side only
    return brokerage + exchange + gst + stt + stamp_duty


def _headers():
    return {"Authorization": f"Bearer {config.UPSTOX_ACCESS_TOKEN}", "Accept": "application/json"}


def load_state(path: str = "state.json") -> dict:
    if not os.path.exists(path):
        return {
            "position": None,
            "daily_realised_pnl": 0.0,
            "claude_spend_usd": 0.0,
            "last_candidates_hash": "",
            "last_decision": None,
            "seen_headline_hashes": [],
            "recently_sold": {},
        }
    with open(path) as f:
        return json.load(f)


def save_state(state: dict, path: str = "state.json"):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)


class OrderExecutor:
    def __init__(self, paper_trade: bool = False, state_path: str = "state.json"):
        self._paper = paper_trade
        self._state_path = state_path

    def has_open_position(self, state: dict) -> bool:
        return state.get("position") is not None

    def is_daily_loss_limit_reached(self, state: dict) -> bool:
        return state.get("daily_realised_pnl", 0.0) <= -config.MAX_DAILY_LOSS_INR

    def check_stop_loss(self, current_price: float, state: dict) -> bool:
        pos = state.get("position")
        if not pos:
            return False
        stop_price = pos.get("stop_price")
        if stop_price:
            return current_price <= stop_price
        drop_pct = (pos["entry_price"] - current_price) / pos["entry_price"]
        return drop_pct >= _STOP_LOSS_PCT

    def check_circuit_breaker(self, instrument_key: str) -> bool:
        """Returns True if stock appears circuit-locked (spread > 5% or near-zero depth)."""
        try:
            resp = requests.get(
                f"{_BASE}/market-quote/quotes",
                headers=_headers(),
                params={"instrument_key": instrument_key},
                timeout=10,
            )
            if not resp.ok:
                return False
            # Upstox keys this response by "EXCHANGE:SYMBOL" (e.g. NSE_EQ:PNB), not the instrument_key.
            q = next(iter(resp.json().get("data", {}).values()), {})
            price = float(q.get("last_price", 0))
            if price <= 0:
                return False
            depth = q.get("depth", {})
            buys = depth.get("buy") or []
            sells = depth.get("sell") or []
            if not buys or not sells:
                return False
            best_bid = float(buys[0].get("price", 0))
            best_ask = float(sells[0].get("price", 0))
            if best_bid <= 0 or best_ask <= 0:
                return False
            spread_pct = (best_ask - best_bid) / price * 100
            total_bid_qty = sum(d.get("quantity", 0) for d in buys)
            return spread_pct > 5.0 or total_bid_qty < 100
        except Exception:
            return False

    def validate_mis_eligibility(self, instrument_key: str) -> bool:
        """Re-check MIS eligibility before placing a BUY."""
        try:
            resp = requests.get(
                f"{_BASE}/market-quote/ltp",
                headers=_headers(),
                params={"instrument_key": instrument_key},
                timeout=10,
            )
            return resp.ok
        except Exception:
            return False

    def _place_order(self, instrument_key: str, side: str, qty: int, order_type: str = "MARKET") -> dict:
        body = {
            "quantity": qty,
            "product": "I",
            "validity": "DAY",
            "price": 0,
            "tag": "trading-bot",
            "instrument_token": instrument_key,
            "order_type": order_type,
            "transaction_type": side.upper(),
            "disclosed_quantity": 0,
            "trigger_price": 0,
            "is_amo": False,
        }
        resp = requests.post(f"{_BASE}/order/place", headers=_headers(), json=body, timeout=15)
        if not resp.ok:
            raise requests.HTTPError(f"{resp.status_code}: {resp.text}", response=resp)
        return resp.json()["data"]

    def _get_filled_qty(self, order_id: str) -> int:
        resp = requests.get(
            f"{_BASE}/order/trades",
            headers=_headers(),
            params={"order_id": order_id},
            timeout=10,
        )
        if not resp.ok:
            return 0
        trades = resp.json().get("data", [])
        return int(sum(float(t.get("quantity", 0)) for t in trades))

    def execute_buy(self, symbol: str, price: float, cash: float, state: dict, instrument_key: str = None, atr: float = None) -> dict:
        # Stop distance (risk per share) determines position size.
        if atr and atr > 0:
            stop_distance = atr
            stop_price = round(price - atr, 2)
            take_profit_price = round(price + atr * 2, 2)
        else:
            stop_distance = price * _STOP_LOSS_PCT
            stop_price = round(price - stop_distance, 2)
            take_profit_price = round(price + stop_distance * 2, 2)

        # Risk-based sizing: lose at most RISK_PER_TRADE_INR if the stop hits,
        # capped by available cash. This replaces all-in full-capital sizing.
        risk_qty = math.floor(config.RISK_PER_TRADE_INR / stop_distance) if stop_distance > 0 else 0
        cash_qty = math.floor(cash / price)
        qty = min(risk_qty, cash_qty)
        if qty < 10:
            return state

        ikey = instrument_key or f"NSE_EQ|{symbol}"
        if not self._paper:
            if not self.validate_mis_eligibility(ikey):
                return state
            order = self._place_order(ikey, "BUY", qty)
            filled_qty = self._get_filled_qty(order["order_id"]) or qty
        else:
            filled_qty = qty

        return {
            **state,
            "position": {
                "stock": symbol,
                "entry_price": price,
                "qty": filled_qty,
                "instrument_key": ikey,
                "stop_price": stop_price,
                "take_profit_price": take_profit_price,
                "entry_time": datetime.now(_IST).isoformat(),
                "atr": round(atr, 4) if atr else 0,
                "peak_price": price,
            },
        }

    def execute_sell(self, symbol: str, price: float, qty: int, state: dict, reason: str = "") -> dict:
        pos = state.get("position", {})
        entry = pos.get("entry_price", price) if pos else price
        sell_value = price * qty
        buy_value = entry * qty
        gross_pnl = (price - entry) * qty
        charges = calculate_charges(buy_value, sell_value)
        net_pnl = gross_pnl - charges

        if not self._paper:
            ikey = (state.get("position") or {}).get("instrument_key") or f"NSE_EQ|{symbol}"
            for attempt in range(2):
                try:
                    self._place_order(ikey, "SELL", qty)
                    break
                except Exception:
                    if attempt == 0:
                        time.sleep(30)
                    else:
                        raise

        return {
            **state,
            "position": None,
            "daily_realised_pnl": state.get("daily_realised_pnl", 0.0) + net_pnl,
        }
