"""Is the result luck? Four checks every lab run reports.

  deflated Sharpe   the probability that the strategy's edge over NIFTY 500
                    is real, after allowing for how many variants of the idea
                    were tried (Bailey and Lopez de Prado, 2014). The best of
                    N tries of a useless idea still looks good; this asks
                    whether it looks better than the best of N would by
                    chance. Computed on daily returns over the index.
  bootstrap range   a 90% range for the yearly return over the index,
                    resampling months of daily excess returns
  by year           the strategy, the index and the random picks per year
  halves            the excess over the index in each half of the window
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

EULER = 0.5772156649


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _norm_ppf(p: float) -> float:
    """Inverse normal CDF (Acklam's approximation, |error| < 1.2e-9)."""
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    lo = 0.02425
    if p < lo:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
            ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > 1 - lo:
        return -_norm_ppf(1 - p)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
        (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def active_returns(curve: pd.Series, bench: pd.Series) -> pd.Series:
    """Daily strategy return minus the index's, on the days both have."""
    r = curve.pct_change()
    br = bench.reindex(curve.index).ffill().pct_change()
    return (r - br).dropna()


def deflated_sharpe(active: pd.Series, trials: int,
                    trial_sharpes: list[float] | None = None) -> float | None:
    """Probability (0-1) that the true daily Sharpe of `active` is above
    zero, given it is the best of `trials` tries. trial_sharpes, when there
    are at least two, estimates how much Sharpe varies between tries with no
    skill (the random controls); otherwise the estimator's own variance."""
    x = active.dropna().to_numpy()
    t = len(x)
    if t < 30 or x.std() == 0:
        return None
    sr = x.mean() / x.std()
    skew = float(pd.Series(x).skew())
    kurt = float(pd.Series(x).kurt()) + 3.0
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr * sr
    if denom <= 0:
        return None
    n = max(int(trials), 1)
    if n == 1:
        sr0 = 0.0
    else:
        if trial_sharpes and len(trial_sharpes) >= 2:
            var = float(np.var(trial_sharpes, ddof=1))
        else:
            var = denom / (t - 1)
        sr0 = math.sqrt(var) * ((1 - EULER) * _norm_ppf(1 - 1 / n)
                                + EULER * _norm_ppf(1 - 1 / (n * math.e)))
    return round(_norm_cdf((sr - sr0) * math.sqrt(t - 1) / math.sqrt(denom)), 3)


def daily_sharpe(active: pd.Series) -> float:
    a = active.dropna()
    return float(a.mean() / a.std()) if len(a) > 1 and a.std() else 0.0


def bootstrap_range(active: pd.Series, n: int = 2000, block: int = 21,
                    seed: int = 7, level: float = 0.90) -> tuple[float, float] | None:
    """A `level` range for the yearly excess return, in points, from
    resampling blocks of `block` sessions (months keep their streaks)."""
    x = active.dropna().to_numpy()
    if len(x) < 2 * block:
        return None
    rng = np.random.default_rng(seed)
    starts = np.arange(len(x) - block + 1)
    k = math.ceil(len(x) / block)
    means = np.empty(n)
    for i in range(n):
        pick = rng.choice(starts, size=k)
        sample = np.concatenate([x[s:s + block] for s in pick])[:len(x)]
        means[i] = sample.mean()
    lo, hi = np.quantile(means, [(1 - level) / 2, 1 - (1 - level) / 2])
    return round(100 * 252 * float(lo), 2), round(100 * 252 * float(hi), 2)


def _yearly(curve: pd.Series) -> pd.Series:
    c = curve.copy()
    c.index = pd.to_datetime(c.index)
    ends = c.resample("YE").last()
    starts = pd.concat([c.iloc[:1], ends.iloc[:-1]])
    out = 100 * (ends.to_numpy() / starts.to_numpy() - 1)
    return pd.Series(out, index=ends.index.year)


def by_year(curves: dict[str, pd.Series]) -> pd.DataFrame:
    """Calendar-year returns, %: strategy, index, random-pick mean."""
    cols = {"strategy": _yearly(curves["strategy"]),
            "NIFTY 500": _yearly(curves["benchmark"])}
    ctrl = [_yearly(v) for k, v in curves.items() if k.startswith("control_")]
    if ctrl:
        cols["random picks"] = pd.concat(ctrl, axis=1).mean(axis=1)
    df = pd.DataFrame(cols).round(2)
    df["vs index"] = (df["strategy"] - df["NIFTY 500"]).round(2)
    return df


def halves(curve: pd.Series, bench: pd.Series) -> tuple[float, float]:
    """Excess CAGR over the index in the first and second half, points."""
    c = curve.copy()
    c.index = pd.to_datetime(c.index)
    mid = c.index[0] + (c.index[-1] - c.index[0]) / 2
    out = []
    for part in (c[c.index <= mid], c[c.index >= mid]):
        b = bench.reindex(part.index).ffill().dropna()
        part = part.reindex(b.index)
        years = max((part.index[-1] - part.index[0]).days / 365.25, 1e-9)
        cagr = (part.iloc[-1] / part.iloc[0]) ** (1 / years) - 1
        bcagr = (b.iloc[-1] / b.iloc[0]) ** (1 / years) - 1
        out.append(round(100 * (cagr - bcagr), 2))
    return out[0], out[1]
