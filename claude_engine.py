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
- Before recommending BUY, calculate: qty = floor(Rs{capital} / price), min_move = Rs48 / qty. Only BUY if you are confident the stock can move more than min_move per share based on its momentum, news, and ATR. A stock at Rs1400 gives qty=7 and needs Rs6.86/share movement. A stock at Rs200 gives qty=50 and needs Rs0.96/share — very achievable. Reject any trade where the expected move does not clearly exceed min_move.

Do all calculations internally. Respond ONLY with valid JSON — no prose, no working, no markdown:
{{"action": "BUY"|"SELL"|"HOLD", "stock": "<SYMBOL>"|null, "confidence": <0.0-1.0>, "reasoning": "<one sentence including min_move calc>"}}""".format(
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
        if c_hash == state.get("last_candidates_hash") and state.get("last_decision"):
            d = state["last_decision"]
            return Decision(**d), state

        position_line = "No open position." if not portfolio.get("position") else (
            f"Open position: {portfolio['position']['stock']} @ Rs{portfolio['position']['entry_price']:.2f}, "
            f"qty={portfolio['position']['qty']}, P&L={portfolio['position'].get('pnl_pct', 0):+.2f}%"
        )
        market_section = "\n".join(candidates.values())
        user_content = (
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

        if action in ("BUY", "SELL") and confidence < config.MIN_CONFIDENCE_THRESHOLD:
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
            "last_candidates_hash": c_hash,
            "last_decision": {"action": action, "stock": stock, "confidence": confidence, "reasoning": reasoning},
        }
        return Decision(action, stock, confidence, reasoning), new_state
