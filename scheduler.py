import time
from datetime import datetime, timezone, timedelta

import schedule

import config
from data_fetcher import get_funds, get_instruments_nse, get_market_quotes_ltp, apply_liquidity_filter, apply_spread_filter
from indicators import compute_indicators, score_and_rank, compress_packet
from news import fetch_headlines, match_headlines_to_symbols
from claude_engine import ClaudeEngine, _candidates_hash
from order_executor import OrderExecutor, load_state, save_state
from notifier import Notifier
from logger import log_trade
import auth

_IST = timezone(timedelta(hours=5, minutes=30))


def ist_now() -> datetime:
    return datetime.now(_IST)


def is_market_open(dt: datetime | None = None) -> bool:
    t = dt or ist_now()
    if t.weekday() >= 5:
        return False
    open_time = t.replace(hour=9, minute=15, second=0, microsecond=0)
    close_time = t.replace(hour=15, minute=15, second=0, microsecond=0)
    return open_time <= t < close_time


def is_eod_close_time(dt: datetime | None = None) -> bool:
    t = dt or ist_now()
    return t.hour == 15 and t.minute == 15


class Scheduler:
    def __init__(self, state_path: str = "state.json"):
        self._state_path = state_path
        self._engine = ClaudeEngine(config.ANTHROPIC_API_KEY)
        self._executor = OrderExecutor(paper_trade=config.PAPER_TRADE, state_path=state_path)
        self._notifier = Notifier(
            telegram_token=config.TELEGRAM_BOT_TOKEN,
            chat_id=config.TELEGRAM_CHAT_ID,
            twilio_sid=config.TWILIO_ACCOUNT_SID,
            twilio_token=config.TWILIO_AUTH_TOKEN,
            whatsapp_from=config.TWILIO_WHATSAPP_FROM,
        )

    def run_cycle(self):
        if not is_market_open():
            return

        state = load_state(self._state_path)

        if is_eod_close_time():
            self._eod_close(state)
            return

        try:
            self._trading_cycle(state)
        except Exception as e:
            log_trade("ERROR", None, 0, 0, "", 0, str(e))
            self._notifier.send(f"Bot error: {e}")

    def _eod_close(self, state: dict):
        pos = state.get("position")
        if not pos:
            return
        try:
            new_state = self._executor.execute_sell(
                pos["stock"], price=0.0, qty=pos["qty"], state=state, reason="EOD forced close"
            )
            save_state(new_state, self._state_path)
            log_trade("SELL", pos["stock"], 0, 0, "EOD forced close", 0)
            self._notifier.send(f"EOD close: sold {pos['stock']}")
        except Exception as e:
            self._notifier.send(f"EOD CLOSE FAILED for {pos['stock']}: {e} — manual intervention required")

    def _trading_cycle(self, state: dict):
        pos = state.get("position")

        if pos:
            from data_fetcher import get_cached_price
            current_price = get_cached_price(pos["instrument_key"]) or pos["entry_price"]

            if self._executor.check_circuit_breaker(pos["instrument_key"]):
                self._notifier.send(f"WARNING: {pos['stock']} appears circuit-locked")
                return

            if self._executor.check_stop_loss(current_price, state):
                new_state = self._executor.execute_sell(
                    pos["stock"], current_price, pos["qty"], state, reason="stop-loss triggered"
                )
                save_state(new_state, self._state_path)
                pnl = (current_price - pos["entry_price"]) * pos["qty"]
                log_trade("SELL", pos["stock"], current_price * pos["qty"], current_price, "stop-loss", 0)
                self._notifier.send(
                    self._notifier.format_trade("SELL", pos["stock"], pos["qty"], current_price,
                                                reasoning="stop-loss triggered", pnl=pnl)
                )
                return

        if self._executor.is_daily_loss_limit_reached(state):
            return

        instruments = get_instruments_nse()
        keys = [i["instrument_key"] for i in instruments[:200]]
        quotes = get_market_quotes_ltp(keys)
        liquid_keys = apply_liquidity_filter(quotes)
        filtered_keys = apply_spread_filter({k: quotes[k] for k in liquid_keys})

        seen_hashes = set(state.get("seen_headline_hashes", []))
        _, new_hashes = fetch_headlines(seen_hashes)

        candidates_data = {}
        for key in filtered_keys[:config.TOP_CANDIDATES]:
            sym = key.split("|")[-1]
            candidates_data[sym] = {
                "price": float(quotes[key].get("last_price", 0)),
                "volume_spike": 1.0,
                "rsi": 50.0,
                "macd_signal": 0.0,
                "vwap_pct": 0.0,
                "spread_pct": 0.1,
                "bb_position": 0.5,
                "atr": 1.0,
            }

        compressed = {sym: compress_packet(sym, data, []) for sym, data in candidates_data.items()}
        cash = get_funds()
        portfolio = {"cash": cash, "position": pos}
        decision, new_state = self._engine.decide(compressed, portfolio, state)

        new_state["seen_headline_hashes"] = list(seen_hashes | new_hashes)
        save_state(new_state, self._state_path)

        if decision.action == "BUY" and not pos:
            price = candidates_data.get(decision.stock, {}).get("price", 0)
            if price > 0:
                new_state = self._executor.execute_buy(decision.stock, price, cash, new_state)
                save_state(new_state, self._state_path)
                log_trade("BUY", decision.stock, price, price, decision.reasoning, cash)
                self._notifier.send(
                    self._notifier.format_trade("BUY", decision.stock, price=price,
                                                confidence=decision.confidence, reasoning=decision.reasoning)
                )
        elif decision.action == "SELL" and pos:
            from data_fetcher import get_cached_price
            current_price = get_cached_price(pos["instrument_key"]) or pos["entry_price"]
            new_state = self._executor.execute_sell(pos["stock"], current_price, pos["qty"], new_state, decision.reasoning)
            save_state(new_state, self._state_path)
            pnl = (current_price - pos["entry_price"]) * pos["qty"]
            log_trade("SELL", pos["stock"], current_price * pos["qty"], current_price, decision.reasoning, 0)
            self._notifier.send(
                self._notifier.format_trade("SELL", pos["stock"], pos["qty"], current_price,
                                            confidence=decision.confidence, reasoning=decision.reasoning, pnl=pnl)
            )

    def start(self):
        if not auth.validate_token(config.UPSTOX_ACCESS_TOKEN):
            self._notifier.send("Upstox token invalid — run auth.py and restart")
            raise SystemExit("Invalid Upstox token")
        print("Trading bot started.")
        self.run_cycle()
        schedule.every(config.CYCLE_INTERVAL_MINUTES).minutes.do(self.run_cycle)
        while True:
            schedule.run_pending()
            time.sleep(30)
