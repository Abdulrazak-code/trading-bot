import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

import anthropic

import config

_IST = timezone(timedelta(hours=5, minutes=30))


def _load_premarket_report() -> str:
    """Load today's pre-market report if available, return as a context string."""
    try:
        if not os.path.exists("premarket_report.json"):
            return ""
        with open("premarket_report.json") as f:
            r = json.load(f)
        if r.get("date") != datetime.now(_IST).strftime("%Y-%m-%d"):
            return ""  # stale report from previous day
        stocks = ", ".join(r.get("stocks_to_watch", [])) or "none flagged"
        sectors = ", ".join(r.get("sectors_to_watch", [])) or "none flagged"
        return (
            f"PRE-MARKET CONTEXT (generated {r.get('generated_at')} IST):\n"
            f"  Outlook: {r.get('market_outlook')} | {r.get('expected_gap')}\n"
            f"  PCR: {r.get('pcr')} — {r.get('pcr_signal')}\n"
            f"  Max Pain: {r.get('max_pain')} — {r.get('max_pain_note')}\n"
            f"  Key OI levels: {r.get('top_oi_strikes')}\n"
            f"  Sectors to watch: {sectors}\n"
            f"  Flagged stocks: {stocks}\n"
            f"  Strategy: {r.get('strategy')}\n"
            f"  Risk: {r.get('risk_note')}"
        )
    except Exception:
        return ""

