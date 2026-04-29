# tests/test_indicators.py
import pandas as pd
import numpy as np
import pytest
from indicators import compute_indicators, score_and_rank, compress_packet


def _make_ohlcv(n=50, base_price=100.0):
    np.random.seed(42)
    dates = pd.date_range("2026-01-01", periods=n, freq="5min")
    prices = base_price + np.cumsum(np.random.randn(n) * 0.5)
    return pd.DataFrame({
        "open": prices * 0.999,
        "high": prices * 1.002,
        "low": prices * 0.998,
        "close": prices,
        "volume": np.random.randint(10000, 100000, n).astype(float),
    }, index=dates)


def test_compute_indicators_returns_required_keys():
    df = _make_ohlcv(60)
    result = compute_indicators(df)
    for key in ["rsi", "macd_signal", "vwap_pct", "bb_position", "atr", "volume_spike", "price"]:
        assert key in result, f"Missing key: {key}"


def test_compute_indicators_rsi_in_range():
    df = _make_ohlcv(60)
    result = compute_indicators(df)
    assert 0 <= result["rsi"] <= 100


def test_score_and_rank_returns_top_n():
    candidates = {
        f"STOCK{i}": {
            "price": 100.0, "volume_spike": float(i), "rsi": 50.0,
            "macd_signal": 0.1, "vwap_pct": 0.0, "spread_pct": 0.2,
            "bb_position": 0.5, "atr": 1.0,
        }
        for i in range(100)
    }
    top = score_and_rank(candidates, n=50)
    assert len(top) == 50


def test_score_and_rank_prefers_high_volume_spike():
    candidates = {
        "LOW_VOL": {"price": 100.0, "volume_spike": 0.5, "rsi": 50.0,
                    "macd_signal": 0.0, "vwap_pct": 0.0, "spread_pct": 0.2,
                    "bb_position": 0.5, "atr": 1.0},
        "HIGH_VOL": {"price": 100.0, "volume_spike": 5.0, "rsi": 50.0,
                     "macd_signal": 0.0, "vwap_pct": 0.0, "spread_pct": 0.2,
                     "bb_position": 0.5, "atr": 1.0},
    }
    top = score_and_rank(candidates, n=2)
    assert list(top.keys())[0] == "HIGH_VOL"


def test_compress_packet_length_and_content():
    packet = compress_packet("RELIANCE", {
        "price": 2450.0, "volume_spike": 2.3, "rsi": 58.0,
        "macd_signal": 0.5, "vwap_pct": 0.8, "spread_pct": 0.1,
        "bb_position": 0.6, "atr": 18.5,
    }, headlines=["Reliance Q4 profit up 12%"])
    assert len(packet) < 200
    assert "RELIANCE" in packet
