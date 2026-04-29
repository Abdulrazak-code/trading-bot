# tests/test_full_cycle.py
"""
Full pipeline cycle test: data_fetcher → indicators → news → claude_engine → order_executor → logger
All external calls are mocked.
"""
import json
import pytest
from unittest.mock import patch, MagicMock
from claude_engine import ClaudeEngine, Decision
from order_executor import OrderExecutor, load_state, save_state, calculate_charges
from indicators import score_and_rank, compress_packet
from logger import log_trade


@pytest.fixture
def state_file(tmp_path):
    s = {
        "position": None,
        "daily_realised_pnl": 0.0,
        "claude_spend_usd": 0.0,
        "last_candidates_hash": "",
        "last_decision": None,
        "seen_headline_hashes": [],
    }
    p = tmp_path / "state.json"
    p.write_text(json.dumps(s))
    return str(p), s


def _mock_claude_response(action, stock, confidence, reasoning):
    msg = MagicMock()
    msg.content = [MagicMock(text=json.dumps({
        "action": action, "stock": stock, "confidence": confidence, "reasoning": reasoning
    }))]
    msg.usage = MagicMock(input_tokens=2500, output_tokens=80,
                          cache_read_input_tokens=2000, cache_creation_input_tokens=0)
    return msg


def test_full_cycle_buy_then_stop_loss(state_file, tmp_path):
    state_path, initial_state = state_file
    trades_path = str(tmp_path / "trades.csv")

    # Stage 1: BUY decision
    candidates = {
        "RELIANCE": {"price": 2450.0, "volume_spike": 3.0, "rsi": 58.0,
                     "macd_signal": 0.5, "vwap_pct": 1.2, "spread_pct": 0.15,
                     "bb_position": 0.65, "atr": 18.0},
    }
    compressed = {sym: compress_packet(sym, data, []) for sym, data in candidates.items()}
    portfolio = {"cash": 4000.0, "position": None}

    engine = ClaudeEngine.__new__(ClaudeEngine)
    engine._client = MagicMock()
    engine._client.messages.create.return_value = _mock_claude_response("BUY", "RELIANCE", 0.88, "strong RSI")

    decision, new_state = engine.decide(compressed, portfolio, initial_state)
    assert decision.action == "BUY"
    assert decision.stock == "RELIANCE"

    executor = OrderExecutor(paper_trade=True, state_path=state_path)
    new_state = executor.execute_buy("RELIANCE", 2450.0, 4000.0, new_state)
    save_state(new_state, state_path)

    assert new_state["position"]["stock"] == "RELIANCE"
    assert new_state["position"]["qty"] == 1

    # Stage 2: stop-loss triggered
    triggered = executor.check_stop_loss(current_price=2400.0, state=new_state)
    assert triggered is True

    final_state = executor.execute_sell("RELIANCE", 2400.0, 1, new_state, "stop-loss")
    save_state(final_state, state_path)

    assert final_state["position"] is None
    # net P&L = gross loss - charges (charges make the loss worse)
    charges = calculate_charges(2400.0 * 1)
    assert final_state["daily_realised_pnl"] == pytest.approx(-50.0 - charges, abs=0.01)

    loaded = load_state(state_path)
    assert loaded["position"] is None
    assert loaded["daily_realised_pnl"] == pytest.approx(-50.0 - charges, abs=0.01)
