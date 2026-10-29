"""How strategies move together, and what a blend of two would have done.

Works from the month-end curves in config/lab_results.json, so only runs
over the same window compare (train with train, and so on). Two edges that
do not move together can make a smoother book than either alone; a blend
here rebalances back to its weights every month, before costs.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from lab import results


def monthly_returns(split: str = "train", data: dict | None = None,
                    curve: str = "strategy") -> pd.DataFrame:
    """Month-by-month returns, one column per strategy that has `split`,
    plus NIFTY 500."""
    data = data if data is not None else results.load()
    cols, bench = {}, None
    for name, runs in data.items():
        r = runs.get(split)
        if not r or not r.get("curves", {}).get(curve):
            continue
        s = pd.Series(r["curves"][curve], dtype=float)
        s.index = pd.to_datetime(s.index)
        cols[name] = s.pct_change()
        if bench is None and r["curves"].get("benchmark"):
            b = pd.Series(r["curves"]["benchmark"], dtype=float)
            b.index = pd.to_datetime(b.index)
            bench = b.pct_change()
    if bench is not None:
        cols["NIFTY 500"] = bench
    return pd.DataFrame(cols).dropna(how="all")


def correlation(split: str = "train", data: dict | None = None) -> pd.DataFrame:
    """Correlation of monthly returns over the months every column has."""
    return monthly_returns(split, data).dropna().corr().round(2)


def blend(a: str, b: str, weight_a: float = 0.5, split: str = "train",
          data: dict | None = None) -> dict:
    """Two strategies held at weight_a / 1 - weight_a, rebalanced monthly."""
    r = monthly_returns(split, data)[[a, b]].dropna()
    if r.empty:
        return {}
    mix = weight_a * r[a] + (1 - weight_a) * r[b]
    out = {}
    for name, series in ((a, r[a]), (b, r[b]), (f"{weight_a:.0%} / {1 - weight_a:.0%}", mix)):
        curve = (1 + series).cumprod()
        years = len(series) / 12
        out[name] = {
            "cagr_pct": round(100 * (curve.iloc[-1] ** (1 / years) - 1), 2),
            "max_drawdown_pct": round(100 * float((curve / curve.cummax() - 1).min()), 2),
            "sharpe": round(float(np.sqrt(12) * series.mean() / series.std()), 2)
            if series.std() else 0.0,
        }
    out["correlation"] = round(float(r[a].corr(r[b])), 2)
    return out