_SYSTEM_PROMPT = """You are an AI intraday stock trading assistant for NSE/BSE markets.
You receive the top 50 pre-filtered stock candidates every cycle and output a structured decision.

RULES:
- Trading capital: Rs{capital}. One position at a time. All positions are force-closed at 3:15 PM IST (market closes 3:30 PM but bot exits early to avoid closing auction). No new BUY after 3:00 PM. Between 2:30 PM and 3:00 PM (late session), only recommend BUY if the stock has BROKE-UP status, vol_spike >= 3x, and MACD_hist > 0 — all three required. Weak or IN-RANGE setups in this window should be HOLD.
- Round-trip cost: Rs45-50 per trade (~1.1-1.25% of capital). Only trade when expected gain clearly exceeds this.
- Confidence threshold: {threshold} minimum for BUY or SELL. Below {threshold}, return HOLD.
- If a position is already open, only SELL or HOLD are valid actions.
- Before recommending BUY, run this exact check: qty = floor(Rs{capital} / price), min_move = Rs54 / qty. ATR in the data is the average true range — the typical per-candle price move. ONLY BUY if ATR > min_move. If ATR <= min_move the stock cannot move enough to cover charges and the trade will lose money. Example: stock at Rs60, qty=333, min_move=Rs0.16 — ATR=0.25 passes, ATR=0.12 is rejected. Also require at least one momentum signal: vol_spike >= 1.5x OR RSI < 38 or > 62 OR VWAP deviation >= 0.5%. No signal = no trade.
- Do NOT recommend BUY for any stock priced above Rs1000. High-priced stocks give too few shares and require large per-share moves to recover costs.
- SELL rule: If net P&L is positive AND momentum is clearly reversing (MACD_hist turning negative, price falling from day high, vol_spike fading significantly), recommend SELL. Do NOT sell just because P&L is positive and small — if momentum is still intact (MACD positive, price near day high, vol_spike strong), HOLD and let the take-profit trigger automatically. The take-profit target is set at 2×ATR — trust it. Only override with early SELL when there is clear evidence the move is over, not just caution. If net P&L is negative, HOLD unless you have strong conviction the stock will fall further (defensive exit). Overbought RSI or upper Bollinger Band alone are NOT sufficient reason to sell a losing position.
- OVERSOLD REVERSAL HOLD RULE: If the trade was entered because RSI < 35 (oversold bounce), negative MACD_hist is EXPECTED and normal — the stock is still recovering from a selloff. Do NOT use negative MACD_hist as a reason to defensively exit an oversold reversal play. Only exit if: (a) price actually breaks below entry - 0.5×ATR (momentum clearly failed), or (b) stop-loss triggers automatically. If RSI < 35 and price is holding above entry, HOLD and let the reversal play out.
- The system also auto-sells at the 2:1 take-profit target and on stop-loss — but you should recommend SELL independently when you have >= {threshold} confidence, do not defer to those automatic triggers if you see clear exit signals.
- Each candidate shows: H=day_high L=day_low and %from_high. Price near day high (0 to -1%) with vol_spike means momentum is real. Price far from high (-3%+) means the move has already faded — avoid.
- MACD_hist is the histogram (MACD line minus signal line). Positive and growing = accelerating upward. Negative or shrinking = momentum dying.
- NIFTY context is provided. If NIFTY is down >0.5%, DO NOT buy breakouts or momentum moves — only consider deep oversold bounces where RSI < 38. Any other BUY signal in a down market will reverse. Return HOLD unless RSI < 38.
- Before recommending BUY, assess day potential: given minutes remaining until 3:15 PM and the stock's day range (day_high - day_low), estimate whether the stock can realistically move enough to hit profit. Use this check: expected_move = ATR * (minutes_remaining / 75). If expected_move < min_move, the stock likely cannot cover charges before close — HOLD. Also assess whether price is more likely to reach day_high again or has already peaked: if price is far below day_high (-3%+) with fading volume and negative MACD, the day high is unlikely to be revisited — avoid. If price is consolidating near day_high with strong volume, a breakout or retest is likely — favour BUY.
- DAY HIGH RESISTANCE: If price is within 0.5% of day_high (i.e., %from_high > -0.5%), the take-profit target (entry + 2×ATR) almost certainly requires breaking to a new day high and continuing higher. This is harder than a stock with room below its high. Only BUY in this zone if vol_spike > 10x AND MACD_hist is positive AND growing — this signals a genuine push through resistance. If vol_spike < 10x or MACD_hist is flat or declining, the stock is likely to stall at the day high and reverse — HOLD.
- OVEREXTENSION VETO: If bb_position > 1.05 OR RSI > 78, do NOT BUY — bb_position > 1.05 means price is significantly above the upper Bollinger Band. bb_position of 1.0-1.05 is just at the band edge and is acceptable on strong breakouts (vol_spike > 10x). These are hard veto conditions, not entry signals.
- OPENING RANGE BREAKOUT (OR): Each candidate includes OR= status. OR=BROKE-UP means price has broken above the 9:15–9:30 opening range high. OR=IN-RANGE means no breakout yet — weaker signal, require RSI < 38 AND vol_spike >= 10x for entry. OR=BROKE-DOWN means downward breakout — avoid BUY entirely. OR init= shows the initial direction of the opening range (UP/DOWN/FLAT). BROKE-UP + init=DOWN is a genuine reversal (best setup) — VWAP can be slightly negative because the stock sold off at open before reversing. BROKE-UP + init=UP with vwap_pct >= 0% is valid. BROKE-UP + init=UP with vwap_pct < -1.0% = likely fake breakout with no VWAP support — avoid.
- CANDLESTICK PATTERNS: Each candidate shows candle=<pattern> when a pattern is detected on the last 3 candles. Use these as supporting signals — they do not override other rules but they strengthen or weaken conviction. BULLISH patterns (hammer, inverted_hammer, bullish_engulfing, morning_star, three_white_soldiers, bullish_marubozu): raise BUY confidence, especially powerful when RSI < 40 or consecutive_red >= 2 (genuine reversal confirmation). BEARISH patterns (shooting_star, bearish_engulfing, evening_star, three_black_crows, bearish_marubozu): lower BUY confidence and strengthen SELL signals on open positions. A hammer or bullish_engulfing after 3+ red candles is a strong oversold reversal signal. A shooting_star or bearish_engulfing near day_high is a strong resistance-rejection signal. No pattern shown = "none" — does not block a BUY on its own.

Do all calculations internally. Respond ONLY with valid JSON — no prose, no working, no markdown:
{{"action": "BUY"|"SELL"|"HOLD", "stock": "<SYMBOL>"|null, "confidence": <0.0-1.0>, "reasoning": "<one sentence including min_move calc or P&L justification>"}}""".format(
    capital=int(config.TRADING_CAPITAL_INR),
    threshold=config.MIN_CONFIDENCE_THRESHOLD,
)


@dataclass
class Decision:
    action: str
    stock: object
    confidence: float
    reasoning: str


_MODEL_PRICING = {
    "claude-haiku-4-5-20251001":  (0.80,  4.0, 0.08, 1.00),
    "claude-3-5-haiku-20241022":  (0.80,  4.0, 0.08, 1.00),
    "claude-sonnet-4-6":          (3.00, 15.0, 0.30, 3.75),
    "claude-3-5-sonnet-20241022": (3.00, 15.0, 0.30, 3.75),
}

def _estimate_cost_usd(input_tokens: int, output_tokens: int, cache_read: int, cache_creation: int = 0, model: str = "claude-haiku-4-5-20251001") -> float:
    inp, out, cr, cw = _MODEL_PRICING.get(model, (3.0, 15.0, 0.30, 3.75))
    uncached = input_tokens - cache_read - cache_creation
    return (uncached * inp + cache_read * cr + cache_creation * cw + output_tokens * out) / 1_000_000


def _candidates_hash(candidates: dict) -> str:
    content = json.dumps(candidates, sort_keys=True)
    return hashlib.md5(content.encode()).hexdigest()[:12]


