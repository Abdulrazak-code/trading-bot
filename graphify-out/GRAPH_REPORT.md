# Graph Report - .  (2026-04-29)

## Corpus Check
- Corpus is ~7,748 words - fits in a single context window. You may not need a graph.

## Summary
- 226 nodes · 252 edges · 31 communities detected
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 18 edges (avg confidence: 0.81)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Dependencies & Test Infrastructure|Dependencies & Test Infrastructure]]
- [[_COMMUNITY_Order Execution Engine|Order Execution Engine]]
- [[_COMMUNITY_Trade Safety Rules|Trade Safety Rules]]
- [[_COMMUNITY_Market Data Fetcher|Market Data Fetcher]]
- [[_COMMUNITY_Order Executor Tests|Order Executor Tests]]
- [[_COMMUNITY_Scheduler & News Pipeline|Scheduler & News Pipeline]]
- [[_COMMUNITY_Auth & Liquidity Filters|Auth & Liquidity Filters]]
- [[_COMMUNITY_Market Hours & Scheduler|Market Hours & Scheduler]]
- [[_COMMUNITY_Technical Analysis Libraries|Technical Analysis Libraries]]
- [[_COMMUNITY_Claude Engine Tests|Claude Engine Tests]]
- [[_COMMUNITY_Claude Decision Engine|Claude Decision Engine]]
- [[_COMMUNITY_News Fetcher|News Fetcher]]
- [[_COMMUNITY_Indicators Tests|Indicators Tests]]
- [[_COMMUNITY_Scheduler Tests|Scheduler Tests]]
- [[_COMMUNITY_Notifier|Notifier]]
- [[_COMMUNITY_Config & Environment|Config & Environment]]
- [[_COMMUNITY_Dashboard UI|Dashboard UI]]
- [[_COMMUNITY_State Management|State Management]]
- [[_COMMUNITY_Logger|Logger]]
- [[_COMMUNITY_Design Gaps - Critical|Design Gaps - Critical]]
- [[_COMMUNITY_Design Gaps - High|Design Gaps - High]]
- [[_COMMUNITY_Design Rationale|Design Rationale]]
- [[_COMMUNITY_Integration Tests|Integration Tests]]
- [[_COMMUNITY_Design Gaps - MediumLow|Design Gaps - Medium/Low]]
- [[_COMMUNITY_Indicators Engine|Indicators Engine]]
- [[_COMMUNITY_Upstox API Integration|Upstox API Integration]]
- [[_COMMUNITY_Notification APIs|Notification APIs]]
- [[_COMMUNITY_Cost Control Mechanisms|Cost Control Mechanisms]]
- [[_COMMUNITY_Data Compression|Data Compression]]
- [[_COMMUNITY_Error Recovery|Error Recovery]]
- [[_COMMUNITY_Test Suite|Test Suite]]

## God Nodes (most connected - your core abstractions)
1. `claude_engine.py - Claude AI Decision Engine` - 14 edges
2. `order_executor.py - Order Placement & Enforcement` - 13 edges
3. `OrderExecutor` - 12 edges
4. `scheduler.py - Trading Loop Orchestrator` - 10 edges
5. `Scheduler` - 9 edges
6. `data_fetcher.py - Two-Stage Market Data Fetcher` - 8 edges
7. `indicators.py - Technical Indicators & Scoring` - 8 edges
8. `dashboard/server.py - FastAPI Dashboard Server` - 7 edges
9. `Notifier` - 6 edges
10. `_headers()` - 5 edges

## Surprising Connections (you probably didn't know these)
- `Scheduler` --uses--> `ClaudeEngine`  [INFERRED]
  scheduler.py → claude_engine.py
- `Scheduler` --uses--> `Notifier`  [INFERRED]
  scheduler.py → notifier.py
- `Scheduler` --uses--> `OrderExecutor`  [INFERRED]
  scheduler.py → order_executor.py
- `claude_engine.py - Claude AI Decision Engine` --references--> `anthropic SDK (>=0.40.0)`  [INFERRED]
  docs/superpowers/specs/2026-04-29-trading-bot-design.md → requirements.txt
- `data_fetcher.py - Two-Stage Market Data Fetcher` --references--> `requests (>=2.31.0)`  [INFERRED]
  docs/superpowers/specs/2026-04-29-trading-bot-design.md → requirements.txt

