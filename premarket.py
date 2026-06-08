"""
Pre-market analysis script.
Runs at 8:30 AM IST, fetches global cues + PCR + news, asks Claude,
then sends a structured report to Telegram.
"""

import json
import re
import time
from datetime import datetime, timezone, timedelta

import requests
import yfinance as yf
import anthropic

import config
from data_fetcher import get_instruments_nse, get_market_quotes_ltp
from news import fetch_headlines
from notifier import Notifier

_IST = timezone(timedelta(hours=5, minutes=30))
_NIFTY_OPTION_KEY  = "NSE_INDEX|Nifty 50"
_UPSTOX_BASE       = "https://api.upstox.com/v2"
_UPSTOX_HEADERS    = {
    "Authorization": f"Bearer {config.UPSTOX_ACCESS_TOKEN}",
    "Accept": "application/json",
}


# ── 1. Global cues via yfinance ──────────────────────────────────────────────

def _pct(ticker: str, period: str = "2d") -> float:
    """Return latest 1-day % change for a ticker."""
    try:
        hist = yf.Ticker(ticker).history(period=period)
        if len(hist) >= 2:
            prev  = float(hist["Close"].iloc[-2])
            last  = float(hist["Close"].iloc[-1])
            return round((last - prev) / prev * 100, 2) if prev else 0.0
        return 0.0
    except Exception:
        return 0.0


def get_global_cues() -> dict:
    tickers = {
        "SP500":      "^GSPC",
        "Dow":        "^DJI",
        "Nasdaq":     "^IXIC",
        "Nikkei":     "^N225",
        "HangSeng":   "^HSI",
        "NIFTY50":    "^NSEI",
        "BankNifty":  "^NSEBANK",
    }
    return {name: _pct(sym) for name, sym in tickers.items()}


# ── 2. Option chain — PCR and max pain ──────────────────────────────────────

def _get_nearest_expiry() -> str:
    resp = requests.get(
        f"{_UPSTOX_BASE}/option/contract",
        headers=_UPSTOX_HEADERS,
        params={"instrument_key": _NIFTY_OPTION_KEY},
        timeout=10,
    )
    resp.raise_for_status()
    contracts = resp.json().get("data", [])
    # Each entry is a contract dict — extract unique expiry dates
    dates = sorted(set(c["expiry"] for c in contracts if "expiry" in c))
    today = str(datetime.now(_IST).date())
    future = [d for d in dates if d >= today]
    return future[0] if future else dates[-1]


def get_pcr_and_maxpain(expiry: str) -> dict:
    resp = requests.get(
        f"{_UPSTOX_BASE}/option/chain",
        headers=_UPSTOX_HEADERS,
        params={"instrument_key": _NIFTY_OPTION_KEY, "expiry_date": expiry},
        timeout=15,
    )
    resp.raise_for_status()
    chain = resp.json().get("data", [])

    total_ce_oi = 0
    total_pe_oi = 0
    strikes     = []

    for row in chain:
        strike = float(row.get("strike_price", 0))
        ce_oi  = float((row.get("call_options") or {}).get("market_data", {}).get("oi", 0))
        pe_oi  = float((row.get("put_options")  or {}).get("market_data", {}).get("oi", 0))
        ce_ltp = float((row.get("call_options") or {}).get("market_data", {}).get("ltp", 0))
        pe_ltp = float((row.get("put_options")  or {}).get("market_data", {}).get("ltp", 0))
        total_ce_oi += ce_oi
        total_pe_oi += pe_oi
        strikes.append({"strike": strike, "ce_oi": ce_oi, "pe_oi": pe_oi,
                        "ce_ltp": ce_ltp, "pe_ltp": pe_ltp})

    pcr = round(total_pe_oi / total_ce_oi, 3) if total_ce_oi else 0.0

    # Max pain: strike where total option writer loss is minimum
    max_pain = 0
    min_loss  = float("inf")
    for s in strikes:
        sp = s["strike"]
        loss = sum(
            max(0, sp - r["strike"]) * r["ce_oi"] +
            max(0, r["strike"] - sp) * r["pe_oi"]
            for r in strikes
        )
        if loss < min_loss:
            min_loss  = loss
            max_pain  = sp

    # Top 5 strikes by total OI (support/resistance levels)
    top_oi = sorted(strikes, key=lambda x: x["ce_oi"] + x["pe_oi"], reverse=True)[:5]

    return {
        "pcr":      pcr,
        "max_pain": max_pain,
        "expiry":   expiry,
        "top_oi_strikes": [int(r["strike"]) for r in top_oi],
        "total_ce_oi": int(total_ce_oi),
        "total_pe_oi": int(total_pe_oi),
    }