class ClaudeEngine:
    def __init__(self, api_key: str):
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=6, timeout=60.0)

    def decide(
        self,
        candidates: dict,
        portfolio: dict,
        state: dict,
        candidates_hash: str = None,
    ) -> tuple:
        if state.get("claude_spend_usd", 0.0) >= config.CLAUDE_API_BUDGET_STOP_USD:
            return Decision("HOLD", None, 0.0, "budget limit reached - manual restart required"), state

        c_hash = candidates_hash or _candidates_hash(candidates)
        pos = portfolio.get("position")
        pos_price_key = f"{pos['stock']}:{pos.get('current_price', pos['entry_price']):.2f}" if pos else "none"
        full_hash = f"{c_hash}:{pos_price_key}"
        if full_hash == state.get("last_candidates_hash") and state.get("last_decision"):
            d = state["last_decision"]
            return Decision(**d), state

        position_line = "No open position." if not portfolio.get("position") else (
            f"Open position: {portfolio['position']['stock']} @ entry Rs{portfolio['position']['entry_price']:.2f}, "
            f"current Rs{portfolio['position'].get('current_price', portfolio['position']['entry_price']):.2f}, "
            f"qty={portfolio['position']['qty']}, "
            f"net_P&L=Rs{portfolio['position'].get('net_pnl_inr', 0):+.2f} (after charges), "
            f"take_profit=Rs{portfolio['position'].get('take_profit_price', 0):.2f}"
        )
        market_section = "\n".join(candidates.values())
        from datetime import datetime, timezone, timedelta
        ist_now = datetime.now(timezone(timedelta(hours=5, minutes=30)))
        minutes_to_close = max(0, int((ist_now.replace(hour=15, minute=15, second=0, microsecond=0) - ist_now).total_seconds() / 60))
        nifty_change = portfolio.get("nifty_change", 0.0)
        nifty_line = f"NIFTY: {nifty_change:+.2f}% from open ({'market selling off' if nifty_change < -0.5 else 'market buying' if nifty_change > 0.5 else 'market flat'})"
        premarket_ctx = _load_premarket_report()
        user_content = (
            f"Current time: {ist_now.strftime('%H:%M')} IST ({minutes_to_close} min until market close)\n"
            f"{nifty_line}\n"
            + (f"{premarket_ctx}\n" if premarket_ctx else "")
            + f"Available cash: Rs{portfolio['cash']:.0f}\n"
            f"{position_line}\n\n"
            f"Top {len(candidates)} candidates:\n{market_section}"
        )

        msg = None
        last_err = None
        _used_model = None
        _models = [
            "claude-haiku-4-5-20251001",
            "claude-sonnet-4-6",
            "claude-3-5-sonnet-20241022",
        ]
        for _model in _models:
            try:
                msg = self._client.messages.create(
                    model=_model,
                    max_tokens=1000,
                    system=[{"type": "text", "text": _SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
                    messages=[{"role": "user", "content": user_content + "\n\nRespond with JSON only. No prose, no markdown, no explanation. Your entire response must be a single JSON object."}],
                )
                _used_model = _model
                break
            except Exception as e:
                last_err = e
                err_str = str(e)
                if "529" in err_str or "overloaded" in err_str.lower() or "404" in err_str or "not_found" in err_str:
                    time.sleep(5)
                    continue
                break
        if msg is None:
            return Decision("HOLD", None, 0.0, f"Claude API error: {last_err}"), state

        raw = msg.content[0].text.strip()
        start = raw.find('{')
        end = raw.rfind('}')
        if start != -1 and end != -1:
            raw = raw[start:end + 1]

        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return Decision("HOLD", None, 0.0, f"parse failure: {raw[:120]}"), state

        action = parsed.get("action", "HOLD")
        confidence = float(parsed.get("confidence", 0.0))
        stock = parsed.get("stock")
        _short_model = _used_model.replace("claude-", "").replace("-20251001", "").replace("-20241022", "")
        reasoning = f"[{_short_model}] " + parsed.get("reasoning", "")

        if action == "BUY" and confidence < config.MIN_CONFIDENCE_THRESHOLD:
            action = "HOLD"
            reasoning = f"[{_short_model}] confidence {confidence:.2f} below threshold - downgraded to HOLD"

        cost = _estimate_cost_usd(
            msg.usage.input_tokens,
            msg.usage.output_tokens,
            getattr(msg.usage, "cache_read_input_tokens", 0),
            getattr(msg.usage, "cache_creation_input_tokens", 0),
            model=_used_model,
        )
        new_state = {
            **state,
            "claude_spend_usd": state.get("claude_spend_usd", 0.0) + cost,
            "last_candidates_hash": full_hash,
            "last_decision": {"action": action, "stock": stock, "confidence": confidence, "reasoning": reasoning},
        }
        return Decision(action, stock, confidence, reasoning), new_state
