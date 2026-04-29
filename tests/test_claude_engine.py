# tests/test_claude_engine.py
import json
import pytest
from unittest.mock import MagicMock, patch
from claude_engine import ClaudeEngine, Decision


def _mock_response(text, input_tokens=2500, output_tokens=80, cache_read=2000):
    msg = MagicMock()
    msg.content = [MagicMock(text=text)]
    msg.usage = MagicMock(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=0,
    )
    return msg


def _state():
    return {"claude_spend_usd": 0.0, "last_candidates_hash": "", "last_decision": None}


def test_decide_returns_buy():
    engine = ClaudeEngine.__new__(ClaudeEngine)
    engine._client = MagicMock()
    engine._client.messages.create.return_value = _mock_response(
        '{"action":"BUY","stock":"RELIANCE","confidence":0.85,"reasoning":"strong momentum"}'
    )
    decision, _ = engine.decide(
        candidates={"RELIANCE": "RELIANCE: Rs2450.0 vol_spike=3.0x RSI=58 ..."},
        portfolio={"cash": 4000.0, "position": None},
        state=_state(),
    )
    assert decision.action == "BUY"
    assert decision.stock == "RELIANCE"
    assert decision.confidence == 0.85


def test_decide_downgrades_low_confidence_to_hold():
    engine = ClaudeEngine.__new__(ClaudeEngine)
    engine._client = MagicMock()
    engine._client.messages.create.return_value = _mock_response(
        '{"action":"BUY","stock":"INFY","confidence":0.65,"reasoning":"weak signal"}'
    )
    decision, _ = engine.decide(
        candidates={"INFY": "INFY: Rs1800.0 vol_spike=1.2x RSI=52 ..."},
        portfolio={"cash": 4000.0, "position": None},
        state=_state(),
    )
    assert decision.action == "HOLD"


def test_decide_returns_hold_on_json_parse_failure():
    engine = ClaudeEngine.__new__(ClaudeEngine)
    engine._client = MagicMock()
    engine._client.messages.create.return_value = _mock_response("I cannot decide right now.")
    decision, _ = engine.decide(
        candidates={"TCS": "TCS: Rs3500.0 ..."},
        portfolio={"cash": 4000.0, "position": None},
        state=_state(),
    )
    assert decision.action == "HOLD"
    assert "parse" in decision.reasoning.lower()


def test_decide_skips_call_when_unchanged():
    engine = ClaudeEngine.__new__(ClaudeEngine)
    engine._client = MagicMock()
    state = {
        "claude_spend_usd": 0.0,
        "last_candidates_hash": "abc123",
        "last_decision": {"action": "HOLD", "stock": None, "confidence": 0.0, "reasoning": "no change"},
    }
    decision, _ = engine.decide(
        candidates={"TCS": "TCS: Rs3500.0 ..."},
        portfolio={"cash": 4000.0, "position": None},
        state=state,
        candidates_hash="abc123",
    )
    engine._client.messages.create.assert_not_called()
    assert decision.action == "HOLD"


def test_decide_stops_when_budget_exceeded():
    engine = ClaudeEngine.__new__(ClaudeEngine)
    engine._client = MagicMock()
    state = {"claude_spend_usd": 8.60, "last_candidates_hash": "", "last_decision": None}
    decision, _ = engine.decide(
        candidates={"TCS": "TCS: Rs3500.0 ..."},
        portfolio={"cash": 4000.0, "position": None},
        state=state,
    )
    engine._client.messages.create.assert_not_called()
    assert decision.action == "HOLD"
    assert "budget" in decision.reasoning.lower()