## Hyperedges (group relationships)
- **One Trading Cycle Pipeline** — spec_scheduler_py, spec_data_fetcher_py, spec_indicators_py, spec_news_py, spec_claude_engine_py, spec_order_executor_py, spec_logger_py, spec_notifier_py [EXTRACTED 1.00]
- **Claude API Cost Control Mechanisms** — spec_concept_prompt_caching, spec_concept_skip_if_unchanged, spec_concept_budget_limit, spec_concept_compressed_packet [EXTRACTED 1.00]
- **Trade Safety & Risk Management Rules** — spec_concept_one_position, spec_concept_confidence_threshold, spec_concept_eod_force_close, spec_concept_liquidity_filter, spec_concept_bid_ask_filter [EXTRACTED 1.00]
- **Full Test Suite** — spec_test_logger, spec_test_indicators, spec_test_claude_engine, spec_test_order_executor, spec_test_notifier, spec_test_scheduler, spec_test_full_cycle [EXTRACTED 1.00]
- **Critical Open Questions Before Implementation** — spec_gap1_no_stoploss, spec_gap2_token_refresh, spec_gap3_eod_order_type, spec_gap4_circuit_breaker [EXTRACTED 1.00]
- **Two-Process Architecture: Trading Loop + Dashboard** — spec_main_py, spec_concept_trading_loop, spec_concept_dashboard, spec_state_json [EXTRACTED 1.00]

## Communities

### Community 0 - "Dependencies & Test Infrastructure"
Cohesion: 0.09
Nodes (25): anthropic SDK (>=0.40.0), FastAPI (>=0.110.0), pytest (>=8.0.0), pytest-mock (>=3.0.0), uvicorn (>=0.27.0), claude_engine.py - Claude AI Decision Engine, Claude claude-sonnet-4-6 - AI Decision Brain, Claude API Hard Budget Limit ($8.50 USD) (+17 more)

### Community 1 - "Order Execution Engine"
Cohesion: 0.16
Nodes (6): calculate_charges(), _headers(), OrderExecutor, Return total Upstox round-trip charges for a trade of the given sell value., Returns True if stock appears circuit-locked (spread > 5% or near-zero depth)., Re-check MIS eligibility before placing a BUY.

### Community 2 - "Trade Safety Rules"
Cohesion: 0.12
Nodes (17): httpx (>=0.27.0), EOD Force-Close at 3:15 PM IST, One-Position-At-A-Time Rule, Paper Trade Mode - Simulated Order Execution, GAP-11: Paper Trade Mode Doesn't Simulate Fills (MEDIUM), GAP-12: No Drawdown / Capital Preservation Limit (LOW), GAP-13: MIS Eligibility List Not Refreshed Mid-Day (LOW), GAP-14: No Backtesting Before Go-Live (LOW) (+9 more)

### Community 3 - "Market Data Fetcher"
Cohesion: 0.15
Nodes (13): apply_liquidity_filter(), apply_spread_filter(), get_funds(), get_instruments_nse(), get_market_quotes_ltp(), get_ohlcv(), _headers(), Return available cash (INR) from Upstox. (+5 more)

### Community 4 - "Order Executor Tests"
Cohesion: 0.13
Nodes (0): 

### Community 5 - "Scheduler & News Pipeline"
Cohesion: 0.15
Nodes (13): feedparser (>=6.0.10), schedule (>=1.2.0), Trading Loop - 5-min Scheduled Pipeline, GAP-9: News Deduplication Missing (MEDIUM), news.py - RSS News Fetcher, notifier.py - Telegram/WhatsApp Alerts, Rationale: Free RSS News to Avoid Additional API Costs, RSS Feeds - MoneyControl & Economic Times News (+5 more)

### Community 6 - "Auth & Liquidity Filters"
Cohesion: 0.18
Nodes (11): requests (>=2.31.0), auth.py - Upstox OAuth Setup, Bid-Ask Spread Filter (<=0.5%), Liquidity Filter - Volume & Traded Value Threshold, MIS (Margin Intraday Square-off) Order Type, data_fetcher.py - Two-Stage Market Data Fetcher, GAP-2: Upstox Token Refresh Undefined (CRITICAL), GAP-5: REST Polling Rate Limit Risk (HIGH) (+3 more)

### Community 7 - "Market Hours & Scheduler"
Cohesion: 0.38
Nodes (4): is_eod_close_time(), is_market_open(), ist_now(), Scheduler

### Community 8 - "Technical Analysis Libraries"
Cohesion: 0.25
Nodes (9): numpy (>=1.24.0), pandas (>=2.0.0), ta - Technical Analysis Library (>=0.11.0), Composite Momentum Score - Top 50 Stock Selector, Compressed Data Packet - 40-token Stock Summary, Technical Indicators: RSI, MACD, Bollinger Bands, EMA, VWAP, ATR, indicators.py - Technical Indicators & Scoring, Rationale: Token Compression to Stretch $9 API Budget (+1 more)

### Community 9 - "Claude Engine Tests"
Cohesion: 0.46
Nodes (5): _mock_response(), _state(), test_decide_downgrades_low_confidence_to_hold(), test_decide_returns_buy(), test_decide_returns_hold_on_json_parse_failure()

### Community 10 - "Claude Decision Engine"
Cohesion: 0.43
Nodes (4): _candidates_hash(), ClaudeEngine, Decision, _estimate_cost_usd()

