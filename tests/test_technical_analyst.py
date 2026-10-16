"""The Technical Analyst, on synthetic prices (no network)."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from analysis import technical as ta
from indicators import core
from scoring import timing


@pytest.fixture
def cfg():
    return timing.load_config()


def frame(close, volume=2e6, spread=0.01):
    close = pd.Series(close, dtype=float)
    n = len(close)
    df = pd.DataFrame({
        "date": [dt.date(2020, 1, 1) + dt.timedelta(days=i) for i in range(n)],
        "open": close.shift(1).fillna(close.iloc[0]).to_numpy(),
        "high": (close * (1 + spread)).to_numpy(),
        "low": (close * (1 - spread)).to_numpy(),
        "close": close.to_numpy(),
        "volume": np.full(n, volume),
    })
    df["bench_close"] = np.linspace(100, 110, n)
    return timing.compute(core.compute_all(df))


def wave(n, start, end, amp=0.06, period=40, seed=1):
    rng = np.random.default_rng(seed)
    base = np.linspace(start, end, n)
    return base * (1 + amp * np.sin(np.arange(n) * 2 * np.pi / period)
                   + rng.normal(0, 0.003, n))


def test_uptrend(cfg):
    r = ta.analyse("UP", frame(wave(400, 100, 250)), cfg)
    assert r.stance == "Uptrend"
    assert r.score is not None and set(r.buckets) == {
        "trend", "momentum", "relative strength", "volume", "volatility"}
    assert any("above the 200 DMA" in p for p in r.positives)
    assert r.returns[-1]["excess_pct"] > 0       # beat a benchmark up 10%


def test_downtrend(cfg):
    r = ta.analyse("DN", frame(wave(400, 250, 100)), cfg)
    assert r.stance == "Downtrend"
    assert any("below the 200 DMA" in n for n in r.negatives)
    assert "Underperformed" in r.summary


def test_levels_bracket_the_close(cfg):
    f = frame(wave(400, 100, 250))
    r = ta.analyse("UP", f, cfg)
    assert r.supports and all(l.price < r.close for l in r.supports)
    assert all(l.price > r.close for l in r.resistances)
    # Nearest first.
    assert [l.price for l in r.supports] == sorted((l.price for l in r.supports), reverse=True)


def test_repeated_swing_highs_cluster_into_one_level():
    # A range: the same highs and lows, touched many times.
    groups = ta.cluster([100, 100.5, 99.8, 120, 120.4], tolerance_pct=1.5)
    assert groups[0][1] == 3 and round(groups[0][0]) == 100
    assert groups[1][1] == 2


def test_overbought_uptrend_is_stretched(cfg):
    close = np.concatenate([wave(380, 100, 200), np.linspace(200, 280, 20)])
    r = ta.analyse("HOT", frame(close), cfg)
    assert r.stance == "Uptrend" and r.qualifier == "stretched"


def test_short_history(cfg):
    r = ta.analyse("NEW", frame(wave(40, 100, 110)), cfg)
    assert r.stance == "Not enough data"
    r = ta.analyse("YOUNG", frame(wave(120, 100, 110)), cfg)
    assert r.stance == "Not enough data" and "200 DMA" in r.summary
    assert r.chart is not None and len(r.chart) == 120


def test_ineligible_setup_says_why(cfg):
    row = pd.Series({"uptrend": False, "close": 500.0, "turnover_cr": 100.0})
    assert "not in an uptrend" in ta._why_not(row, cfg)
    row = pd.Series({"uptrend": True, "close": 500.0, "turnover_cr": 1.0})
    assert "cr a day" in ta._why_not(row, cfg)
