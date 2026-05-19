import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

import schedule

import config
from data_fetcher import (
    get_funds, get_instruments_nse, get_market_quotes_ltp,
    apply_liquidity_filter, apply_spread_filter, get_ohlcv, get_cached_price,
    update_price_cache, get_nifty_change,
)
from indicators import compute_indicators, score_and_rank, compress_packet, compute_opening_range
from news import fetch_headlines, match_headlines_to_symbols
from claude_engine import ClaudeEngine, _candidates_hash
from order_executor import OrderExecutor, load_state, save_state, calculate_charges
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
    return t.hour == 15 and t.minute >= 15


def is_too_late_to_buy(dt: datetime | None = None) -> bool:
    t = dt or ist_now()
    return t.hour > 14 or (t.hour == 14 and t.minute >= 30)


class Scheduler:
    def __init__(self, state_path: str = "state.json"):
        self._state_path = state_path
        self._engine = ClaudeEngine(config.ANTHROPIC_API_KEY)
        self._executor = OrderExecutor(paper_trade=config.PAPER_TRADE, state_path=state_path)
        self._notifier = Notifier(
            telegram_token=config.TELEGRAM_BOT_TOKEN,
            chat_id=config.TELEGRAM_CHAT_ID,
        )
        self._eod_closed_date = None  # prevents double-selling if cycle fires multiple times at 15:15+

    def run_cycle(self):
        try:
            if not is_market_open():
                return

            state = load_state(self._state_path)
            today = ist_now().date()

            if state.get("last_trade_date") != str(today):
                state["daily_realised_pnl"] = 0.0
                state["last_trade_date"] = str(today)
                state["recently_sold"] = {}
                save_state(state, self._state_path)

            if is_eod_close_time() and self._eod_closed_date != today:
                self._eod_closed_date = today
                self._eod_close(state)
                return

            self._trading_cycle(state)
        except Exception as e:
            log_trade("ERROR", None, 0, 0, "", 0, str(e))
            self._notifier.send(f"Bot error: {e}")

    def _eod_close(self, state: dict):
        pos = state.get("position")
        if not pos:
            return
        try:
            quotes = get_market_quotes_ltp([pos["instrument_key"]])
            current_price = float(quotes.get(pos["instrument_key"], {}).get("last_price", 0)) or get_cached_price(pos["instrument_key"]) or pos["entry_price"]
            sell_value = current_price * pos["qty"]
            charges = calculate_charges(sell_value)
            new_state = self._executor.execute_sell(
                pos["stock"], price=current_price, qty=pos["qty"], state=state, reason="EOD forced close"
            )
            save_state(new_state, self._state_path)
            balance_after = state.get("cash", 0) + sell_value - charges
            log_trade("SELL", pos["stock"], round(sell_value, 2), current_price, "EOD forced close", round(balance_after, 2))
            self._notifier.send(f"EOD close: sold {pos['stock']} @ ₹{current_price:.2f}")
        except Exception as e:
            self._notifier.send(f"EOD CLOSE FAILED for {pos['stock']}: {e} — manual intervention required")

    def _trading_cycle(self, state: dict):
        pos = state.get("position")
        pos_current_price = None

        if pos:
            try:
                live_quotes = get_market_quotes_ltp([pos["instrument_key"]])
                live_price = float((live_quotes.get(pos["instrument_key"]) or {}).get("last_price", 0))
                if live_price > 0:
                    update_price_cache(pos["instrument_key"], live_price)
                    pos_current_price = live_price
                else:
                    pos_current_price = get_cached_price(pos["instrument_key"]) or pos["entry_price"]
            except Exception:
                pos_current_price = get_cached_price(pos["instrument_key"]) or pos["entry_price"]

            if self._executor.check_circuit_breaker(pos["instrument_key"]):
                self._notifier.send(f"WARNING: {pos['stock']} appears circuit-locked")
                return

            if self._executor.check_stop_loss(pos_current_price, state):
                sell_value = pos_current_price * pos["qty"]
                charges = calculate_charges(sell_value)
                new_state = self._executor.execute_sell(
                    pos["stock"], pos_current_price, pos["qty"], state, reason="stop-loss triggered"
                )
                save_state(new_state, self._state_path)
                pnl = (pos_current_price - pos["entry_price"]) * pos["qty"]
                balance_after = state.get("cash", 0) + sell_value - charges
                log_trade("SELL", pos["stock"], sell_value, pos_current_price, "stop-loss", round(balance_after, 2))
                self._notifier.send(
                    self._notifier.format_trade("SELL", pos["stock"], pos["qty"], pos_current_price,
                                                reasoning="stop-loss triggered", pnl=pnl)
                )
                return

        if self._executor.is_daily_loss_limit_reached(state):
            pnl = state.get("daily_realised_pnl", 0.0)
            log_trade("HOLD", None, 0, 0, f"daily loss limit reached (Rs{pnl:.2f}) — no trading until tomorrow", state.get("cash", 0))
            return

        instruments = get_instruments_nse()
        key_to_sym = {i["instrument_key"]: i["trading_symbol"] for i in instruments}
        keys = list(key_to_sym.keys())
        quotes = get_market_quotes_ltp(keys)
        liquid_keys = apply_liquidity_filter(quotes)
        filtered_keys = apply_spread_filter({k: quotes[k] for k in liquid_keys})
        filtered_keys.sort(key=lambda k: float(quotes[k].get("volume", 0)), reverse=True)

        seen_hashes = set(state.get("seen_headline_hashes", []))
        all_entries, new_hashes = fetch_headlines(seen_hashes)

        candidate_keys = filtered_keys[:config.TOP_CANDIDATES * 2]
        symbols = [key_to_sym.get(k, quotes[k].get("symbol", "")) for k in candidate_keys]
        headlines_map = match_headlines_to_symbols(all_entries, symbols)

        def _fetch_one(key):
            sym = key_to_sym.get(key, quotes[key].get("symbol", ""))
            if not sym:
                return None
            try:
                df = get_ohlcv(key)
                if df.empty or len(df) < 5:
                    return None
                ind = compute_indicators(df)
                orb = compute_opening_range(df)
            except Exception:
                return None
            q = quotes[key]
            price = float(q.get("last_price", 0))
            if price <= 0:
                return None
            depth = q.get("depth", {})
            buys = depth.get("buy", [])
            sells = depth.get("sell", [])
            best_bid = float(buys[0].get("price", price)) if buys else price
            best_ask = float(sells[0].get("price", price)) if sells else price
            spread_pct = (best_ask - best_bid) / price * 100 if price > 0 else 0.5
            return sym, {
                **ind,
                **orb,
                "price": price,
                "instrument_key": key,
                "spread_pct": spread_pct,
            }

        candidates_data = {}
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {pool.submit(_fetch_one, k): k for k in candidate_keys}
            for fut in as_completed(futures):
                result = fut.result()
                if result and len(candidates_data) < config.TOP_CANDIDATES:
                    sym, data = result
                    candidates_data[sym] = data

        # Strip stocks still in re-buy cooldown before Claude sees them
        recently_sold = state.get("recently_sold", {})
        _now = ist_now()
        candidates_data = {
            sym: data for sym, data in candidates_data.items()
            if sym not in recently_sold or
            (_now - __import__("datetime").datetime.fromisoformat(recently_sold[sym]["at"])).total_seconds() / 60 >= config.REBUY_COOLDOWN_MINUTES
        }

        ranked = score_and_rank(candidates_data, n=config.TOP_CANDIDATES)
        compressed = {sym: compress_packet(sym, data, headlines_map.get(sym, [])) for sym, data in ranked.items()}

        cash = get_funds()
        nifty_change = get_nifty_change()
        if pos:
            fresh = float((quotes.get(pos["instrument_key"]) or {}).get("last_price", 0))
            pos_current_price = fresh if fresh > 0 else (pos_current_price or pos["entry_price"])
            buy_val = pos["entry_price"] * pos["qty"]
            sv = pos_current_price * pos["qty"]
            charges = calculate_charges(buy_val, sv)
            net_pnl = (pos_current_price - pos["entry_price"]) * pos["qty"] - charges
            pos_for_claude = {**pos, "current_price": pos_current_price, "net_pnl_inr": round(net_pnl, 2)}

            # Take-profit auto-sell
            take_profit = pos.get("take_profit_price")
            if take_profit and pos_current_price >= take_profit:
                new_state = self._executor.execute_sell(pos["stock"], pos_current_price, pos["qty"], state, "take-profit hit")
                new_state["recently_sold"] = {**state.get("recently_sold", {}), pos["stock"]: {"price": pos_current_price, "at": ist_now().isoformat()}}
                save_state(new_state, self._state_path)
                balance_after = cash + sv - charges
                log_trade("SELL", pos["stock"], round(sv, 2), pos_current_price,
                          f"take-profit hit @ Rs{take_profit:.2f} (2:1 R/R)", round(balance_after, 2))
                self._notifier.send(
                    self._notifier.format_trade("SELL", pos["stock"], pos["qty"], pos_current_price,
                                                reasoning=f"take-profit hit @ Rs{take_profit:.2f}", pnl=net_pnl)
                )
                return

        else:
            pos_for_claude = pos
        portfolio = {"cash": cash, "position": pos_for_claude, "nifty_change": nifty_change}

        # Skip Claude if no candidate has any meaningful signal (saves API cost)
        has_signal = pos or any(
            d["volume_spike"] >= 1.5 or abs(d["rsi"] - 50) >= 12 or abs(d["vwap_pct"]) >= 0.5
            for d in candidates_data.values()
        )
        if not has_signal:
            new_state = {**state, "cash": cash, "seen_headline_hashes": list(seen_hashes | new_hashes)[-500:]}
            save_state(new_state, self._state_path)
            log_trade("HOLD", None, 0, 0, "no signal — skipped Claude", cash)
            return

        decision, new_state = self._engine.decide(compressed, portfolio, state)

        new_state["seen_headline_hashes"] = list(seen_hashes | new_hashes)[-500:]
        new_state["cash"] = cash
        save_state(new_state, self._state_path)

        if decision.action == "BUY" and not pos:
            stock_data = candidates_data.get(decision.stock, {})
            price = stock_data.get("price", 0)
            ikey = stock_data.get("instrument_key", "")
            block_reason = None
            atr = stock_data.get("atr", 0)
            qty_est = math.floor(cash / price) if price > 0 else 0
            if price <= 0:
                block_reason = f"price=0 ({decision.stock} missing from candidates_data)"
            elif price > 1000:
                block_reason = f"price Rs{price:.2f} exceeds Rs1000 limit"
            elif atr > 0 and qty_est > 0 and (atr * qty_est) < 81:
                block_reason = f"ATR×qty=Rs{atr*qty_est:.1f} too small vs charges — net R/R would be negative"
            elif not (stock_data.get("volume_spike", 1.0) >= 1.5 or
                      abs(stock_data.get("rsi", 50.0) - 50) >= 12 or
                      abs(stock_data.get("vwap_pct", 0.0)) >= 0.5):
                block_reason = (f"no momentum: vol_spike={stock_data.get('volume_spike',1):.1f}x "
                                f"RSI={stock_data.get('rsi',50):.0f} VWAP={stock_data.get('vwap_pct',0):+.1f}%")
            elif decision.stock in state.get("recently_sold", {}):
                sold_info = state["recently_sold"][decision.stock]
                sold_at = sold_info.get("at", "")
                minutes_since_sold = (ist_now() - __import__("datetime").datetime.fromisoformat(sold_at)).total_seconds() / 60 if sold_at else 999
                if minutes_since_sold < config.REBUY_COOLDOWN_MINUTES:
                    block_reason = f"re-buy cooldown: {decision.stock} sold {minutes_since_sold:.0f} min ago, wait {config.REBUY_COOLDOWN_MINUTES} min"
                else:
                    day_high = stock_data.get("day_high", 0)
                    room_to_high = (day_high - price) if day_high > price else 0
                    min_move = 54 / qty_est if qty_est > 0 else float("inf")
                    if room_to_high < min_move * 2:
                        block_reason = f"re-buy blocked: only Rs{room_to_high:.2f} room to day_high Rs{day_high:.2f}, need Rs{min_move*2:.2f} — upside too limited"
            elif is_too_late_to_buy():
                block_reason = "too late to buy (>= 14:30 IST)"
            elif self._executor.check_circuit_breaker(ikey):
                block_reason = f"{decision.stock} appears circuit-locked"
            if block_reason is None:
                new_state = self._executor.execute_buy(decision.stock, price, cash, new_state, instrument_key=ikey, atr=atr)
                save_state(new_state, self._state_path)
                bought_qty = (new_state.get("position") or {}).get("qty", 0)
                tp = (new_state.get("position") or {}).get("take_profit_price", 0)
                sl = (new_state.get("position") or {}).get("stop_price", 0)
                log_trade("BUY", decision.stock, round(price * bought_qty, 2), price,
                          f"{decision.reasoning} | stop=Rs{sl:.2f} take-profit=Rs{tp:.2f}", cash,
                          confidence=decision.confidence)
                self._notifier.send(
                    self._notifier.format_trade("BUY", decision.stock, price=price,
                                                confidence=decision.confidence, reasoning=decision.reasoning)
                )
            else:
                log_trade("HOLD", decision.stock, 0, 0, f"BUY rejected: {block_reason} | claude: {decision.reasoning}", cash)
                self._notifier.send(f"BUY rejected for {decision.stock}: {block_reason}")
        elif decision.action == "SELL" and pos:
            current_price = get_cached_price(pos["instrument_key"]) or pos["entry_price"]
            take_profit = pos.get("take_profit_price", 0)
            at_target = take_profit > 0 and current_price >= take_profit
            if decision.confidence < config.MIN_SELL_CONFIDENCE_THRESHOLD and not at_target:
                log_trade("HOLD", pos["stock"], 0, 0,
                          f"SELL blocked: confidence {decision.confidence:.2f} < {config.MIN_SELL_CONFIDENCE_THRESHOLD} and price Rs{current_price:.2f} not at 2:1 target Rs{take_profit:.2f} | {decision.reasoning}", cash)
                self._notifier.send(f"SELL blocked: {pos['stock']} conf={decision.confidence:.2f} below {config.MIN_SELL_CONFIDENCE_THRESHOLD} sell threshold, not at TP Rs{take_profit:.2f}")
            else:
                sell_value = current_price * pos["qty"]
                charges = calculate_charges(sell_value)
                gross_pnl = (current_price - pos["entry_price"]) * pos["qty"]
                net_pnl = gross_pnl - charges
                new_state = self._executor.execute_sell(pos["stock"], current_price, pos["qty"], new_state, decision.reasoning)
                new_state["recently_sold"] = {**state.get("recently_sold", {}), pos["stock"]: {"price": current_price, "at": ist_now().isoformat()}}
                save_state(new_state, self._state_path)
                balance_after = cash + sell_value - charges
                log_trade("SELL", pos["stock"], round(sell_value, 2), current_price, decision.reasoning, round(balance_after, 2),
                          confidence=decision.confidence)
                self._notifier.send(
                    self._notifier.format_trade("SELL", pos["stock"], pos["qty"], current_price,
                                                confidence=decision.confidence, reasoning=decision.reasoning, pnl=net_pnl)
                )
        else:
            log_trade("HOLD", decision.stock, 0, 0, decision.reasoning, cash, confidence=decision.confidence)
            self._notifier.send(
                f"HOLD | conf={decision.confidence:.2f} | {decision.reasoning}"
            )

    def start(self):
        if not auth.validate_token(config.UPSTOX_ACCESS_TOKEN):
            self._notifier.send("Upstox token invalid — run auth.py and restart")
            raise SystemExit("Invalid Upstox token")
        state = load_state(self._state_path)
        state["cash"] = get_funds()
        save_state(state, self._state_path)
        print("Trading bot started.")
        self.run_cycle()
        schedule.every(config.CYCLE_INTERVAL_MINUTES).minutes.do(self.run_cycle)
        while True:
            try:
                schedule.run_pending()
            except Exception as e:
                log_trade("ERROR", None, 0, 0, "", 0, f"scheduler error: {e}")
            time.sleep(30)