### Community 11 - "News Fetcher"
Cohesion: 0.38
Nodes (6): _entry_age_hours(), fetch_headlines(), _hash(), match_headlines_to_symbols(), Fetch fresh headlines from RSS feeds, skipping already-seen or stale entries., Match headline text to stock symbols by substring search.

### Community 12 - "Indicators Tests"
Cohesion: 0.38
Nodes (3): _make_ohlcv(), test_compute_indicators_returns_required_keys(), test_compute_indicators_rsi_in_range()

### Community 13 - "Scheduler Tests"
Cohesion: 0.48
Nodes (5): _ist(), test_eod_close_triggers_at_3_15(), test_market_closed_after_close(), test_market_closed_before_open(), test_market_open_during_hours()

### Community 14 - "Notifier"
Cohesion: 0.4
Nodes (1): Notifier

### Community 15 - "Config & Environment"
Cohesion: 0.33
Nodes (0): 

### Community 16 - "Dashboard UI"
Cohesion: 0.4
Nodes (0): 

### Community 17 - "State Management"
Cohesion: 0.5
Nodes (2): notifier(), test_format_trade_alert()

### Community 18 - "Logger"
Cohesion: 0.5
Nodes (0): 

### Community 19 - "Design Gaps - Critical"
Cohesion: 0.5
Nodes (0): 

### Community 20 - "Design Gaps - High"
Cohesion: 0.5
Nodes (0): 

### Community 21 - "Design Rationale"
Cohesion: 0.67
Nodes (2): _mock_claude_response(), test_full_cycle_buy_then_stop_loss()

### Community 22 - "Integration Tests"
Cohesion: 0.67
Nodes (2): pytest_configure(), Set dummy environment variables required by config.py for test collection.

### Community 23 - "Design Gaps - Medium/Low"
Cohesion: 0.67
Nodes (0): 

### Community 24 - "Indicators Engine"
Cohesion: 0.67
Nodes (3): python-dotenv (>=1.0.0), config.py - Environment Variable Loader, .env - Environment Variables Configuration

### Community 25 - "Upstox API Integration"
Cohesion: 1.0
Nodes (0): 

### Community 26 - "Notification APIs"
Cohesion: 1.0
Nodes (0): 

### Community 27 - "Cost Control Mechanisms"
Cohesion: 1.0
Nodes (0): 

### Community 28 - "Data Compression"
Cohesion: 1.0
Nodes (0): 

### Community 29 - "Error Recovery"
Cohesion: 1.0
Nodes (0): 

### Community 30 - "Test Suite"
Cohesion: 1.0
Nodes (1): Trading Bot Design Spec

## Knowledge Gaps
- **61 isolated node(s):** `Set dummy environment variables required by config.py for test collection.`, `Return available cash (INR) from Upstox.`, `Download NSE EQ instruments from Upstox instrument master.`, `Fetch LTP + depth for up to 500 instruments at once.`, `Fetch intraday OHLCV candles for a single instrument.` (+56 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Upstox API Integration`** (2 nodes): `config.py`, `_require()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Notification APIs`** (2 nodes): `log_trade()`, `logger.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Cost Control Mechanisms`** (1 nodes): `graphify_ast_extract.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Data Compression`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Error Recovery`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Test Suite`** (1 nodes): `Trading Bot Design Spec`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `scheduler.py - Trading Loop Orchestrator` connect `Scheduler & News Pipeline` to `Dependencies & Test Infrastructure`, `Technical Analysis Libraries`, `Trade Safety Rules`, `Auth & Liquidity Filters`?**
  _High betweenness centrality (0.058) - this node is a cross-community bridge._
- **Why does `order_executor.py - Order Placement & Enforcement` connect `Trade Safety Rules` to `Dependencies & Test Infrastructure`, `Scheduler & News Pipeline`, `Auth & Liquidity Filters`?**
  _High betweenness centrality (0.046) - this node is a cross-community bridge._
- **Why does `claude_engine.py - Claude AI Decision Engine` connect `Dependencies & Test Infrastructure` to `Technical Analysis Libraries`, `Trade Safety Rules`, `Scheduler & News Pipeline`?**
  _High betweenness centrality (0.038) - this node is a cross-community bridge._
- **Are the 3 inferred relationships involving `Scheduler` (e.g. with `ClaudeEngine` and `OrderExecutor`) actually correct?**
  _`Scheduler` has 3 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Set dummy environment variables required by config.py for test collection.`, `Return available cash (INR) from Upstox.`, `Download NSE EQ instruments from Upstox instrument master.` to the rest of the system?**
  _61 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Dependencies & Test Infrastructure` be split into smaller, more focused modules?**
  _Cohesion score 0.09 - nodes in this community are weakly interconnected._
- **Should `Trade Safety Rules` be split into smaller, more focused modules?**
  _Cohesion score 0.12 - nodes in this community are weakly interconnected._