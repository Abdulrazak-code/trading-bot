# Trading Bot — Design Spec
**Date:** 2026-04-29
**Status:** Approved

---

## Overview

An AI-driven intraday stock trading bot that trades NSE/BSE stocks via the Upstox API. Claude (claude-sonnet-4-6) is the decision-making brain — it receives a compressed data packet of the top 50 pre-filtered stock candidates every cycle and outputs a structured BUY/SELL/HOLD decision including stock, quantity, price limit, confidence score, and reasoning. One position is held at a time. All positions are closed by 3:15 PM IST daily.

---

## Constraints

### Capital
- **Trading capital: ₹4,000 INR**
- **One open position at a time** — splitting capital across multiple positions on ₹4,000 means two sets of ₹45–50 charges on half the capital each, making profitability nearly impossible
- Upstox intraday brokerage: ₹20 flat per executed order (buy + sell = ₹40 round-trip)
- Additional charges per trade: STT (0.025% on sell side), exchange transaction charges (~0.00325%), GST (18% on brokerage), SEBI charges
- Round-trip cost on ₹4,000 is roughly ₹45–50, ~1.1–1.25% of capital
- **Implication:** Claude must require high confidence (≥ 0.80) before placing any trade. Frequent low-confidence trades will destroy the account through charges alone. Claude is explicitly told the charge structure in every prompt.
- No upper price cap on stocks — small/cheap stocks are actively preferred as they allow more shares per trade and larger absolute gains per % move on ₹4,000 capital
- **Liquidity filter (hard requirement):** Only stocks with average daily traded value ≥ ₹1 crore and average daily volume ≥ 50,000 shares are eligible. Thin-volume stocks risk wide bid-ask spreads and inability to exit before 3:15 PM.
- **Bid-ask spread filter:** Reject any stock where the current spread exceeds 0.5% of price — spread cost compounds with charges on small capital
- **Upstox MIS eligibility:** Only stocks approved by Upstox for intraday (MIS) trading are considered
- **Leverage:** MIS leverage is off by default (`USE_LEVERAGE=false`). Can be enabled via config — Upstox provides up to 5x on eligible stocks — but one bad leveraged trade can wipe the account, so this is an explicit opt-in

### Claude API Budget
- **Budget: $9 USD total**
- claude-sonnet-4-6 pricing: $3/MTok input, $15/MTok output; cached input: $0.30/MTok
- Each stock's data is **compressed to ~40 tokens** (current price, volume, RSI, MACD signal, VWAP distance, spread, 1–2 news headlines) — not raw candles
- 50 candidates × 40 tokens = ~2,000 tokens of market data per cycle, plus ~500 tokens system prompt = ~2,500 tokens input total
- **With prompt caching:** System prompt cached at $0.30/MTok; ~$0.003–0.004 per cycle → $9 lasts ~2,250–3,000 cycles (~30–40 trading days)
- **Hard budget limit:** Bot tracks cumulative Claude API spend via token counts in `state.json`. If estimated spend exceeds $8.50, the bot stops calling Claude and sends an alert. The $0.50 buffer is preserved for error-recovery calls.
- **Skip-if-unchanged:** If no candidate has moved > 0.3% and no new news since last cycle, skip the Claude call and carry forward the previous HOLD decision. Cuts Claude calls by 30–50% on quiet days.

### Upstox API
- Rate limits apply — full market scan is pre-filtered to top ~200 MIS-eligible stocks by liquidity before fetching detailed indicator data, then further scored down to top 50 candidates passed to Claude

### News
- Financial news fetched via **free RSS feeds** (MoneyControl, Economic Times) — no API key or cost. Only the top 50 candidates receive news enrichment, keeping fetch count low.

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
Two-stage fetch:
1. **Stage 1 — universe filter:** Fetch the MIS-eligible stock list from Upstox. Apply liquidity filter (daily traded value ≥ ₹1 crore, volume ≥ 50,000 shares). Yields ~200 candidates.
2. **Stage 2 — detail fetch:** For the top 200, fetch OHLCV candles (1-min and 5-min), current order book (bid/ask depth), and portfolio state (holdings + cash). Apply bid-ask spread filter (≤ 0.5%). Yields enriched data for up to 200 stocks.

Also fetches and returns current portfolio state: open position (if any), available cash.

### `indicators.py`
Computes technical indicators from raw OHLCV data for all candidates:
- RSI (14), MACD (12/26/9), Bollinger Bands (20, 2σ)
- EMA (9, 21, 50), VWAP, ATR (14)

Then **scores** each stock on a composite momentum score (volume spike ratio, RSI distance from 50, MACD crossover signal, VWAP deviation). Returns the **top 50 by score** with compressed data packets — only the computed values, not raw candles.

Each compressed packet: `{symbol, price, volume_spike, rsi, macd_signal, vwap_pct, spread_pct, bb_position}`

### `news.py`
Fetches recent headlines from MoneyControl and Economic Times RSS feeds (no API key). Matches headlines to the top 50 symbols by company name/ticker. Attaches up to 2 relevant headlines per stock. Stocks with no matching news get an empty headlines list.

