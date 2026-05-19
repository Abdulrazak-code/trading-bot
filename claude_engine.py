import hashlib
import json
import re
from dataclasses import dataclass

import anthropic

import config

_SYSTEM_PROMPT = """You are an AI intraday stock trading assistant for NSE/BSE markets.
You receive the top 50 pre-filtered stock candidates every cycle and output a structured decision.

RULES:
- Trading capital: Rs{capital}. One position at a time. All positions close at 3:15 PM IST.
- Round-trip cost: Rs45-50 per trade (~1.1-1.25% of capital). Only trade when expected gain clearly exceeds this.
- Confidence threshold: {threshold} minimum for BUY or SELL. Below {threshold}, return HOLD.
- If a position is already open, only SELL or HOLD are valid actions.
- Before recommending BUY, run this exact check: qty = floor(Rs{capital} / price), min_move = Rs54 / qty. ATR in the data is the average true range — the typical per-candle price move. ONLY BUY if ATR > min_move. If ATR <= min_move the stock cannot move enough to cover charges and the trade will lose money. Example: stock at Rs60, qty=333, min_move=Rs0.16 — ATR=0.25 passes, ATR=0.12 is rejected. Also require at least one momentum signal: vol_spike >= 1.5x OR RSI < 38 or > 62 OR VWAP deviation >= 0.5%. No signal = no trade.
- Do NOT recommend BUY for any stock priced above Rs1000. High-priced stocks give too few shares and require large per-share moves to recover costs.
- SELL rule: Recommend SELL if net P&L after charges is POSITIVE and you have >= {threshold} confidence the stock has peaked or momentum is reversing (e.g. price falling from day high, MACD_hist turning negative, vol_spike fading). You do NOT need to wait for the take-profit target — if you have high confidence the move is over, SELL. If net P&L is negative, HOLD unless you have strong conviction the stock will fall further (defensive exit). Overbought RSI or upper Bollinger Band alone are NOT sufficient reason to sell a losing position.
- The system also auto-sells at the 2:1 take-profit target and on stop-loss — but you should recommend SELL independently when you have >= {threshold} confidence, do not defer to those automatic triggers if you see clear exit signals.
- Each candidate shows: H=day_high L=day_low and %from_high. Price near day high (0 to -1%) with vol_spike means momentum is real. Price far from high (-3%+) means the move has already faded — avoid.
- MACD_hist is the histogram (MACD line minus signal line). Positive and growing = accelerating upward. Negative or shrinking = momentum dying.
- NIFTY context is provided. If NIFTY is down >0.5%, individual stock bullish signals need extra conviction — most moves will reverse with the market.
- Before recommending BUY, assess day potential: given minutes remaining until 3:15 PM and the stock's day range (day_high - day_low), estimate whether the stock can realistically move enough to hit profit. Use this check: expected_move = ATR * (minutes_remaining / 75). If expected_move < min_move, the stock likely cannot cover charges before close — HOLD. Also assess whether price is more likely to reach day_high again or has already peaked: if price is far below day_high (-3%+) with fading volume and negative MACD, the day high is unlikely to be revisited — avoid. If price is consolidating near day_high with strong volume, a breakout or retest is likely — favour BUY.

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


def _estimate_cost_usd(input_tokens: int, output_tokens: int, cache_read: int, cache_creation: int = 0) -> float:
    # Sonnet 4.6 pricing: $3/1M input, $15/1M output, $0.30/1M cache read, $3.75/1M cache write
    uncached = input_tokens - cache_read - cache_creation
    return (uncached * 3.0 + cache_read * 0.3 + cache_creation * 3.75 + output_tokens * 15.0) / 1_000_000


def _candidates_hash(candidates: dict) -> str:
    content = json.dumps(candidates, sort_keys=True)
    return hashlib.md5(content.encode()).hexdigest()[:12]


class ClaudeEngine:
    def __init__(self, api_key: str):
        self._client = anthropic.Anthropic(api_key=api_key)

    def decide(
        self,
        candidates: dict,
        portfolio: dict,
        state: dict,
        candidates_hash: str = None,
    ) -> tuple:
        if state["claude_spend_usd"] >= config.CLAUDE_API_BUDGET_STOP_USD:
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
        user_content = (
            f"Current time: {ist_now.strftime('%H:%M')} IST ({minutes_to_close} min until market close)\n"
            f"{nifty_line}\n"
            f"Available cash: Rs{portfolio['cash']:.0f}\n"
            f"{position_line}\n\n"
            f"Top {len(candidates)} candidates:\n{market_section}"
        )

        try:
            msg = self._client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=500,
                system=[{"type": "text", "text": _SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user_content + "\n\nRespond with JSON only. No prose, no markdown, no explanation. Your entire response must be a single JSON object."}],
            )
        except Exception as e:
            return Decision("HOLD", None, 0.0, f"Claude API error: {e}"), state

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
        reasoning = parsed.get("reasoning", "")

        if action == "BUY" and confidence < config.MIN_CONFIDENCE_THRESHOLD:
            action = "HOLD"
            reasoning = f"confidence {confidence:.2f} below threshold - downgraded to HOLD"

        cost = _estimate_cost_usd(
            msg.usage.input_tokens,
            msg.usage.output_tokens,
            getattr(msg.usage, "cache_read_input_tokens", 0),
            getattr(msg.usage, "cache_creation_input_tokens", 0),
        )
        new_state = {
            **state,
            "claude_spend_usd": state["claude_spend_usd"] + cost,
            "last_candidates_hash": full_hash,
            "last_decision": {"action": action, "stock": stock, "confidence": confidence, "reasoning": reasoning},
        }
        return Decision(action, stock, confidence, reasoning), new_state
