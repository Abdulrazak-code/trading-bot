# Tomorrow Morning Checklist — Paper Trade Day 1
Date: 2026-04-30 | Mode: PAPER_TRADE=true

---

## Before 9:15 AM IST

- [ ] 1. Refresh Upstox token (current one expires at 3:30 AM)
        python auth.py
        → browser opens → log in → paste redirect URL
        → token saved automatically to .env

- [ ] 2. Confirm token is valid
        python -c "from auth import validate_token; import config; print('Token valid:', validate_token(config.UPSTOX_ACCESS_TOKEN))"

- [ ] 3. Start the bot
        python main.py

- [ ] 4. Open dashboard in browser
        http://localhost:8000

- [ ] 5. Check Telegram — first cycle alert should arrive by 9:20 AM IST

---

## During the Day (9:15 AM – 3:15 PM IST)

- [ ] Watch Telegram for BUY / SELL / HOLD alerts every ~5 minutes
- [ ] Note which stocks Claude picks and the reasoning it gives
- [ ] Check if Claude is doing the min_move calculation in its reasoning
- [ ] Monitor trades.csv for a running log of all decisions

---

## After 3:15 PM IST (EOD)

- [ ] Confirm EOD forced-close alert arrived on Telegram
- [ ] Review trades.csv — every decision, stock picked, reasoning
- [ ] Note: simulated P&L, charges, net profit per trade
- [ ] Check Claude API spend in dashboard (target: under $0.30 for the day)

---

## What to Evaluate After the Paper Day

Answer these before deciding to go live:

- [ ] Did the pipeline complete full cycles without errors?
- [ ] Did Claude pick stocks under ~Rs400 with viable min_move?
- [ ] Were the stocks liquid enough (clean fills, tight spreads)?
- [ ] What was the simulated net P&L after charges?
- [ ] Did EOD force-close fire at 3:15 PM?
- [ ] Any error alerts on Telegram worth investigating?

---

## What We Fixed Today (for reference)

- Upstox token validated ✓
- All credentials tested (Upstox, Anthropic, Telegram) ✓
- Fixed: instrument filter (series → instrument_type) ✓
- Fixed: OHLCV interval (5minute → 1minute) ✓
- Fixed: funds endpoint fallback to TRADING_CAPITAL_INR ✓
- Fixed: order product code (MIS → I) ✓
- Fixed: minimum viable quantity guard (qty < 10 rejected) ✓
- Fixed: Claude now calculates min_move = Rs48/qty before every BUY ✓
- Dry run completed — all components tested end-to-end ✓
- PAPER_TRADE=true confirmed in .env ✓

---

## Go / No-Go Decision After Paper Day

If clean paper day → consider adding capital before going live.
Current ₹4,000 capital means ~₹9–₹67 net profit per winning trade.
With ₹20,000 capital the same trades yield 5x more on identical % moves.
