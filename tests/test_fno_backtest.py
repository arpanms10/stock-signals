"""Validation helpers on synthetic observations."""
import numpy as np
import pandas as pd
import pytest

import run_fno_backtest as bt


def obs_frame(n=600, seed=1, size_from=None):
    """Observations whose realised move grows with `size_from` (a column) if
    given, else is independent of everything."""
    rng = np.random.default_rng(seed)
    o = pd.DataFrame({
        "kind": "equity", "cycle": "monthly", "group": "equity", "sessions": 5,
        "adjusted": False, "spot": 100.0, "straddle": 5.0,
        "iv_pct": rng.uniform(0, 100, n), "iv_rv": rng.uniform(0.5, 2, n),
        "skew": rng.normal(0, 2, n), "term": rng.normal(0, 2, n),
        "iv": 25.0, "results_in_cycle": rng.random(n) < 0.3,
        "regime": np.where(rng.random(n) < 0.5, "rising", "falling"),
    })
    scale = 1 + (o[size_from] / o[size_from].max() * 2 if size_from else 0)
    move = rng.normal(0, 5, n) * scale
    o["close"] = 100 + move
    o["moves"] = (o["close"] - o["spot"]).abs() / o["straddle"]
    return o


def test_context_tables_find_a_real_effect():
    t = bt.context_tables(obs_frame(size_from="iv_pct"))
    s = t["Move size vs volatility context (summary)"].set_index("factor")
    assert s.loc["IV percentile", "rho_vs_move_size"] > 0.2
    assert s.loc["IV percentile", "inside_q1_pct"] > s.loc["IV percentile", "inside_q5_pct"]
    assert abs(s.loc["skew (put - call IV, pts)", "rho_vs_move_size"]) < 0.1


def test_context_tables_split_results_and_regime():
    t = bt.context_tables(obs_frame())
    r = t["Results inside the cycle (stocks)"]
    assert set(r["results_in_cycle"]) == {True, False}
    assert set(t["Market regime (NIFTY 500 vs its 200-day average)"]["regime"]) == {
        "rising", "falling"}


def test_calibration_reads_the_live_range_rows():
    ranges = pd.DataFrame([
        {"kind": "equity", "sessions": 5, "range": "±straddle (live)", "expiry_inside_pct": 61.3},
        {"kind": "index weekly", "sessions": 2, "range": "±straddle (live)", "expiry_inside_pct": 55.0},
        {"kind": "equity", "sessions": 5, "range": "walls only (S1-R1)", "expiry_inside_pct": 99},
    ])
    assert bt.calibration({"Ranges": ranges}) == {"equity": {5: 61.3},
                                                 "index weekly": {2: 55.0}}