# ── 3. Previous day top movers from Upstox ──────────────────────────────────

def get_top_movers(n: int = 10) -> list:
    """Return top n stocks by volume from live quotes (proxy for yesterday's active stocks)."""
    try:
        instruments = get_instruments_nse()
        keys = [i["instrument_key"] for i in instruments[:300]]
        key_to_sym = {i["instrument_key"]: i["trading_symbol"] for i in instruments}
        quotes = get_market_quotes_ltp(keys)
        movers = []
        for k, q in quotes.items():
            price  = float(q.get("last_price", 0))
            volume = float(q.get("volume", 0))
            if price > 0 and price <= 1000:
                movers.append({"symbol": key_to_sym.get(k, k), "price": price, "volume": volume})
        movers.sort(key=lambda x: x["volume"], reverse=True)
        return movers[:n]
    except Exception:
        return []


# ── 4. Claude analysis ───────────────────────────────────────────────────────

_PREMARKET_PROMPT = """You are a pre-market analyst for Indian equity markets (NSE/BSE).
Given global cues, options data, and top active stocks, produce a concise pre-market report.

Output ONLY valid JSON with this exact structure:
{
  "market_outlook": "BULLISH"|"BEARISH"|"NEUTRAL",
  "expected_gap": "<e.g. Gap up 0.2-0.4%>",
  "pcr_signal": "<one line interpretation of PCR>",
  "max_pain_note": "<one line about max pain and its implication>",
  "sectors_to_watch": ["<sector1>", "<sector2>"],
  "stocks_to_watch": [
    {"symbol": "<SYMBOL>", "reason": "<one line>"},
    {"symbol": "<SYMBOL>", "reason": "<one line>"},
    {"symbol": "<SYMBOL>", "reason": "<one line>"}
  ],
  "strategy": "<2-3 sentence trading strategy for the day>",
  "risk_note": "<one line key risk to watch>"
}"""


def run_claude_analysis(global_cues: dict, options: dict, top_movers: list, news: list) -> dict:
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, max_retries=3, timeout=30.0)

    cues_str = "\n".join(f"  {k}: {v:+.2f}%" for k, v in global_cues.items())
    movers_str = "\n".join(
        f"  {m['symbol']}: Rs{m['price']:.1f} vol={m['volume']:,.0f}"
        for m in top_movers
    )
    news_str = "\n".join(f"  - {n}" for n in news[:8]) if news else "  No major headlines."
    pcr = options.get("pcr", 0)
    pcr_signal = "Bullish" if pcr > 1.2 else "Bearish" if pcr < 0.8 else "Neutral"

    user_content = f"""Pre-market data for {datetime.now(_IST).strftime('%d %b %Y')}:

GLOBAL CUES:
{cues_str}

OPTIONS (Expiry: {options.get('expiry')}):
  PCR: {pcr} ({pcr_signal})
  Max Pain: {options.get('max_pain')}
  Top OI strikes (support/resistance): {options.get('top_oi_strikes')}
  Total CE OI: {options.get('total_ce_oi'):,} | PE OI: {options.get('total_pe_oi'):,}

TOP ACTIVE STOCKS (by volume):
{movers_str}

RECENT NEWS:
{news_str}

Produce the pre-market report JSON."""

    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            system=[{"type": "text", "text": _PREMARKET_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_content}],
        )
        raw = msg.content[0].text.strip()
        m   = re.search(r'\{.*\}', raw, re.DOTALL)
        return json.loads(m.group()) if m else {}
    except Exception as e:
        return {"error": str(e)}


# ── 5. Format and send Telegram report ──────────────────────────────────────

