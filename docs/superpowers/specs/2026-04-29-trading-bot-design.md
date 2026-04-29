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

---

## Open Questions & Gaps
**Status:** To be resolved before implementation begins.

---

### CRITICAL

**[GAP-1] No Stop-Loss**
The only exit triggers are Claude returning SELL or the 3:15 PM force-close. There is no intraday stop-loss. A stock dropping 5% on ₹4,000 = ₹200 loss, wiping out 4+ trades worth of profit needed to cover charges.
- Decision needed: What is the stop-loss threshold (e.g., exit if position is down >2%)? Is it a hard-coded rule in `order_executor.py` or passed to Claude?
- Is it a fixed % of entry price, or ATR-based?

**[GAP-2] Upstox Token Refresh is Undefined**
`auth.py` is a one-time script. The spec says the trading loop "refreshes the token daily at startup" but Upstox OAuth tokens require a browser redirect to refresh — this cannot happen silently in an unattended bot.
- Decision needed: How does the daily token refresh actually work? Does it use a refresh token? Does Upstox support silent refresh without browser interaction? Does the user need to manually run `auth.py` every morning?

**[GAP-3] EOD Force-Close Order Type Unspecified**
"At 3:15 PM, execute forced close of all open positions" — the order type is not specified. A limit order might not fill if the market moves. A market order guarantees exit but with unknown slippage.
- Decision needed: What order type is used for EOD close? What is the fallback if the close order is rejected or partially fills? Does the bot retry? Alert and stop?

**[GAP-4] Circuit Breaker / Trading Halt Handling**
NSE stocks hit upper/lower circuits regularly and become untradeable while locked. If the bot holds a position in a stock that hits lower circuit, it cannot exit — not even at 3:15 PM.
- Decision needed: How does the bot detect that a held stock is circuit-locked? What is the recovery action? Does it alert and wait, or is there another exit mechanism?

---

### HIGH

**[GAP-5] REST Polling Will Hit Upstox Rate Limits**
200 stocks × 72 cycles/day = ~14,400 REST calls per day for price/OHLCV data alone. Upstox rate limits will be hit.
- Decision needed: Switch to Upstox WebSocket market data streaming (subscribe once, receive continuous updates, trading loop reads in-memory cache) instead of REST polling every 5 minutes? This is the standard approach for intraday systems and would reduce API calls to near-zero for price data.

**[GAP-6] Quantity Calculation Logic is Missing**
Claude returns a `quantity` in its JSON response, but the spec does not say how Claude knows what quantity to request. Claude needs to know exact available cash, current price, and the ₹45–50 charge impact to calculate a sensible quantity.
- Decision needed: Is quantity calculated by `order_executor.py` before passing to Claude (Claude only decides BUY/SELL/HOLD), or does Claude calculate it from the cash figure passed in the prompt? Either way, the formula and who owns it must be defined.

**[GAP-7] `state.json` Has No Concurrency Protection**
The trading loop (writer) and dashboard server (reader) access `state.json` simultaneously. A write mid-read will produce a corrupted/partial read, causing the dashboard to show wrong state or crash.
- Decision needed: Use atomic writes (write to a temp file, then rename) for the trading loop, or use a lightweight locking mechanism. Define who is the single writer.

---

### MEDIUM

**[GAP-8] Partial Fill Handling is Vague**
`order_executor.py` "handles partial fills" but no detail is given. If Claude says buy 100 shares and only 60 fill, what quantity is recorded in `state.json`? Does the bot try to fill the remaining 40 in the next cycle or accept the partial position?
- Decision needed: Accept partial fills as the full position (record actual filled quantity). Do not attempt to top up — it adds complexity and more charges.

**[GAP-9] News Deduplication Missing**
`news.py` fetches RSS every cycle. The same headline will reappear across many cycles, potentially biasing Claude repeatedly on stale news. No headline age filter or seen-headline tracking is mentioned.
- Decision needed: Track seen headline URLs/hashes in `state.json`. Only pass headlines newer than X hours (suggest 4 hours) to Claude. Headlines already seen in a prior cycle are marked as `[repeated]` or omitted.

**[GAP-10] Claude JSON Parse Failure Unhandled**
If Claude returns malformed JSON or a response that doesn't match the expected schema, the spec doesn't describe what happens. An unhandled parse error will crash `claude_engine.py`.
- Decision needed: On JSON parse failure, treat the cycle as HOLD, log an ERROR row, send alert, and continue. Do not retry the Claude call (it will likely produce the same output and cost more).

**[GAP-11] Paper Trade Mode Doesn't Simulate Fills**
`PAPER_TRADE=true` logs decisions without placing real orders, but `state.json` will still record an "open position." The EOD close will then attempt to close a position that never existed via the real API.
- Decision needed: Paper trade mode must fully simulate the position lifecycle in `state.json` without any Upstox API calls. EOD close in paper mode just clears the simulated position from state. Notifier still fires so alerts can be tested.

---

### LOW

**[GAP-12] No Drawdown / Capital Preservation Limit**
If the account loses ₹500 across multiple bad days, the bot continues trading with reduced capital as if nothing changed. No daily loss limit or account-floor is defined.
- Decision needed: Add a configurable `MAX_DAILY_LOSS_INR` env var (suggest ₹200). If realised P&L for the day crosses this threshold, the bot stops placing new orders for the rest of that day and sends an alert.

**[GAP-13] MIS Eligibility List Not Refreshed Mid-Day**
The MIS-eligible stock list is fetched at startup. If Upstox removes a stock from MIS eligibility during the trading day (which it can do), the bot may attempt an MIS order that gets rejected.
- Decision needed: Re-validate MIS eligibility immediately before placing any order, not just at startup.

**[GAP-14] No Backtesting**
The bot will go live with real money based solely on Claude's judgment + indicator scoring, with no historical validation of the strategy.
- Decision needed: Is backtesting in scope before go-live? At minimum, a paper-trade run of 2–3 days before switching to live orders is strongly recommended.
