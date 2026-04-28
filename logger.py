import csv
import os
from datetime import datetime

LOG_FILE = "trades.csv"
_HEADERS = ["timestamp", "action", "coin", "amount_inr", "price", "reason", "balance_after", "error"]


def log_trade(action, coin, amount_inr, price, reason, balance_after, error=""):
    file_exists = os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_HEADERS)
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "action": action,
            "coin": coin or "",
            "amount_inr": amount_inr or 0,
            "price": price or 0,
            "reason": reason or "",
            "balance_after": balance_after or 0,
            "error": error,
        })