def _format_report(report: dict, global_cues: dict, options: dict) -> str:
    outlook = report.get("market_outlook", "NEUTRAL")
    emoji   = "🟢" if outlook == "BULLISH" else "🔴" if outlook == "BEARISH" else "🟡"

    lines = [
        f"{emoji} PRE-MARKET REPORT — {datetime.now(_IST).strftime('%d %b %Y %H:%M')} IST",
        "",
        f"Outlook: {outlook} | {report.get('expected_gap', '')}",
        "",
        "── GLOBAL CUES ──",
    ]
    for name, pct in global_cues.items():
        arrow = "▲" if pct > 0 else "▼" if pct < 0 else "─"
        lines.append(f"  {name}: {arrow} {pct:+.2f}%")

    lines += [
        "",
        "── OPTIONS ──",
        f"  PCR: {options.get('pcr')} — {report.get('pcr_signal', '')}",
        f"  Max Pain: {options.get('max_pain')} — {report.get('max_pain_note', '')}",
        f"  Key OI levels: {options.get('top_oi_strikes')}",
        "",
        "── SECTORS TO WATCH ──",
    ]
    for sec in report.get("sectors_to_watch", []):
        lines.append(f"  • {sec}")

    lines += ["", "── STOCKS TO WATCH ──"]
    for s in report.get("stocks_to_watch", []):
        lines.append(f"  {s.get('symbol')}: {s.get('reason')}")

    lines += [
        "",
        "── STRATEGY ──",
        report.get("strategy", ""),
        "",
        f"⚠️  {report.get('risk_note', '')}",
    ]
    return "\n".join(lines)


# ── 6. Main entry point ──────────────────────────────────────────────────────

def run_premarket_analysis():
    notifier = Notifier(
        telegram_token=config.TELEGRAM_BOT_TOKEN,
        chat_id=config.TELEGRAM_CHAT_ID,
    )
    print(f"[{datetime.now(_IST).strftime('%H:%M')}] Running pre-market analysis...")

    notifier.send("Pre-market analysis starting... ⏳")

    print("  Fetching global cues...")
    global_cues = get_global_cues()

    print("  Fetching option chain (PCR + max pain)...")
    try:
        expiry  = _get_nearest_expiry()
        options = get_pcr_and_maxpain(expiry)
    except Exception as e:
        print(f"  Option chain error: {e}")
        options = {"pcr": 0, "max_pain": 0, "expiry": "unknown",
                   "top_oi_strikes": [], "total_ce_oi": 0, "total_pe_oi": 0}

    print("  Fetching top active stocks...")
    top_movers = get_top_movers(10)

    print("  Fetching news headlines...")
    try:
        news_entries, _ = fetch_headlines(set())
        # fetch_headlines returns (hash, title) tuples, not dicts
        news = [title for _h, title in news_entries[:8] if title]
    except Exception:
        news = []

    print("  Running Claude analysis...")
    report = run_claude_analysis(global_cues, options, top_movers, news)

    if "error" in report:
        notifier.send(f"Pre-market analysis failed: {report['error']}")
        return

    message = _format_report(report, global_cues, options)
    print("\n" + message.encode("ascii", errors="replace").decode("ascii"))
    notifier.send(message)

    # Save structured report so the trading bot's Claude engine can reference it all day
    report_to_save = {
        "date": datetime.now(_IST).strftime("%Y-%m-%d"),
        "generated_at": datetime.now(_IST).strftime("%H:%M"),
        "market_outlook": report.get("market_outlook", "NEUTRAL"),
        "expected_gap": report.get("expected_gap", ""),
        "pcr": options.get("pcr", 0),
        "pcr_signal": report.get("pcr_signal", ""),
        "max_pain": options.get("max_pain", 0),
        "max_pain_note": report.get("max_pain_note", ""),
        "top_oi_strikes": options.get("top_oi_strikes", []),
        "sectors_to_watch": report.get("sectors_to_watch", []),
        "stocks_to_watch": [s.get("symbol") for s in report.get("stocks_to_watch", [])],
        "strategy": report.get("strategy", ""),
        "risk_note": report.get("risk_note", ""),
        "global_cues": global_cues,
    }
    with open("premarket_report.json", "w") as f:
        json.dump(report_to_save, f, indent=2)
    print(f"[{datetime.now(_IST).strftime('%H:%M')}] Pre-market report sent to Telegram and saved to premarket_report.json")


if __name__ == "__main__":
    run_premarket_analysis()
