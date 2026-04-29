# tests/test_scheduler.py
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from scheduler import is_market_open, is_eod_close_time, Scheduler

_IST = timezone(timedelta(hours=5, minutes=30))


def _ist(hour, minute=0):
    return datetime(2026, 4, 29, hour, minute, tzinfo=_IST)


def test_market_open_during_hours():
    assert is_market_open(_ist(10, 0)) is True
    assert is_market_open(_ist(14, 30)) is True


def test_market_closed_before_open():
    assert is_market_open(_ist(9, 0)) is False


def test_market_closed_after_close():
    assert is_market_open(_ist(15, 20)) is False


def test_eod_close_triggers_at_3_15():
    assert is_eod_close_time(_ist(15, 15)) is True
    assert is_eod_close_time(_ist(15, 20)) is True   # within EOD window
    assert is_eod_close_time(_ist(15, 14)) is False  # before window
    assert is_eod_close_time(_ist(16, 0)) is False   # next hour


def test_weekend_is_not_market_open():
    saturday = datetime(2026, 5, 2, 10, 0, tzinfo=_IST)
    assert is_market_open(saturday) is False
