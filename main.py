import multiprocessing
import os

import uvicorn

import config
from scheduler import Scheduler


def run_dashboard():
    os.environ["STATE_PATH"] = "state.json"
    os.environ["TRADES_PATH"] = "trades.csv"
    uvicorn.run("dashboard.server:app", host="0.0.0.0", port=8000, log_level="warning")


def run_trading():
    Scheduler().start()


if __name__ == "__main__":
    dashboard_proc = multiprocessing.Process(target=run_dashboard, daemon=True)
    dashboard_proc.start()
    print("Dashboard running at http://localhost:8000")
    run_trading()
