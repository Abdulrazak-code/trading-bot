import hashlib
from datetime import datetime, timezone

import feedparser

# Maps NSE ticker → lowercase company name fragment for headline matching.
# Tickers that match verbatim in headlines don't need an entry here.
_COMPANY_NAMES = {
    "TATAMOTORS": "tata motors",
    "HDFCBANK": "hdfc bank",
    "ICICIBANK": "icici bank",
    "KOTAKBANK": "kotak bank",
    "BAJAJFINSV": "bajaj finserv",
    "BAJFINANCE": "bajaj finance",
    "SBILIFE": "sbi life",
    "HCLTECH": "hcl tech",
    "AXISBANK": "axis bank",
    "ULTRACEMCO": "ultratech",
    "SUNPHARMA": "sun pharma",
    "ASIANPAINT": "asian paint",
    "MARUTI": "maruti suzuki",
    "ADANIPORTS": "adani ports",
    "ADANIENT": "adani enterprises",
    "ADANIGREEN": "adani green",
    "INDUSINDBK": "indusind",
    "TECHM": "tech mahindra",
    "TATASTEEL": "tata steel",
    "JSWSTEEL": "jsw steel",
    "HINDALCO": "hindalco",
    "VEDL": "vedanta",
    "IOC": "indian oil",
    "BPCL": "bharat petroleum",
    "COALINDIA": "coal india",
    "HEROMOTOCO": "hero motocorp",
    "EICHERMOT": "eicher motors",
    "DRREDDY": "dr reddy",
    "DIVISLAB": "divi",
    "APOLLOHOSP": "apollo hospital",
    "TATACONSUM": "tata consumer",
    "BAJAJ-AUTO": "bajaj auto",
}

_FEEDS = [
    "https://www.moneycontrol.com/rss/business.xml",
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
]
_MAX_AGE_HOURS = 4


def _hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def _entry_age_hours(entry) -> float:
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        pub = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - pub).total_seconds() / 3600
        return age
    return 0.0


def fetch_headlines(seen_hashes: set) -> tuple:
    """
    Fetch fresh headlines from RSS feeds, skipping already-seen or stale entries.
    Returns:
        all_entries: list of (hash, title) tuples for new headlines
        new_hashes: set of hashes for all fetched new entries
    """
    all_entries = []
    for feed_url in _FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries:
                h = _hash(getattr(entry, "link", entry.title))
                if h in seen_hashes:
                    continue
                if _entry_age_hours(entry) > _MAX_AGE_HOURS:
                    continue
                all_entries.append((h, entry.title))
        except Exception:
            pass

    new_hashes = {h for h, _ in all_entries}
    return all_entries, new_hashes


def match_headlines_to_symbols(
    raw_entries: list, symbols: list
) -> dict:
    """Match headline text to stock symbols by ticker or company name."""
    result = {s: [] for s in symbols}
    for _h, title in raw_entries:
        title_lower = title.lower()
        for sym in symbols:
            if len(result[sym]) >= 2:
                continue
            if sym.lower() in title_lower:
                result[sym].append(title)
                continue
            name = _COMPANY_NAMES.get(sym, "")
            if name and name in title_lower:
                result[sym].append(title)
    return result
