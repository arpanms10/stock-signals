"""Portfolio risk: history of the current mix, exposures, flags."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy import portfolio_risk as pr

CFG = pr.load_config()


def _series(rets, start="2023-01-02"):
    idx = pd.bdate_range(start, periods=len(rets) + 1)
    return pd.Series(100 * np.cumprod(np.r_[1.0, 1 + np.asarray(rets)]), index=idx)


def test_portfolio_returns_reweight_missing_days():
    a = _series([0.01] * 10)
    b = _series([0.03] * 5, start=a.index[5])           # listed later
    r = pr.portfolio_returns({"A": a, "B": b}, {"A": 0.5, "B": 0.5})
    assert r.iloc[0] == pytest.approx(0.01)               # only A trades
    assert r.iloc[-1] == pytest.approx(0.02)              # both


def test_stats_drawdown_and_volatility():
    rng = np.random.default_rng(0)
    rets = list(rng.normal(0, 0.01, 200)) + [-0.05] * 4 + list(rng.normal(0, 0.01, 100))
    r = _series(rets).pct_change().dropna()
    s = pr.stats(r)
    assert s["days"] == len(r)
    assert s["max_drawdown_pct"] < -18
    assert 15 < s["volatility_pct"] < 30
    assert s["var95_1d_pct"] < 0
    assert s["drawdown_peak"] < s["drawdown_trough"]


def test_stats_needs_enough_days():
    assert pr.stats(pd.Series([0.01] * 10)) == {"days": 10}


def test_beta_of_a_levered_copy():
    rng = np.random.default_rng(1)
    b = pd.Series(rng.normal(0, 0.01, 300), index=pd.bdate_range("2023-01-02", periods=300))
    assert pr.beta(1.5 * b, b) == pytest.approx(1.5)


def test_correlation_flags_pairs():
    rng = np.random.default_rng(2)
    x = rng.normal(0, 0.01, 300)
    px = {"A": _series(x), "B": _series(x + rng.normal(0, 0.001, 300)),
          "C": _series(rng.normal(0, 0.01, 300))}
    c = pr.correlation(px, ["A", "B", "C"], 0.7)
    assert [(p["a"], p["b"]) for p in c["pairs"]] == [("A", "B")]


def test_growth_curve_starts_near_100():
    rng = np.random.default_rng(3)
    r = pd.Series(rng.normal(0, 0.01, 100), index=pd.bdate_range("2023-01-02", periods=100))
    c = pr.growth_curve(r, r)
    assert c and abs(c[0]["portfolio"] - 100) < 10
    assert c[-1]["portfolio"] == pytest.approx(c[-1]["benchmark"])


def _hs():
    return [pr.Holding("HDFCBANK", 30, group="bank", adv_value=1e9),
            pr.Holding("ICICIBANK", 20, group="bank", adv_value=1e9),
            pr.Holding("TCS", 30, group="it", adv_value=1e9, flags=["auditor resigned"]),
            pr.Holding("TINY", 20, group="other", adv_value=10, own_band_pct=0.9,
                       peer_relative=1.5, pat_ttm_positive=False, tier="exit_candidate",
                       satellite_category="turnaround")]


def test_themes_and_overlaps():
    th = {t["key"]: t for t in pr.themes(_hs(), 100, CFG)}
    assert th["rate_sensitive"]["pct"] == pytest.approx(50)
    assert th["rate_sensitive"]["flag"]
    assert th["export"]["pct"] == pytest.approx(30)
    ov = pr.overlaps(_hs(), 100)
    assert ov == [{"group": "bank", "holdings": ["HDFCBANK", "ICICIBANK"], "pct": 50}]


def test_flags_liquidity_and_effective_names():
    hs = _hs()
    liq = pr.liquidity(hs, CFG)
    assert liq[0]["symbol"] == "TINY" and liq[0]["flag"]
    f = {x["key"]: x for x in pr.risk_flags(hs, 100, liq, CFG)}
    assert f["governance"]["holdings"] == ["TCS"]
    for k in ("valuation", "earnings", "liquidity", "quality"):
        assert f[k]["holdings"] == ["TINY"], k
    assert pr.effective_names(hs) == pytest.approx(1 / (0.09 + 0.04 + 0.09 + 0.04))


def test_satellite_mix_against_targets():
    hs = _hs()
    mix = {m["category"]: m for m in pr.satellite_mix(hs, 100, {"turnaround": 5, "growth": 10})}
    assert mix["turnaround"]["pct"] == pytest.approx(20)
    assert mix["growth"]["pct"] == 0
    assert mix["uncategorised"]["pct"] == pytest.approx(80)
