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
    return t.hour >= 15

def is_late_session(dt: datetime | None = None) -> bool:
    """14:30–15:00 window: stricter momentum required before allowing a BUY."""
    t = dt or ist_now()
    return t.hour == 14 and t.minute >= 30


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
        self._needs_claude_call = False   # set by fast loop when position needs urgent review
        self._last_fast_price = {}        # instrument_key -> price at last fast check
        self._rescan_needed = False       # set after any SELL to trigger immediate BUY rescan
        self._peak_pnl = {}               # instrument_key -> highest net_pnl seen while in profit
        self._vol_dry_count = {}          # instrument_key -> consecutive low vol_spike readings

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
                state["claude_spend_usd"] = 0.0
                state["last_decision"] = {}
                state["last_candidates_hash"] = ""
                state["last_nifty_change"] = None
                save_state(state, self._state_path)

            if is_eod_close_time() and self._eod_closed_date != today:
                self._eod_closed_date = today
                self._eod_close(state)
                return

            self._rescan_needed = False
            self._trading_cycle(state)
            if self._rescan_needed and is_market_open() and not is_eod_close_time() and not is_too_late_to_buy():
                self._rescan_needed = False
                self._trading_cycle(load_state(self._state_path))
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
            new_state["last_decision"] = {}
            new_state["last_nifty_change"] = None
            save_state(new_state, self._state_path)
            balance_after = state.get("cash", 0) + sell_value - charges
            log_trade("SELL", pos["stock"], round(sell_value, 2), current_price, "EOD forced close", round(balance_after, 2))
            self._notifier.send(f"EOD close: sold {pos['stock']} @ ₹{current_price:.2f}")
        except Exception as e:
            self._notifier.send(f"EOD CLOSE FAILED for {pos['stock']}: {e} — manual intervention required")

    def _fast_price_check(self):
        """1-minute loop: monitors open position for stop-loss, take-profit, and P&L shifts.
        Sets _needs_claude_call=True when price moves >=0.5% or P&L turns positive."""
        try:
            if not is_market_open():
                return
            state = load_state(self._state_path)
            pos = state.get("position")
            if not pos:
                return

            try:
                live_quotes = get_market_quotes_ltp([pos["instrument_key"]])
                live_price = float((live_quotes.get(pos["instrument_key"]) or {}).get("last_price", 0))
                if live_price > 0:
                    update_price_cache(pos["instrument_key"], live_price)
                else:
                    live_price = get_cached_price(pos["instrument_key"]) or pos["entry_price"]
            except Exception:
                live_price = get_cached_price(pos["instrument_key"]) or pos["entry_price"]

            # Trailing stop
            _atr = pos.get("atr", 0)
            if _atr > 0:
                _entry = pos["entry_price"]
                _peak = pos.get("peak_price", _entry)
                if live_price > _peak:
                    _peak = live_price
                    pos["peak_price"] = _peak
                    state["position"] = pos
                _gain = _peak - _entry
                if _gain >= _atr:
                    _new_stop = round(_peak - _atr * 0.5, 2)
                elif _gain >= _atr * 0.5:
                    _new_stop = round(_entry, 2)
                else:
                    _new_stop = None
                if _new_stop and _new_stop > pos.get("stop_price", 0):
                    _old_stop = pos["stop_price"]
                    pos["stop_price"] = _new_stop
                    state["position"] = pos
                    save_state(state, self._state_path)
                    self._notifier.send(
                        f"Trailing stop: {pos['stock']} Rs{_old_stop:.2f}→Rs{_new_stop:.2f} (peak Rs{_peak:.2f})"
                    )

            # Stop-loss
            if self._executor.check_stop_loss(live_price, state):
                sell_value = live_price * pos["qty"]
                charges = calculate_charges(sell_value)
                new_state = self._executor.execute_sell(
                    pos["stock"], live_price, pos["qty"], state, reason="stop-loss triggered"
                )
                new_state["last_decision"] = {}
                new_state["last_nifty_change"] = None
                save_state(new_state, self._state_path)
                pnl = (live_price - pos["entry_price"]) * pos["qty"]
                balance_after = state.get("cash", 0) + sell_value - charges
                log_trade("SELL", pos["stock"], round(sell_value, 2), live_price,
                          "stop-loss triggered [1m]", round(balance_after, 2))
                self._notifier.send(
                    self._notifier.format_trade("SELL", pos["stock"], pos["qty"], live_price,
                                                reasoning="stop-loss triggered", pnl=pnl)
                )
                self._needs_claude_call = False
                self._last_fast_price.pop(pos["instrument_key"], None)
                self._peak_pnl.pop(pos["instrument_key"], None)
                self._vol_dry_count.pop(pos["instrument_key"], None)
                if is_market_open() and not is_eod_close_time() and not is_too_late_to_buy():
                    self.run_cycle()
                return

            # Take-profit
            take_profit = pos.get("take_profit_price")
            if take_profit and live_price >= take_profit:
                sell_value = live_price * pos["qty"]
                charges = calculate_charges(sell_value)
                net_pnl = (live_price - pos["entry_price"]) * pos["qty"] - charges
                new_state = self._executor.execute_sell(pos["stock"], live_price, pos["qty"], state, "take-profit hit")
                new_state["recently_sold"] = {**state.get("recently_sold", {}), pos["stock"]: {"price": live_price, "at": ist_now().isoformat()}}
                new_state["last_decision"] = {}
                new_state["last_nifty_change"] = None
                save_state(new_state, self._state_path)
                balance_after = state.get("cash", 0) + sell_value - charges
                log_trade("SELL", pos["stock"], round(sell_value, 2), live_price,
                          f"take-profit hit @ Rs{take_profit:.2f} [1m]", round(balance_after, 2))
                self._notifier.send(
                    self._notifier.format_trade("SELL", pos["stock"], pos["qty"], live_price,
                                                reasoning=f"take-profit hit @ Rs{take_profit:.2f}", pnl=net_pnl)
                )
                self._needs_claude_call = False
                self._last_fast_price.pop(pos["instrument_key"], None)
                self._peak_pnl.pop(pos["instrument_key"], None)
                self._vol_dry_count.pop(pos["instrument_key"], None)
                if is_market_open() and not is_eod_close_time() and not is_too_late_to_buy():
                    self.run_cycle()
                return

            # P&L and price-move assessment
            buy_val = pos["entry_price"] * pos["qty"]
            sv = live_price * pos["qty"]
            charges = calculate_charges(buy_val, sv)
            net_pnl = (live_price - pos["entry_price"]) * pos["qty"] - charges

            last_fast = self._last_fast_price.get(pos["instrument_key"], pos["entry_price"])
            move_pct = abs(live_price - last_fast) / last_fast * 100 if last_fast > 0 else 0
            self._last_fast_price[pos["instrument_key"]] = live_price

            ikey = pos["instrument_key"]
            if net_pnl > 0:
                peak = self._peak_pnl.get(ikey, 0)
                if net_pnl > peak:
                    self._peak_pnl[ikey] = net_pnl  # profit growing — update peak, hold
                trail_threshold = 0.90 if pos.get("tight_trail") else 0.75
                if peak > 54 and net_pnl < peak * trail_threshold:
                    # Profit dropped 25%+ from peak — trailing profit stop triggered
                    sell_value = live_price * pos["qty"]
                    charges = calculate_charges(sell_value)
                    new_state = self._executor.execute_sell(
                        pos["stock"], live_price, pos["qty"], state,
                        reason=f"trailing profit stop: peak Rs{peak:.2f} → now Rs{net_pnl:.2f} [1m]"
                    )
                    new_state["recently_sold"] = {**state.get("recently_sold", {}), pos["stock"]: {"price": live_price, "at": ist_now().isoformat()}}
                    new_state["last_decision"] = {}
                    new_state["last_nifty_change"] = None
                    save_state(new_state, self._state_path)
                    balance_after = state.get("cash", 0) + sell_value - charges
                    log_trade("SELL", pos["stock"], round(sell_value, 2), live_price,
                              f"trailing profit stop — peak Rs{peak:.2f} dropped to Rs{net_pnl:.2f} [1m]",
                              round(balance_after, 2))
                    self._notifier.send(
                        self._notifier.format_trade("SELL", pos["stock"], pos["qty"], live_price,
                                                    reasoning=f"trailing profit stop (peak Rs{peak:.2f})",
                                                    pnl=net_pnl)
                    )
                    self._peak_pnl.pop(ikey, None)
                    self._vol_dry_count.pop(ikey, None)
                    self._needs_claude_call = False
                    if is_market_open() and not is_eod_close_time() and not is_too_late_to_buy():
                        self.run_cycle()
                    return
            else:
                self._peak_pnl.pop(ikey, None)  # reset peak if trade goes back negative

            # Volume dry-up detection: if vol_spike drops below 1.5x for 2 consecutive
            # 1-min checks, tighten the trailing profit stop from 25% to 10% drop from peak.
            # This lets the trailing stop still capture maximum profit while exiting sooner
            # if price starts reversing — no immediate sell.
            try:
                df_vd = get_ohlcv(pos["instrument_key"])
                if not df_vd.empty:
                    ind_vd = compute_indicators(df_vd)
                    cur_vs = ind_vd.get("volume_spike", 1.0)
                    entry_vs = pos.get("entry_vol_spike", 3.0)
                    if cur_vs < 1.5:
                        self._vol_dry_count[ikey] = self._vol_dry_count.get(ikey, 0) + 1
                    else:
                        self._vol_dry_count[ikey] = 0
                        if pos.get("tight_trail"):
                            pos["tight_trail"] = False
                            state["position"] = pos
                    if self._vol_dry_count.get(ikey, 0) >= 2 and not pos.get("tight_trail"):
                        pos["tight_trail"] = True
                        state["position"] = pos
                        log_trade("HOLD", pos["stock"], round(live_price * pos["qty"], 2), live_price,
                                  f"volume dry-up (vol={cur_vs:.1f}x, was {entry_vs:.1f}x) — trailing stop tightened to 10% [1m]",
                                  state.get("cash", 0))
                        self._notifier.send(
                            f"Vol dry-up: {pos['stock']} vol={cur_vs:.1f}x (entry {entry_vs:.1f}x) — trailing stop tightened"
                        )
            except Exception:
                pass

            if move_pct >= 0.5 and not self._needs_claude_call:
                self._needs_claude_call = True

            save_state(state, self._state_path)

        except Exception as e:
            log_trade("ERROR", None, 0, 0, "", 0, f"fast_price_check error: {e}")

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

            # Trailing stop: raise stop to protect gains as price moves in our favour.
            # Phase 1 (peak ≥ entry + 0.5×ATR): move stop to entry (breakeven protection).
            # Phase 2 (peak ≥ entry + 1×ATR): trail stop at peak - 0.5×ATR (lock in half the first ATR).
            _atr = pos.get("atr", 0)
            if _atr > 0 and pos_current_price:
                _entry = pos["entry_price"]
                _peak = pos.get("peak_price", _entry)
                if pos_current_price > _peak:
                    _peak = pos_current_price
                    pos["peak_price"] = _peak
                    state["position"] = pos
                _gain = _peak - _entry
                if _gain >= _atr:
                    _new_stop = round(_peak - _atr * 0.5, 2)
                elif _gain >= _atr * 0.5:
                    _new_stop = round(_entry, 2)
                else:
                    _new_stop = None
                if _new_stop and _new_stop > pos.get("stop_price", 0):
                    _old_stop = pos["stop_price"]
                    pos["stop_price"] = _new_stop
                    state["position"] = pos
                    save_state(state, self._state_path)
                    self._notifier.send(
                        f"Trailing stop: {pos['stock']} Rs{_old_stop:.2f}→Rs{_new_stop:.2f} (peak Rs{_peak:.2f})"
                    )

            if self._executor.check_circuit_breaker(pos["instrument_key"]):
                self._notifier.send(f"WARNING: {pos['stock']} appears circuit-locked")
                return

            if self._executor.check_stop_loss(pos_current_price, state):
                sell_value = pos_current_price * pos["qty"]
                charges = calculate_charges(sell_value)
                new_state = self._executor.execute_sell(
                    pos["stock"], pos_current_price, pos["qty"], state, reason="stop-loss triggered"
                )
                new_state["last_decision"] = {}
                new_state["last_nifty_change"] = None
                save_state(new_state, self._state_path)
                pnl = (pos_current_price - pos["entry_price"]) * pos["qty"]
                balance_after = state.get("cash", 0) + sell_value - charges
                log_trade("SELL", pos["stock"], sell_value, pos_current_price, "stop-loss", round(balance_after, 2))
                self._notifier.send(
                    self._notifier.format_trade("SELL", pos["stock"], pos["qty"], pos_current_price,
                                                reasoning="stop-loss triggered", pnl=pnl)
                )
                self._rescan_needed = True
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
                new_state["last_decision"] = {}
                new_state["last_nifty_change"] = None
                save_state(new_state, self._state_path)
                balance_after = cash + sv - charges
                log_trade("SELL", pos["stock"], round(sv, 2), pos_current_price,
                          f"take-profit hit @ Rs{take_profit:.2f} (2:1 R/R)", round(balance_after, 2))
                self._notifier.send(
                    self._notifier.format_trade("SELL", pos["stock"], pos["qty"], pos_current_price,
                                                reasoning=f"take-profit hit @ Rs{take_profit:.2f}", pnl=net_pnl)
                )
                self._rescan_needed = True
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

        # Skip Claude when no position and market context unchanged since last HOLD.
        # Call Claude only if: NIFTY moved ≥0.2% since last call OR 15 minutes have passed.
        if not pos and (state.get("last_decision") or {}).get("action") == "HOLD":
            try:
                _last_nifty = float(state.get("last_nifty_change", "nan"))
                _last_claude_time = state.get("last_claude_call_time")
                _minutes_since_claude = 999
                if _last_claude_time:
                    _last_dt = __import__("datetime").datetime.fromisoformat(_last_claude_time)
                    _minutes_since_claude = (ist_now() - _last_dt).total_seconds() / 60
                if abs(nifty_change - _last_nifty) < 0.2 and _minutes_since_claude < 15:
                    _d = state["last_decision"]
                    new_state = {**state, "cash": cash, "seen_headline_hashes": list(seen_hashes | new_hashes)[-500:]}
                    save_state(new_state, self._state_path)
                    log_trade("HOLD", None, 0, 0,
                              f"market unchanged — skipped Claude | {_d['reasoning']}", cash, confidence=_d["confidence"])
                    return
            except (ValueError, TypeError, KeyError):
                pass

        # Skip Claude when last SELL was blocked (below threshold) and price hasn't moved.
        # If confidence now meets threshold, fall through so it actually executes.
        if pos and (state.get("last_decision") or {}).get("action") == "SELL":
            try:
                _d = state["last_decision"]
                if _d.get("stock") == pos["stock"]:
                    _last_price = float(state.get("last_candidates_hash", "").split(":")[-1])
                    _move_pct = abs(pos_current_price - _last_price) / _last_price * 100
                    if _move_pct < 0.3 and float(_d.get("confidence", 0)) < config.MIN_SELL_CONFIDENCE_THRESHOLD:
                        _hold_amount = round(pos_current_price * pos["qty"], 2)
                        new_state = {**state, "cash": cash, "seen_headline_hashes": list(seen_hashes | new_hashes)[-500:]}
                        save_state(new_state, self._state_path)
                        log_trade("HOLD", pos["stock"], _hold_amount, pos_current_price,
                                  f"SELL pending — skipped Claude | {_d['reasoning']}", cash, confidence=_d["confidence"])
                        return
            except (ValueError, TypeError, KeyError):
                pass

        # Skip Claude when holding a stable position to preserve API budget.
        # Call Claude only if: price moved ≥0.3% since last call, OR within 0.5% of SL/TP.
        if pos and (state.get("last_decision") or {}).get("action") == "HOLD":
            try:
                _last_price = float(state.get("last_candidates_hash", "").split(":")[-1])
                _move_pct = abs(pos_current_price - _last_price) / _last_price * 100
                _stop_gap_pct = (pos_current_price - pos.get("stop_price", 0)) / pos_current_price * 100
                _tp_gap_pct = (pos.get("take_profit_price", pos_current_price * 2) - pos_current_price) / pos_current_price * 100
                if _move_pct < 0.3 and _stop_gap_pct > 0.5 and _tp_gap_pct > 0.5 and not self._needs_claude_call:
                    _d = state["last_decision"]
                    _hold_amount = round(pos_current_price * pos["qty"], 2)
                    new_state = {**state, "cash": cash, "seen_headline_hashes": list(seen_hashes | new_hashes)[-500:]}
                    save_state(new_state, self._state_path)
                    log_trade("HOLD", pos["stock"], _hold_amount, pos_current_price,
                              f"stable — skipped Claude | {_d['reasoning']}", cash, confidence=_d["confidence"])
                    return
            except (ValueError, TypeError, KeyError):
                pass

        self._needs_claude_call = False
        decision, new_state = self._engine.decide(compressed, portfolio, state)

        new_state["seen_headline_hashes"] = list(seen_hashes | new_hashes)[-500:]
        new_state["cash"] = cash
        new_state["last_nifty_change"] = nifty_change
        new_state["last_claude_call_time"] = ist_now().isoformat()
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
            elif stock_data.get("bb_position", 0) > 1.0:
                block_reason = f"overextension veto: bb_position={stock_data.get('bb_position'):.2f} > 1.0 (above upper band)"
            elif stock_data.get("rsi", 0) > 78:
                block_reason = f"overextension veto: RSI={stock_data.get('rsi'):.0f} > 78"
            elif not (stock_data.get("volume_spike", 1.0) >= 1.5 or
                      abs(stock_data.get("rsi", 50.0) - 50) >= 12 or
                      abs(stock_data.get("vwap_pct", 0.0)) >= 0.5):
                block_reason = (f"no momentum: vol_spike={stock_data.get('volume_spike',1):.1f}x "
                                f"RSI={stock_data.get('rsi',50):.0f} VWAP={stock_data.get('vwap_pct',0):+.1f}%")
            elif (stock_data.get("day_high", 0) > 0
                  and (stock_data.get("day_high", price) - price) / stock_data.get("day_high", price) < 0.005
                  and (price + 2 * atr) > stock_data.get("day_high", price) + atr
                  and stock_data.get("volume_spike", 0) < 10):
                day_high = stock_data.get("day_high", price)
                tp_price = price + 2 * atr
                block_reason = (f"near day high: price Rs{price:.2f} within 0.5% of day_high Rs{day_high:.2f}, "
                                f"take-profit Rs{tp_price:.2f} requires Rs{tp_price - day_high:.2f} beyond day high "
                                f"but vol_spike={stock_data.get('volume_spike',0):.1f}x < 10x — insufficient momentum to break resistance")
            elif (stock_data.get("vwap_pct", 0) < -0.5 and stock_data.get("rsi", 50) > 40
                  and stock_data.get("or_direction", "") != "DOWN"):
                block_reason = (f"VWAP divergence: price {stock_data.get('vwap_pct',0):+.1f}% below VWAP "
                                f"RSI={stock_data.get('rsi',50):.0f} or_dir={stock_data.get('or_direction','')} — no VWAP support, not oversold enough for bounce")
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
                block_reason = "too late to buy (>= 15:00 IST)"
            elif is_late_session():
                vol_ok = stock_data.get("volume_spike", 0) >= 3.0
                macd_ok = stock_data.get("macd_hist", 0) > 0
                or_high = stock_data.get("or_high", 0)
                broke_up = or_high > 0 and price > or_high
                if not (vol_ok and macd_ok and broke_up):
                    block_reason = (
                        f"late session (14:30–15:00): requires BROKE-UP + vol_spike>=3x + MACD>0 | "
                        f"vol={stock_data.get('volume_spike',0):.1f}x macd={stock_data.get('macd_hist',0):+.3f} broke_up={broke_up}"
                    )
            elif self._executor.check_circuit_breaker(ikey):
                block_reason = f"{decision.stock} appears circuit-locked"
            if block_reason is None:
                new_state = self._executor.execute_buy(decision.stock, price, cash, new_state, instrument_key=ikey, atr=atr)
                if new_state.get("position"):
                    new_state["position"]["entry_vol_spike"] = stock_data.get("volume_spike", 1.0)
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
                log_trade("HOLD", pos["stock"], round(current_price * pos["qty"], 2), current_price,
                          f"SELL blocked: confidence {decision.confidence:.2f} < {config.MIN_SELL_CONFIDENCE_THRESHOLD} and price Rs{current_price:.2f} not at 2:1 target Rs{take_profit:.2f} | {decision.reasoning}", cash)
                self._notifier.send(f"SELL blocked: {pos['stock']} conf={decision.confidence:.2f} below {config.MIN_SELL_CONFIDENCE_THRESHOLD} sell threshold, not at TP Rs{take_profit:.2f}")
            else:
                sell_value = current_price * pos["qty"]
                charges = calculate_charges(sell_value)
                gross_pnl = (current_price - pos["entry_price"]) * pos["qty"]
                net_pnl = gross_pnl - charges
                # Confirmation gate: losing position requires 2 consecutive SELL signals before executing.
                # Profitable positions execute immediately — no confirmation needed.
                if net_pnl < 0:
                    def_count = pos.get("defensive_sell_count", 0) + 1
                    if def_count < 2:
                        pos["defensive_sell_count"] = def_count
                        new_state["position"] = pos
                        save_state(new_state, self._state_path)
                        log_trade("HOLD", pos["stock"], round(current_price * pos["qty"], 2), current_price,
                                  f"defensive SELL signal {def_count}/2 — awaiting confirmation | {decision.reasoning}",
                                  cash, confidence=decision.confidence)
                        self._notifier.send(
                            f"SELL signal {def_count}/2 for {pos['stock']} (P&L Rs{net_pnl:+.2f}) — holding for confirmation next cycle"
                        )
                    else:
                        new_state = self._executor.execute_sell(pos["stock"], current_price, pos["qty"], new_state, decision.reasoning)
                        new_state["recently_sold"] = {**state.get("recently_sold", {}), pos["stock"]: {"price": current_price, "at": ist_now().isoformat()}}
                        new_state["last_decision"] = {}
                        new_state["last_nifty_change"] = None
                        save_state(new_state, self._state_path)
                        balance_after = cash + sell_value - charges
                        log_trade("SELL", pos["stock"], round(sell_value, 2), current_price,
                                  f"confirmed defensive exit (2/2) | {decision.reasoning}", round(balance_after, 2),
                                  confidence=decision.confidence)
                        self._notifier.send(
                            self._notifier.format_trade("SELL", pos["stock"], pos["qty"], current_price,
                                                        confidence=decision.confidence, reasoning=decision.reasoning, pnl=net_pnl)
                        )
                        self._rescan_needed = True
                else:
                    new_state = self._executor.execute_sell(pos["stock"], current_price, pos["qty"], new_state, decision.reasoning)
                    new_state["recently_sold"] = {**state.get("recently_sold", {}), pos["stock"]: {"price": current_price, "at": ist_now().isoformat()}}
                    new_state["last_decision"] = {}
                    new_state["last_nifty_change"] = None
                    save_state(new_state, self._state_path)
                    balance_after = cash + sell_value - charges
                    log_trade("SELL", pos["stock"], round(sell_value, 2), current_price, decision.reasoning, round(balance_after, 2),
                              confidence=decision.confidence)
                    self._notifier.send(
                        self._notifier.format_trade("SELL", pos["stock"], pos["qty"], current_price,
                                                    confidence=decision.confidence, reasoning=decision.reasoning, pnl=net_pnl)
                    )
                    self._rescan_needed = True
        else:
            if pos and pos.get("defensive_sell_count", 0) > 0:
                pos["defensive_sell_count"] = 0
                new_state["position"] = pos
                save_state(new_state, self._state_path)
            _hold_price = pos_current_price if pos and pos_current_price else 0
            _hold_amount = round(_hold_price * pos["qty"], 2) if pos and _hold_price else 0
            log_trade("HOLD", decision.stock, _hold_amount, _hold_price, decision.reasoning, cash, confidence=decision.confidence)
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
        from premarket import run_premarket_analysis
        print("Trading bot started.")
        schedule.every().day.at("08:30").do(
            lambda: run_premarket_analysis() if ist_now().weekday() < 5 else None
        )
        self.run_cycle()
        schedule.every(1).minutes.do(self._fast_price_check)
        schedule.every(config.CYCLE_INTERVAL_MINUTES).minutes.do(self.run_cycle)
        while True:
            try:
                schedule.run_pending()
            except Exception as e:
                log_trade("ERROR", None, 0, 0, "", 0, f"scheduler error: {e}")
            time.sleep(30)
