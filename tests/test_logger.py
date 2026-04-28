import os
import csv
import pytest
from logger import log_trade, LOG_FILE


@pytest.fixture(autouse=True)
def cleanup():
    yield
    if os.path.exists(LOG_FILE):
        os.remove(LOG_FILE)


def test_log_creates_file_with_headers():
    log_trade("BUY", "BTC", 250, 52340, "test reason", 2750)
    assert os.path.exists(LOG_FILE)
    with open(LOG_FILE) as f:
        reader = csv.DictReader(f)
        row = next(reader)
        assert row["action"] == "BUY"
        assert row["coin"] == "BTC"
        assert row["amount_inr"] == "250"
        assert row["price"] == "52340"


def test_log_hold_with_no_coin():
    log_trade("HOLD", None, 0, 0, "flat market", 2750)
    with open(LOG_FILE) as f:
        reader = csv.DictReader(f)
        row = next(reader)
        assert row["action"] == "HOLD"
        assert row["coin"] == ""
        assert row["amount_inr"] == "0"


def test_log_appends_multiple_rows():
    log_trade("BUY", "BTC", 250, 52340, "reason 1", 2750)
    log_trade("HOLD", None, 0, 0, "reason 2", 2750)
    with open(LOG_FILE) as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2


def test_log_error_row():
    log_trade("ERROR", None, 0, 0, "", 0, "API timeout")
    with open(LOG_FILE) as f:
        reader = csv.DictReader(f)
        row = next(reader)
        assert row["error"] == "API timeout"
        assert row["action"] == "ERROR"
