# Trading Bot — Design Spec
**Date:** 2026-04-29
**Status:** Approved

---

## Overview

An AI-driven intraday stock trading bot that trades NSE/BSE stocks via the Upstox API. Claude (claude-sonnet-4-6) is the decision-making brain — it receives a full market data packet every cycle and outputs a structured BUY/SELL/HOLD decision including stock, quantity, price limit, confidence score, and reasoning. All positions are closed by 3:15 PM IST daily.

---

## Constraints

### Capital
- **Trading capital: ₹4,000 INR**
- Upstox intraday brokerage: ₹20 flat per executed order (buy + sell = ₹40 round-trip)
- Additional charges per trade: STT (0.025% on sell side), exchange transaction charges (~0.00325%), GST (18% on brokerage), SEBI charges
- Round-trip cost on ₹4,000 is roughly ₹45–50, ~1.1–1.25% of capital
- **Implication:** Claude must require high confidence (≥ 0.80) before placing any trade. Frequent low-confidence trades will destroy the account through charges alone. Claude is explicitly told the charge structure in every prompt.
- Only stocks priced below ₹3,900 per share can be traded (to afford at least 1 share with buffer for charges)

### Claude API Budget
- **Budget: $9 USD total**
- claude-sonnet-4-6 pricing: $3/MTok input, $15/MTok output; cached input: $0.30/MTok
- Estimated prompt size per cycle: ~3,000–5,000 tokens input, ~300 tokens output
- Without caching: ~$0.013–0.020 per cycle → $9 lasts ~450–700 cycles (~6–10 trading days at 72 cycles/day
- **With prompt caching:** System prompt and static instructions are cached; only the market data changes each cycle. Estimated cost drops to ~$0.003–0.005 per cycle → $9 lasts ~1,800–3,000 cycles (~25–40 trading days
- **Hard budget limit:** Bot tracks cumulative Claude API spend via token counts. If estimated spend exceeds $8.50, the bot stops calling Claude and sends an alert. The $0.50 buffer is preserved for error-recovery calls.
- **Skip-if-unchanged:** If market data has not changed meaningfully from the previous cycle (price movement < 0.3%, no new news), skip the Claude call and carry forward the previous HOLD decision. This alone can cut Claude calls by 30–50%.

### Upstox API
- Rate limits apply — full market scan is pre-filtered to top ~200 stocks by volume before fetching detailed data, to stay within limits.

---

## Architecture

Two processes run side by side from `main.py`:

1. **Trading Loop** — scheduled pipeline that runs every ~5 minutes during market hours (9:15 AM – 3:15 PM IST)
2. **Dashboard Server** — FastAPI web server serving a live UI

```
main.py
  ├── scheduler.py        ← trading loop orchestrator
  │     ├── data_fetcher.py
  │     ├── indicators.py
  │     ├── news.py
  │     ├── claude_engine.py
  │     ├── order_executor.py
  │     └── notifier.py
  ├── dashboard/
  │     ├── server.py     ← FastAPI app
  │     └── static/       ← HTML/JS UI
  ├── logger.py           ← existing CSV trade logger
  ├── config.py           ← loads and validates .env
  └── auth.py             ← one-time Upstox OAuth setup
```

Shared state between the two processes is written by the trading loop to `state.json` and read by the dashboard server.

---

## Components

### `auth.py`
One-time script to complete Upstox OAuth 2.0 flow and save the access token to `.env`. The trading loop refreshes the token daily at startup.

### `config.py`
Loads all environment variables from `.env` at startup. Validates that required keys are present (Upstox API key/secret/token, Claude API key, Telegram bot token). Raises a clear error with the missing key name if any are absent. Fails fast — the bot does not start with incomplete config.

### `data_fetcher.py`
Calls Upstox API to fetch:
- OHLCV candles (1-min and 5-min timeframes) for top ~200 stocks by volume
- Current order book (bid/ask depth) for candidates
- Portfolio state: current holdings and available cash balance

Pre-filters the full NSE/BSE universe to the top ~200 most active stocks by volume to stay within Upstox API rate limits. Outputs a structured dict per stock.

### `indicators.py`
Computes technical indicators from raw OHLCV data:
- RSI (14), MACD (12/26/9), Bollinger Bands (20, 2σ)
- EMA (9, 21, 50), VWAP, ATR (14)

Outputs an enriched dict per stock, ready for Claude.

### `news.py`
Fetches recent financial news headlines for the top stock candidates (post-indicator pre-filter) from a financial news API (Finnhub or NewsAPI). Attaches headline list and basic sentiment context to each stock's data packet.

### `claude_engine.py`
- Builds a structured prompt containing: portfolio state, available capital (₹4,000), charge structure (₹45–50 round-trip cost), current time, top stock candidates with all indicators and news
- Uses **Anthropic prompt caching** — the system prompt (instructions, charge structure, decision format) is marked as a cache breakpoint so it is only billed at $0.30/MTok on cache hits; only the market data section changes each cycle
- Tracks cumulative token usage and estimated spend in `state.json`; refuses to call Claude if estimated total exceeds $8.50
- Implements **skip-if-unchanged**: if price movement < 0.3% and no new news since last cycle, skips the Claude call and returns previous HOLD decision
- Calls Claude API (claude-sonnet-4-6) with the prompt
- Expects Claude to return a JSON decision:
  ```json
  {
    "action": "BUY" | "SELL" | "HOLD",
    "stock": "SYMBOL",
    "quantity": 10,
    "price_limit": 1450.00,
    "confidence": 0.85,
    "reasoning": "..."
  }
  ```
- Claude is instructed to only return BUY/SELL when confidence ≥ 0.80, given the high charge-to-capital ratio
- Claude is instructed to only consider stocks priced below ₹3,900 per share
- Parses and validates the JSON response
- For error recovery: passes error context + portfolio state to Claude, which returns one of `RETRY`, `SKIP_CYCLE`, `CLOSE_ALL`, or `CONTINUE`

### `order_executor.py`
- Takes Claude's parsed decision and places the order via Upstox API
- Tracks all open positions in `state.json`
- Handles order confirmation and partial fills
- At 3:15 PM IST, executes forced close of all open positions regardless of Claude's decision
- Respects `PAPER_TRADE=true` env flag — logs decisions without placing real orders

### `notifier.py`
Sends alerts via:
- **Telegram** — bot token + chat ID from `.env`
- **WhatsApp** — via Twilio API (optional, configured separately)

Fires on: every trade decision (including Claude's reasoning, stock, action, quantity, confidence), every error event, and EOD summary.

### `scheduler.py`
- Orchestrates the trading loop using the `schedule` library
- Runs the pipeline every 5 minutes between 9:15 AM and 3:15 PM IST on weekdays
- Triggers EOD forced close at 3:15 PM
- Skips cycles on market holidays (holiday list loaded from config or a public API)

### `dashboard/server.py`
FastAPI server that exposes:
- `GET /` — serves the dashboard HTML
- `GET /api/state` — returns current `state.json` (open positions, cash, last cycle time)
- `GET /api/trades` — returns parsed CSV trade log

### `dashboard/static/`
Minimal HTML/JS dashboard showing:
- Open positions with unrealised P&L
- Available cash
- Today's trade history with Claude's reasoning per trade
- Bot status (running / stopped / error)

Polls `/api/state` and `/api/trades` every 10 seconds.

### `logger.py` (existing)
Unchanged. Used by `order_executor` and `claude_engine` to log every trade and error row to `trades.csv`.

---

## Data Flow (one cycle)

```
scheduler.py triggers cycle
  → data_fetcher.py   fetches OHLCV + order book + portfolio state
  → indicators.py     computes RSI, MACD, EMA, VWAP, ATR, Bollinger Bands
  → news.py           fetches headlines for top candidates
  → claude_engine.py  builds prompt, calls Claude, parses JSON decision
  → order_executor.py places order on Upstox, updates state.json
  → logger.py         appends row to trades.csv
  → notifier.py       sends Telegram/WhatsApp alert
```

On error at any stage:
```
  → claude_engine.py  called with error context
  → Claude returns    RETRY | SKIP_CYCLE | CLOSE_ALL | CONTINUE
  → scheduler.py      acts on instruction
  → logger.py         logs ERROR row
  → notifier.py       sends alert
```

---

## Error Handling

**Hard overrides (Claude cannot override):**
- Past 3:15 PM IST with open positions → force close, no exceptions
- Claude API unreachable for 3+ consecutive retries → stop bot, send alert, require manual restart
- Margin breach detected → close all positions immediately, stop bot
- Estimated Claude API spend ≥ $8.50 → stop calling Claude, send alert, require manual restart
- Claude returns confidence < 0.80 for BUY/SELL → downgrade to HOLD automatically, no order placed

**Claude-managed recovery (everything else):**
- Data API errors, rate limits, malformed responses, order rejections, partial fills
- Claude receives the error description and current portfolio state and returns a recovery instruction

**All errors** are logged to `trades.csv` with `action="ERROR"` using the existing `error` field in `logger.py`.

---

## Environment Variables (`.env`)

```
UPSTOX_API_KEY=
UPSTOX_API_SECRET=
UPSTOX_ACCESS_TOKEN=
ANTHROPIC_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TWILIO_ACCOUNT_SID=           # optional, for WhatsApp
TWILIO_AUTH_TOKEN=            # optional
TWILIO_WHATSAPP_FROM=         # optional
NEWS_API_KEY=                 # Finnhub or NewsAPI
PAPER_TRADE=true              # set to false for live trading
CYCLE_INTERVAL_MINUTES=5
TRADING_CAPITAL_INR=4000
CLAUDE_API_BUDGET_USD=9.00
CLAUDE_API_BUDGET_STOP_USD=8.50
MIN_CONFIDENCE_THRESHOLD=0.80
MAX_STOCK_PRICE_INR=3900
```

---

## Testing

**Unit tests** (one file per module, all external calls mocked):
- `tests/test_logger.py` — existing, complete
- `tests/test_indicators.py` — verify indicator computations against known inputs
- `tests/test_claude_engine.py` — mock Claude API, verify prompt building and JSON parsing
- `tests/test_order_executor.py` — mock Upstox API, verify order placement and state.json updates
- `tests/test_notifier.py` — mock Telegram/Twilio clients, verify message formatting
- `tests/test_scheduler.py` — verify market hours logic, EOD close trigger, cycle orchestration

**Integration test:**
- `tests/test_full_cycle.py` — one complete pipeline cycle with all external calls mocked, verifies data flows end-to-end

**Paper trading mode:**
- `PAPER_TRADE=true` makes `order_executor` skip real API calls and log decisions only
- Use this for validation before going live

No tests call real Upstox or Claude APIs.

---

## Implementation Order

1. `config.py` + `.env` structure
2. `auth.py` — Upstox OAuth setup
3. `data_fetcher.py` — Upstox data layer
4. `indicators.py` — technical indicators
5. `news.py` — news/sentiment fetching
6. `claude_engine.py` — Claude integration
7. `order_executor.py` + `state.json`
8. `notifier.py` — Telegram + WhatsApp
9. `scheduler.py` — loop orchestration
10. `dashboard/` — FastAPI + UI
11. `main.py` — entry point
12. Full test suite
