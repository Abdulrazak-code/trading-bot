import json
import math
import os
import time

import requests

import config

_BASE = "https://api.upstox.com/v2"
_STOP_LOSS_PCT = config.STOP_LOSS_PCT / 100


def calculate_charges(sell_value: float) -> float:
    """Return total Upstox round-trip charges for a trade of the given sell value."""
    brokerage = config.UPSTOX_FLAT_BROKERAGE_INR * 2
    gst = brokerage * config.UPSTOX_GST_PCT
    stt = sell_value * config.UPSTOX_STT_SELL_PCT
    exchange = sell_value * config.UPSTOX_EXCHANGE_CHARGE_PCT
    return brokerage + gst + stt + exchange


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
        drop_pct = (pos["entry_price"] - current_price) / pos["entry_price"]
        return drop_pct >= _STOP_LOSS_PCT

    def check_circuit_breaker(self, instrument_key: str) -> bool:
        """Returns True if stock appears circuit-locked (spread > 5% or near-zero depth)."""
        try:
            resp = requests.get(
                f"{_BASE}/market-quote/ltp",
                headers=_headers(),
                params={"instrument_key": instrument_key},
                timeout=10,
            )
            if not resp.ok:
                return False
            q = resp.json().get("data", {}).get(instrument_key, {})
            price = float(q.get("last_price", 1))
            depth = q.get("depth", {})
            buys = depth.get("buy", [{}])
            sells = depth.get("sell", [{}])
            best_bid = float(buys[0].get("price", price)) if buys else price
            best_ask = float(sells[0].get("price", price)) if sells else price
            spread_pct = (best_ask - best_bid) / price * 100 if price > 0 else 0
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
            "product": "MIS",
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

    def execute_buy(self, symbol: str, price: float, cash: float, state: dict) -> dict:
        qty = math.floor(cash / price)
        if qty < 1:
            return state

        if not self._paper:
            if not self.validate_mis_eligibility(f"NSE_EQ|{symbol}"):
                return state
            order = self._place_order(f"NSE_EQ|{symbol}", "BUY", qty)
            filled_qty = self._get_filled_qty(order["order_id"]) or qty
        else:
            filled_qty = qty

        return {
            **state,
            "position": {
                "stock": symbol,
                "entry_price": price,
                "qty": filled_qty,
                "instrument_key": f"NSE_EQ|{symbol}",
            },
        }

    def execute_sell(self, symbol: str, price: float, qty: int, state: dict, reason: str = "") -> dict:
        pos = state.get("position", {})
        entry = pos.get("entry_price", price) if pos else price
        sell_value = price * qty
        gross_pnl = (price - entry) * qty
        charges = calculate_charges(sell_value)
        net_pnl = gross_pnl - charges

        if not self._paper:
            for attempt in range(2):
                try:
                    self._place_order(f"NSE_EQ|{symbol}", "SELL", qty)
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
