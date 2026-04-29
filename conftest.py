import os
import pytest


def pytest_configure(config):
    """Set dummy environment variables required by config.py for test collection."""
    required = {
        "UPSTOX_API_KEY": "test_upstox_key",
        "UPSTOX_API_SECRET": "test_upstox_secret",
        "UPSTOX_ACCESS_TOKEN": "test_access_token",
        "ANTHROPIC_API_KEY": "test_anthropic_key",
        "TELEGRAM_BOT_TOKEN": "test_telegram_token",
        "TELEGRAM_CHAT_ID": "test_chat_id",
    }
    for key, value in required.items():
        if not os.environ.get(key):
            os.environ[key] = value
