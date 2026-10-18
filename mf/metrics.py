"""How a fund has done against the funds it competes with, as of a month.

Everything is on month-end NAVs and against the fund's SEBI category: the
peers a holder could have switched into, all Direct Growth, all after costs.
The category median is the benchmark rather than an index because it always
exists, is after costs, and in the backtest includes funds that later closed.
An index fund for the category, where one has enough history, is the second
yardstick: the cheap thing you could own instead.

snapshot() computes every fund in the given categories at one month. The live
view calls it at the newest month; the backtest calls it at every month.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

WINDOWS = {"1y": 12, "3y": 36, "5y": 60}
MIN_PEERS = 5


def cagr(total: pd.Series | float, months: int):
    return (1 + total) ** (12 / months) - 1


def _period_return(P: pd.DataFrame, t: pd.Period, k: int) -> pd.Series:
    s = t - k
    if s not in P.index or t not in P.index:
        return pd.Series(dtype=float)
    return (P.loc[t] / P.loc[s] - 1).dropna()


def _pct_beaten(x: pd.Series) -> pd.Series:
    """Share of the other funds this one beat: 1 = best, 0 = worst."""
    n = len(x)
    if n < 2:
        return pd.Series(np.nan, index=x.index)
    return (x.rank(method="average") - 1) / (n - 1)


def _max_drawdown(nav: pd.Series) -> float:
    nav = nav.dropna()
    if len(nav) < 2:
        return np.nan
    return float((nav / nav.cummax() - 1).min())


def snapshot(P: pd.DataFrame, category: pd.Series, t: pd.Period,
             index_funds: dict[str, int] | None = None,
             categories: set[str] | None = None) -> pd.DataFrame:
    """One row per fund alive at `t` in `categories` (all, if None).

    P         month x code NAVs (navdb.panel)
    category  code -> category
    """
    index_funds = index_funds or {}
    if t not in P.index:
        return pd.DataFrame()
    alive = P.loc[t].dropna().index
    cats = category.reindex(alive).dropna()
    if categories is not None:
        cats = cats[cats.isin(categories)]
    R = P.pct_change(fill_method=None)
    rows = []
    for cat, codes in cats.groupby(cats).groups.items():
        codes = list(codes)
        out = pd.DataFrame(index=pd.Index(codes, name="code"))
        out["category"] = cat
        first = P[codes].apply(pd.Series.first_valid_index)
        out["history_months"] = [(t - f).n if f is not None else 0 for f in first]
        out["peers"] = len(codes)
        for label, k in WINDOWS.items():
            r = _period_return(P[codes], t, k)
            c = cagr(r, k)
            out[f"cagr_{label}"] = c
            if len(c) >= MIN_PEERS:
                out[f"excess_{label}"] = c - c.median()
                out[f"rank_{label}"] = _pct_beaten(c)
            else:
                out[f"excess_{label}"] = np.nan
                out[f"rank_{label}"] = np.nan
            idx = index_funds.get(cat)
            if idx is not None and idx in P.columns:
                ri = _period_return(P[[idx]], t, k)
                out[f"vs_index_{label}"] = (c - cagr(ri.iloc[0], k)) if len(ri) else np.nan
            else:
                out[f"vs_index_{label}"] = np.nan

        # Consistency: of the 12-month windows ending in the last 25 months
        # (so all inside the 3-year window), the share where the fund beat the
        # category median. A fund can top a 3-year table on one great year.
        wins, n_win = pd.Series(0.0, index=codes), pd.Series(0, index=codes)
        for j in range(25):
            e = t - j
            r12 = _period_return(P[codes], e, 12)
            if len(r12) >= MIN_PEERS:
                beat = (r12 > r12.median()).astype(float)
                wins = wins.add(beat, fill_value=0)
                n_win = n_win.add(pd.Series(1, index=beat.index), fill_value=0)
        out["consistency_3y"] = (wins / n_win.replace(0, np.nan)).where(n_win >= 20)

        # Risk over 3 years, against the category's median month.
        win = R.loc[t - 35:t, codes] if (t - 35) in R.index else R.loc[:t, codes].tail(36)
        med = win.median(axis=1)
        out["vol_3y"] = win.std() * np.sqrt(12)
        down = med < 0
        if down.sum() >= 6:
            out["downside_capture_3y"] = win[down].mean() / med[down].mean()
        else:
            out["downside_capture_3y"] = np.nan
        navwin = P.loc[t - 36:t, codes] if (t - 36) in P.index else P.loc[:t, codes].tail(37)
        out["max_drawdown_3y"] = navwin.apply(_max_drawdown)
        full = win.notna().sum() >= 34
        for col in ("vol_3y", "downside_capture_3y", "max_drawdown_3y"):
            out[col] = out[col].where(full)
        rows.append(out)
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows)


def correlation(P: pd.DataFrame, codes: list[int], t: pd.Period, months: int = 36) -> pd.DataFrame:
    """Monthly-return correlation: a stand-in for portfolio overlap when only
    NAVs are known. Two funds above ~0.95 move as one."""
    R = P[codes].pct_change(fill_method=None)
    return R.loc[t - (months - 1):t].corr(min_periods=24)


def forward_excess(P: pd.DataFrame, category: pd.Series, t: pd.Period, k: int,
                   codes) -> pd.Series:
    """Each fund's k-month return from t, minus its category median.

    A fund that stops reporting (merged, closed) is carried at the category
    median from its last NAV -- the holder was moved somewhere, and the worst
    funds are exactly the ones that get merged away, so dropping them would
    flatter the losers' bucket.
    """
    end = t + k
    if end not in P.index:
        return pd.Series(dtype=float)
    R = P.loc[t + 1:end].pct_change(fill_method=None)
    R.iloc[0] = P.loc[t + 1] / P.loc[t] - 1
    out = {}
    for cat, members in category.reindex(codes).dropna().groupby(lambda c: category[c]).groups.items():
        peers = category[category == cat].index.intersection(P.columns)
        peers = [c for c in peers if pd.notna(P.at[t, c])]
        if len(peers) < MIN_PEERS:
            continue
        Rp = R[peers]
        med = Rp.median(axis=1)
        filled = Rp.apply(lambda col: col.fillna(med))
        total = (1 + filled).prod() - 1
        exc = total - total.median()
        for c in members:
            if c in exc.index:
                out[c] = exc[c]
    return pd.Series(out, dtype=float)
