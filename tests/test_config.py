# tests/test_config.py
import pytest
import importlib
import os


def test_missing_required_key_raises(monkeypatch):
    monkeypatch.delenv("UPSTOX_API_KEY", raising=False)
    monkeypatch.delenv("UPSTOX_API_SECRET", raising=False)
    with pytest.raises(EnvironmentError, match="UPSTOX_API_KEY"):
        import config
        importlib.reload(config)


def test_defaults_are_applied(monkeypatch):
    for key in ["UPSTOX_API_KEY", "UPSTOX_API_SECRET", "UPSTOX_ACCESS_TOKEN",
                "ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]:
        monkeypatch.setenv(key, "test")
    monkeypatch.delenv("STOP_LOSS_PCT", raising=False)
    import config
    importlib.reload(config)
    assert config.STOP_LOSS_PCT == 2.0


def test_paper_trade_parses_bool(monkeypatch):
    for key in ["UPSTOX_API_KEY", "UPSTOX_API_SECRET", "UPSTOX_ACCESS_TOKEN",
                "ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]:
        monkeypatch.setenv(key, "test")
    monkeypatch.setenv("PAPER_TRADE", "true")
    import config
    importlib.reload(config)
    assert config.PAPER_TRADE is True
