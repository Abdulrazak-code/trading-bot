"""
Tradeable universe: NIFTY 50 + NIFTY NEXT 50 (large-caps only).

Restricting to large-caps avoids the slippage and erratic moves of small-caps,
which historically caused the bot's worst losses (DIACABS, CUPID, VIKRAN, SUVEN).

Index composition drifts over time — review this list each quarter after NSE
index rebalancing. Matched against Upstox `trading_symbol`.
"""

NIFTY_50 = {
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL",
    "BPCL", "BRITANNIA", "CIPLA", "COALINDIA", "DRREDDY",
    "EICHERMOT", "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE",
    "HEROMOTOCO", "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDUSINDBK",
    "INFY", "ITC", "JSWSTEEL", "JIOFIN", "KOTAKBANK",
    "LT", "LTIM", "M&M", "MARUTI", "NESTLEIND",
    "NTPC", "ONGC", "POWERGRID", "RELIANCE", "SBILIFE",
    "SBIN", "SHRIRAMFIN", "SUNPHARMA", "TCS", "TATACONSUM",
    "TATAMOTORS", "TATASTEEL", "TECHM", "TITAN", "TRENT",
    "ULTRACEMCO", "WIPRO",
}

NIFTY_NEXT_50 = {
    "ABB", "ADANIENSOL", "ADANIGREEN", "ADANIPOWER", "AMBUJACEM",
    "DMART", "BAJAJHLDNG", "BANKBARODA", "BERGEPAINT", "BOSCHLTD",
    "CANBK", "CHOLAFIN", "COLPAL", "DABUR", "DLF",
    "DIVISLAB", "GAIL", "GODREJCP", "HAVELLS", "HAL",
    "ICICIGI", "ICICIPRULI", "IOC", "IRCTC", "INDIGO",
    "NAUKRI", "JINDALSTEL", "JUBLFOOD", "LICI", "MARICO",
    "MOTHERSON", "MUTHOOTFIN", "PIIND", "PIDILITIND", "PFC",
    "PNB", "RECLTD", "SIEMENS", "SRF", "TVSMOTOR",
    "TATAPOWER", "TORNTPHARM", "UNITDSPR", "VBL", "VEDL",
    "ZYDUSLIFE", "GICRE", "IRFC", "BANKINDIA", "ETERNAL",
}

# Union used for filtering — ~100 large-cap names.
TRADEABLE_UNIVERSE = NIFTY_50 | NIFTY_NEXT_50


def in_universe(trading_symbol: str) -> bool:
    return trading_symbol.upper() in TRADEABLE_UNIVERSE
