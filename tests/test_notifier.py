# tests/test_notifier.py
import pytest
from unittest.mock import patch, MagicMock
from notifier import Notifier


@pytest.fixture
def notifier():
    return Notifier(telegram_token="tok", chat_id="123", twilio_sid=None, twilio_token=None, whatsapp_from=None)


def test_telegram_sends_message(notifier):
    with patch("notifier.requests.post") as mock_post:
        mock_post.return_value = MagicMock(ok=True)
        notifier.send("Test message")
    mock_post.assert_called_once()
    call_url = mock_post.call_args[0][0]
    assert "sendMessage" in call_url


def test_telegram_failure_does_not_raise(notifier):
    with patch("notifier.requests.post", side_effect=Exception("network error")):
        notifier.send("Test")  # should not raise


def test_format_trade_alert():
    notifier = Notifier("tok", "123")
    msg = notifier.format_trade("BUY", "RELIANCE", qty=1, price=2450.0, confidence=0.87, reasoning="strong momentum", pnl=None)
    assert "BUY" in msg
    assert "RELIANCE" in msg
    assert "0.87" in msg
