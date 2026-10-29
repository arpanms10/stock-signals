"""Luck checks: deflated Sharpe, bootstrap range, by year, halves."""
import numpy as np
import pandas as pd
import pytest

from lab import stats


def _series(daily, start="2018-01-01"):
    idx = pd.bdate_range(start, periods=len(daily))
    return pd.Series(np.cumprod(1 + np.asarray(daily)), index=idx)


def test_inverse_normal_matches_known_values():
    assert stats._norm_ppf(0.5) == pytest.approx(0.0, abs=1e-9)
    assert stats._norm_ppf(0.975) == pytest.approx(1.959964, abs=1e-5)
    assert stats._norm_ppf(0.01) == pytest.approx(-2.326348, abs=1e-5)


def test_more_trials_lower_the_deflated_sharpe():
    rng = np.random.default_rng(1)
    active = pd.Series(rng.normal(0.001, 0.01, 1200))
    one = stats.deflated_sharpe(active, 1)
    many = stats.deflated_sharpe(active, 50)
    assert 0.5 < one <= 1 and many < one


def test_no_edge_gives_a_low_deflated_sharpe():
    rng = np.random.default_rng(2)
    assert stats.deflated_sharpe(pd.Series(rng.normal(0, 0.01, 1200)), 20) < 0.5


def test_bootstrap_range_brackets_the_mean():
    rng = np.random.default_rng(3)
    active = pd.Series(rng.normal(0.0004, 0.01, 1200))
    lo, hi = stats.bootstrap_range(active, n=500)
    mean = 100 * 252 * active.mean()
    assert lo < mean < hi


def test_by_year_and_halves():
    strat = _series([0.001] * 520)
    bench = _series([0.0005] * 520)
    t = stats.by_year({"strategy": strat, "benchmark": bench,
                       "control_1": bench, "control_2": bench})
    assert list(t.columns) == ["strategy", "NIFTY 500", "random picks", "vs index"]
    assert (t["vs index"] > 0).all()
    first, second = stats.halves(strat, bench)
    assert first > 0 and second > 0
