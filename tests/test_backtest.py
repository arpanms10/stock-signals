"""Backtest integrity.

The dangerous failures here are silent: a fill at the wrong bar, or costs that
quietly do not apply, both make results look better than reality.
"""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from backtest import engine as bt
from backtest.costs import CostModel
from scoring import timing


@pytest.fixture
def cfg():
    return timing.load_config()


def series(n=400, start=100.0, end=300.0):
    dates = [dt.date(2023, 1, 2) + dt.timedelta(days=i) for i in range(n)]
    close = np.linspace(start, end, n)
    df = pd.DataFrame({
        "date": dates, "open": close * 0.999, "high": close * 1.01,
        "low": close * 0.99, "close": close, "volume": 1e6,
        "sma50": pd.Series(close).rolling(50, min_periods=1).mean(),
        "sma200": pd.Series(close).rolling(200, min_periods=1).mean(),
        "sma200_slope": 1.0, "rsi14": 55.0, "atr14": close * 0.02,
        "vol_ratio": 1.0, "macd_hist": 1.0,
    })
    df["high_52w"] = df["close"].rolling(250, min_periods=1).max()
    df["timing_score"] = 70.0
    return df


def bench_frame(n=400, rising=True):
    dates = [dt.date(2023, 1, 2) + dt.timedelta(days=i) for i in range(n)]
    close = np.linspace(100, 200, n) if rising else np.linspace(200, 100, n)
    return pd.DataFrame({"date": dates, "close": close})


def test_costs_are_actually_deducted(cfg):
    f = series()
    f["timing_score"] = [50.0] * 250 + [70.0] * (len(f) - 250)
    free = bt.run({"A": f}, bench_frame(), cfg, 1_000_000,
                  CostModel(stt_pct=0, exchange_pct=0, sebi_pct=0, stamp_duty_pct=0,
                            gst_pct=0, dp_charge=0, slippage_pct=0))
    real = bt.run({"A": f}, bench_frame(), cfg, 1_000_000, CostModel())
    assert real.equity.iloc[-1] < free.equity.iloc[-1]


def test_no_lookahead_fills_at_the_next_open(cfg):
    """A signal on bar i must fill on bar i+1, never on bar i."""
    f = series()
    f["timing_score"] = [50.0] * 250 + [70.0] * (len(f) - 250)
    res = bt.run({"A": f}, bench_frame(), cfg, 1_000_000, CostModel(slippage_pct=0))
    entries = [t for t in res.trades]
    assert entries, "expected at least one trade"
    t = entries[0]
    signal_bar = f[f["date"] < t.entry_date].iloc[-1]
    fill_bar = f[f["date"] == t.entry_date].iloc[0]
    # The fill price is the *next* bar's open, not the signal bar's close.
    assert t.entry_price == pytest.approx(fill_bar["open"], rel=1e-6)
    assert t.entry_price != pytest.approx(signal_bar["close"], rel=1e-9)


def test_regime_filter_prevents_entries_in_a_bear_market(cfg):
    f = series()
    f["timing_score"] = [50.0] * 250 + [70.0] * (len(f) - 250)
    falling = bench_frame(rising=False)
    res = bt.run({"A": f}, falling, cfg, 1_000_000)
    assert len(res.trades) == 0


def test_max_positions_is_respected(cfg):
    frames = {}
    for i in range(20):
        f = series()
        f["timing_score"] = [50.0] * 250 + [70.0] * (len(f) - 250)
        frames[f"S{i}"] = f
    res = bt.run(frames, bench_frame(), cfg, 10_000_000)
    # Count concurrently open *positions*. A scale-out books part of an existing
    # position and is not a new one, so its leg is merged rather than counted
    # again -- otherwise every partially booked position would look like two.
    spans: dict[tuple, list] = {}
    for t in res.trades:
        key = (t.symbol, t.entry_date)
        end = t.exit_date or dt.date(2100, 1, 1)
        if key in spans:
            spans[key][1] = max(spans[key][1], end)
        else:
            spans[key] = [t.entry_date, end]
    events = []
    for start, end in spans.values():
        events.append((start, 1))
        events.append((end, -1))
    peak, cur = 0, 0
    for _, delta in sorted(events):
        cur += delta
        peak = max(peak, cur)
    assert peak <= cfg["risk"]["max_positions"]


def test_equity_curve_starts_at_capital(cfg):
    res = bt.run({"A": series()}, bench_frame(), cfg, 500_000)
    assert res.equity.iloc[0] == pytest.approx(500_000)


def test_metrics_are_empty_without_data(cfg):
    res = bt.run({}, pd.DataFrame(columns=["date", "close"]), cfg)
    assert res.metrics == {}
