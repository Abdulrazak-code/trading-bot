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
    return (_static / "index.html").read_text()


@app.get("/api/state")
def api_state():
    if not os.path.exists(_STATE_PATH):
        return JSONResponse({"error": "state.json not found"}, status_code=404)
    with open(_STATE_PATH) as f:
        return JSONResponse(json.load(f))


@app.get("/api/trades")
def api_trades():
    if not os.path.exists(_TRADES_PATH):
        return JSONResponse([])
    rows = []
    with open(_TRADES_PATH, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return JSONResponse(rows[-100:])