### `claude_engine.py`
- Builds a structured prompt with a **cached system section** (instructions, charge structure ₹45–50 round-trip, confidence requirement ≥ 0.80, one-position-at-a-time rule, decision JSON format) and a **dynamic market section** (current time, portfolio state, top 50 compressed candidate packets + news)
- Uses **Anthropic prompt caching** — system section marked as cache breakpoint, billed at $0.30/MTok on hits
- Tracks cumulative token usage and estimated spend in `state.json`; refuses to call Claude if estimated total exceeds $8.50
- Implements **skip-if-unchanged**: if no candidate moved > 0.3% and no new news, skips call and returns previous decision
- Calls Claude API (claude-sonnet-4-6)
- Expected JSON response:
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
- Claude is instructed to: prefer lower-priced liquid stocks (more shares = better granularity), account for ₹45–50 round-trip cost in expected profit calculation, and only act when expected gain clearly exceeds charges
- If confidence < 0.80 on a BUY/SELL decision, the bot downgrades it to HOLD — no order placed
- For error recovery: passes error context + portfolio state to Claude, which returns `RETRY | SKIP_CYCLE | CLOSE_ALL | CONTINUE`

### `order_executor.py`
- Takes Claude's parsed decision and places the order via Upstox API (MIS order type)
- Enforces one-position-at-a-time: if a position is already open, only SELL or HOLD are acted on; a new BUY is ignored
- Tracks open position in `state.json`
- Handles order confirmation and partial fills
- At 3:15 PM IST, executes forced close of all open positions regardless of Claude's decision
- Respects `PAPER_TRADE=true` env flag — logs decisions without placing real orders (optional, off by default)

### `notifier.py`
Sends alerts via:
- **Telegram** — bot token + chat ID from `.env`
- **WhatsApp** — via Twilio API (optional, configured separately)

Fires on: every trade decision (Claude's reasoning, stock, action, quantity, confidence, estimated charge impact), every error event, EOD summary (P&L, charges paid, Claude API spend to date).

### `scheduler.py`
- Orchestrates the trading loop using the `schedule` library
- Runs the pipeline every 5 minutes between 9:15 AM and 3:15 PM IST on weekdays
- Triggers EOD forced close at 3:15 PM
- Skips cycles on NSE market holidays (holiday list fetched from Upstox API at startup)

### `dashboard/server.py`
FastAPI server that exposes:
- `GET /` — serves the dashboard HTML
- `GET /api/state` — returns current `state.json` (open position, cash, Claude spend, last cycle time)
- `GET /api/trades` — returns parsed CSV trade log

### `dashboard/static/`
Minimal HTML/JS dashboard showing:
- Open position with unrealised P&L
- Available cash and today's realised P&L
- Claude API budget consumed vs remaining
- Today's trade history with Claude's reasoning and confidence per trade
- Bot status (running / stopped / error)

Polls `/api/state` and `/api/trades` every 10 seconds.

### `logger.py` (existing)
Unchanged. Used by `order_executor` and `claude_engine` to log every trade and error row to `trades.csv`.

---

## Data Flow (one cycle)

```
scheduler.py triggers cycle
  → data_fetcher.py   stage 1: MIS + liquidity filter (~200 stocks)
                      stage 2: fetch OHLCV + order book, apply spread filter
  → indicators.py     compute indicators, score all candidates, return top 50 compressed packets
  → news.py           fetch RSS headlines, attach to top 50
  → claude_engine.py  skip-if-unchanged check → build prompt → call Claude → parse decision
  → order_executor.py enforce one-position rule → place order → update state.json
  → logger.py         append row to trades.csv
  → notifier.py       send Telegram/WhatsApp alert
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
- Claude returns confidence < 0.80 for BUY/SELL → automatically downgrade to HOLD, no order placed
- Position already open and Claude returns BUY on a different stock → ignore, hold current position

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
TWILIO_ACCOUNT_SID=              # optional, for WhatsApp
TWILIO_AUTH_TOKEN=               # optional
TWILIO_WHATSAPP_FROM=            # optional
PAPER_TRADE=false
USE_LEVERAGE=false               # enable Upstox MIS leverage (up to 5x) — use with caution
CYCLE_INTERVAL_MINUTES=5
TRADING_CAPITAL_INR=4000
CLAUDE_API_BUDGET_USD=9.00
CLAUDE_API_BUDGET_STOP_USD=8.50
MIN_CONFIDENCE_THRESHOLD=0.80
MIN_DAILY_TRADED_VALUE_CR=1
MIN_DAILY_VOLUME=50000
MAX_BID_ASK_SPREAD_PCT=0.5
TOP_CANDIDATES=50
```

---

## Testing

**Unit tests** (one file per module, all external calls mocked):
- `tests/test_logger.py` — existing, complete
- `tests/test_indicators.py` — verify indicator computations and scoring against known inputs; verify top-50 selection
- `tests/test_claude_engine.py` — mock Claude API; verify prompt structure, caching headers, skip-if-unchanged logic, confidence downgrade to HOLD
- `tests/test_order_executor.py` — mock Upstox API; verify order placement, one-position enforcement, EOD forced close, state.json updates
- `tests/test_notifier.py` — mock Telegram/Twilio clients, verify message formatting
- `tests/test_scheduler.py` — verify market hours logic, EOD close trigger, holiday skipping, cycle orchestration

**Integration test:**
- `tests/test_full_cycle.py` — one complete pipeline cycle with all external calls mocked, verifies data flows end-to-end from data_fetcher through to logger

No tests call real Upstox or Claude APIs.

---

## Implementation Order

1. `config.py` + `.env` structure
2. `auth.py` — Upstox OAuth setup
3. `data_fetcher.py` — two-stage Upstox data fetch with filters
4. `indicators.py` — technical indicators + composite scoring + top-50 compression
5. `news.py` — RSS feed fetching and symbol matching
6. `claude_engine.py` — Claude integration with caching, budget tracking, skip logic
7. `order_executor.py` + `state.json` — order placement with one-position enforcement
8. `notifier.py` — Telegram + WhatsApp
9. `scheduler.py` — loop orchestration with holiday awareness
10. `dashboard/` — FastAPI + UI
11. `main.py` — entry point launching both processes
12. Full test suite
