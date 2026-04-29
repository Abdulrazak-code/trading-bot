import json
import pytest
from unittest.mock import patch, MagicMock
from order_executor import OrderExecutor, load_state, save_state, calculate_charges


@pytest.fixture
def initial_state():
    return {
        "position": None,
        "daily_realised_pnl": 0.0,
        "claude_spend_usd": 0.0,
        "last_candidates_hash": "",
        "last_decision": None,
        "seen_headline_hashes": [],
    }


@pytest.fixture
def state_file(tmp_path, initial_state):
    p = tmp_path / "state.json"
    p.write_text(json.dumps(initial_state))
    return initial_state, str(p)


def test_load_save_state_roundtrip(tmp_path):
    path = str(tmp_path / "state.json")
    s = {"position": None, "daily_realised_pnl": 0.0}
    save_state(s, path)
    loaded = load_state(path)
    assert loaded["position"] is None


def test_load_state_returns_defaults_when_missing(tmp_path):
    path = str(tmp_path / "nonexistent.json")
    loaded = load_state(path)
    assert loaded["position"] is None
    assert loaded["daily_realised_pnl"] == 0.0


def test_buy_records_position_in_paper_mode(state_file):
    s, path = state_file
    ex = OrderExecutor(paper_trade=True, state_path=path)
    new_s = ex.execute_buy("RELIANCE", price=2450.0, cash=4000.0, state=s)
    assert new_s["position"]["stock"] == "RELIANCE"
    assert new_s["position"]["qty"] == int(4000.0 / 2450.0)
    assert new_s["position"]["entry_price"] == 2450.0


def test_sell_deducts_charges_from_pnl(state_file):
    s, path = state_file
    s["position"] = {"stock": "RELIANCE", "entry_price": 2400.0, "qty": 1}
    ex = OrderExecutor(paper_trade=True, state_path=path)
    new_s = ex.execute_sell("RELIANCE", price=2450.0, qty=1, state=s, reason="target hit")
    gross_pnl = (2450.0 - 2400.0) * 1  # 50.0
    charges = calculate_charges(2450.0 * 1)
    expected_net = gross_pnl - charges
    assert new_s["daily_realised_pnl"] == pytest.approx(expected_net, abs=0.01)


def test_sell_clears_position(state_file):
    s, path = state_file
    s["position"] = {"stock": "RELIANCE", "entry_price": 2400.0, "qty": 1}
    ex = OrderExecutor(paper_trade=True, state_path=path)
    new_s = ex.execute_sell("RELIANCE", price=2450.0, qty=1, state=s, reason="target hit")
    assert new_s["position"] is None


def test_stop_loss_triggered_when_down_2pct(state_file):
    s, path = state_file
    s["position"] = {"stock": "TCS", "entry_price": 3500.0, "qty": 1}
    ex = OrderExecutor(paper_trade=True, state_path=path)
    # 3500 * 0.98 = 3430, so 3430 should trigger
    triggered = ex.check_stop_loss(current_price=3430.0, state=s)
    assert triggered is True


def test_stop_loss_not_triggered_within_limit(state_file):
    s, path = state_file
    s["position"] = {"stock": "TCS", "entry_price": 3500.0, "qty": 1}
    ex = OrderExecutor(paper_trade=True, state_path=path)
    # 3500 * 0.982 = 3437, so 3460 is within 2% limit
    triggered = ex.check_stop_loss(current_price=3460.0, state=s)
    assert triggered is False


def test_daily_loss_limit_blocks_at_threshold(state_file):
    s, path = state_file
    s["daily_realised_pnl"] = -210.0
    ex = OrderExecutor(paper_trade=True, state_path=path)
    assert ex.is_daily_loss_limit_reached(s) is True


def test_daily_loss_limit_not_reached(state_file):
    s, path = state_file
    s["daily_realised_pnl"] = -150.0
    ex = OrderExecutor(paper_trade=True, state_path=path)
    assert ex.is_daily_loss_limit_reached(s) is False


def test_has_open_position(state_file):
    s, path = state_file
    s["position"] = {"stock": "INFY", "entry_price": 1800.0, "qty": 2}
    ex = OrderExecutor(paper_trade=True, state_path=path)
    assert ex.has_open_position(s) is True


def test_no_open_position(state_file):
    s, path = state_file
    ex = OrderExecutor(paper_trade=True, state_path=path)
    assert ex.has_open_position(s) is False


def test_calculate_charges_structure():
    # For sell_value=4000: brokerage=40, gst=7.2, stt=1.0, exchange=0.13
    charges = calculate_charges(4000.0)
    assert charges > 40.0  # at minimum flat brokerage round-trip
    assert charges < 60.0  # sanity upper bound
