import csv
import json
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()
_STATE_PATH = os.getenv("STATE_PATH", "state.json")
_TRADES_PATH = os.getenv("TRADES_PATH", "trades.csv")

_static = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_static)), name="static")


@app.get("/", response_class=HTMLResponse)
def root():
    return HTMLResponse(content=(_static / "index.html").read_text(encoding="utf-8"), media_type="text/html; charset=utf-8")


@app.get("/api/state")
def api_state():
    if not os.path.exists(_STATE_PATH):
        return JSONResponse({"error": "state.json not found"}, status_code=404)
    with open(_STATE_PATH, encoding="utf-8") as f:
        return JSONResponse(json.load(f))


_NEW_FIELDS = ["timestamp", "action", "coin", "amount_inr", "price", "confidence", "reason", "balance_after", "error"]
_OLD_FIELDS = ["timestamp", "action", "coin", "amount_inr", "price", "reason", "balance_after", "error"]


@app.get("/api/trades")
def api_trades():
    if not os.path.exists(_TRADES_PATH):
        return JSONResponse([])
    rows = []
    with open(_TRADES_PATH, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        next(reader, None)  # skip the file header row (may be stale/old format)
        for row in reader:
            if len(row) >= 9:
                d = dict(zip(_NEW_FIELDS, row))
            else:
                d = dict(zip(_OLD_FIELDS, row))
                d["confidence"] = ""
            rows.append(d)
    return JSONResponse(rows[-100:])
