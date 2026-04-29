import os
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise EnvironmentError(f"Missing required environment variable: {key}")
    return val


UPSTOX_API_KEY = _require("UPSTOX_API_KEY")
UPSTOX_API_SECRET = _require("UPSTOX_API_SECRET")
UPSTOX_ACCESS_TOKEN = _require("UPSTOX_ACCESS_TOKEN")
ANTHROPIC_API_KEY = _require("ANTHROPIC_API_KEY")
TELEGRAM_BOT_TOKEN = _require("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _require("TELEGRAM_CHAT_ID")


PAPER_TRADE = os.getenv("PAPER_TRADE", "false").lower() == "true"
USE_LEVERAGE = os.getenv("USE_LEVERAGE", "false").lower() == "true"
CYCLE_INTERVAL_MINUTES = int(os.getenv("CYCLE_INTERVAL_MINUTES", "5"))
TRADING_CAPITAL_INR = float(os.getenv("TRADING_CAPITAL_INR", "4000"))
CLAUDE_API_BUDGET_USD = float(os.getenv("CLAUDE_API_BUDGET_USD", "9.00"))
CLAUDE_API_BUDGET_STOP_USD = float(os.getenv("CLAUDE_API_BUDGET_STOP_USD", "8.50"))
MIN_CONFIDENCE_THRESHOLD = float(os.getenv("MIN_CONFIDENCE_THRESHOLD", "0.80"))
MIN_DAILY_TRADED_VALUE_CR = float(os.getenv("MIN_DAILY_TRADED_VALUE_CR", "1"))
MIN_DAILY_VOLUME = int(os.getenv("MIN_DAILY_VOLUME", "50000"))
MAX_BID_ASK_SPREAD_PCT = float(os.getenv("MAX_BID_ASK_SPREAD_PCT", "0.5"))
TOP_CANDIDATES = int(os.getenv("TOP_CANDIDATES", "50"))
MAX_DAILY_LOSS_INR = float(os.getenv("MAX_DAILY_LOSS_INR", "200"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "2.0"))
UPSTOX_FLAT_BROKERAGE_INR = float(os.getenv("UPSTOX_FLAT_BROKERAGE_INR", "20"))
UPSTOX_STT_SELL_PCT = float(os.getenv("UPSTOX_STT_SELL_PCT", "0.00025"))
UPSTOX_EXCHANGE_CHARGE_PCT = float(os.getenv("UPSTOX_EXCHANGE_CHARGE_PCT", "0.0000325"))
UPSTOX_GST_PCT = float(os.getenv("UPSTOX_GST_PCT", "0.18"))
