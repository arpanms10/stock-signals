"""Backtest reporting: the statistics and tables every backtest should leave.

A summary number hides how a result was made. One lucky trade, one good year,
or a long losing streak that nobody would sit through all look the same in a
CAGR. So every backtest here can export its trade log (or equity curve), and
these functions turn it into the standard set:

  trade_stats    win rate, average win/loss, payoff ratio, profit factor,
                 expectancy, max drawdown on cumulative P&L, return / max
                 drawdown, best/worst, longest winning and losing streaks
  equity_stats   max drawdown %, longest drawdown, time spent in drawdown
  month_table    year x month table with a Total column -- P&L summed for
                 trade logs, returns compounded for an equity curve
  export         one Excel workbook (and a CSV of the trades) to inspect, or
                 to hand to someone else to analyse

Pure functions on pandas objects; no I/O except export().
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _streaks(wins: np.ndarray) -> tuple[int, int]:
    """Longest run of True and of False."""
    best = {True: 0, False: 0}
    run, prev = 0, None
    for w in wins:
        run = run + 1 if w == prev else 1
        prev = w
        best[bool(w)] = max(best[bool(w)], run)
    return best[True], best[False]


def drawdown(cum: pd.Series) -> pd.Series:
    """Distance below the running peak (same units as `cum`)."""
    return cum - cum.cummax()


def trade_stats(pnl: pd.Series) -> dict:
    """Statistics of a sequence of per-trade P&L, in exit-date order.

    Drawdown here is in P&L units on the cumulative sum -- the right measure
    for fixed-size trades (one lot each), where there is no compounding
    equity to take a percentage of.
    """
    p = pd.Series(pnl, dtype=float).dropna().reset_index(drop=True)
    n = len(p)
    if n == 0:
        return {"trades": 0}
    wins, losses = p[p > 0], p[p <= 0]
    cum = p.cumsum()
    dd = drawdown(pd.concat([pd.Series([0.0]), cum], ignore_index=True))
    max_dd = float(-dd.min())
    gross_win, gross_loss = float(wins.sum()), float(-losses.sum())
    w_streak, l_streak = _streaks((p > 0).to_numpy())
    return {
        "trades": n,
        "win_rate_pct": round(100 * len(wins) / n, 1),
        "avg_win": round(float(wins.mean()), 2) if len(wins) else 0.0,
        "avg_loss": round(float(losses.mean()), 2) if len(losses) else 0.0,
        "payoff_ratio": (round(float(wins.mean() / -losses.mean()), 2)
                         if len(wins) and len(losses) and losses.mean() < 0 else None),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "expectancy": round(float(p.mean()), 2),
        "total": round(float(p.sum()), 2),
        "best": round(float(p.max()), 2),
        "worst": round(float(p.min()), 2),
        "max_drawdown": round(max_dd, 2),
        "return_to_max_dd": round(float(p.sum()) / max_dd, 2) if max_dd > 0 else None,
        "longest_win_streak": w_streak,
        "longest_loss_streak": l_streak,
    }


def equity_stats(equity: pd.Series) -> dict:
    """Drawdown shape of an equity curve indexed by date."""
    e = pd.Series(equity, dtype=float).dropna()
    if len(e) < 2:
        return {}
    peak = e.cummax()
    dd = e / peak - 1
    under = dd < 0
    # Longest stretch below a previous peak, in calendar days.
    longest, start = 0, None
    idx = list(e.index)
    for i, u in enumerate(under):
        if u and start is None:
            start = idx[i - 1] if i else idx[i]
        if (not u or i == len(idx) - 1) and start is not None:
            end = idx[i]
            longest = max(longest, (pd.Timestamp(end) - pd.Timestamp(start)).days)
            start = None
    return {
        "max_drawdown_pct": round(100 * float(dd.min()), 2),
        "longest_drawdown_days": int(longest),
        "time_in_drawdown_pct": round(100 * float(under.mean()), 1),
    }


def month_table(values: pd.Series, how: str = "sum") -> pd.DataFrame:
    """Year x month table with a Total column.

    how="sum":      `values` are P&L amounts by date; months and years sum.
    how="compound": `values` is an equity curve by date; cells are monthly
                    returns in %, and Total is the year's compounded return.
    """
    s = pd.Series(values, dtype=float).dropna()
    if s.empty:
        return pd.DataFrame()
    s.index = pd.to_datetime(s.index)
    if how == "sum":
        m = s.groupby([s.index.year, s.index.month]).sum()
    elif how == "compound":
        last = s.groupby([s.index.year, s.index.month]).last()
        prev = last.shift(1)
        prev.iloc[0] = s.iloc[0]
        m = 100 * (last / prev - 1)
    else:
        raise ValueError(how)
    t = m.unstack()
    t = t.reindex(columns=range(1, 13))
    t.columns = MONTHS
    if how == "sum":
        t["Total"] = t.sum(axis=1, min_count=1)
    else:
        t["Total"] = 100 * ((1 + t[MONTHS] / 100).prod(axis=1, min_count=1) - 1)
    t.index.name = "year"
    return t.round(2)


def split_stats(trades: pd.DataFrame, by: str | list[str], pnl_col: str = "pnl") -> pd.DataFrame:
    """trade_stats for each group -- e.g. calls vs puts, or by year -- so one
    side's contribution is visible instead of buried in the total."""
    rows = []
    for key, g in trades.sort_values("exit_date").groupby(by):
        st = trade_stats(g[pnl_col])
        key = key if isinstance(key, tuple) else (key,)
        rows.append({**dict(zip([by] if isinstance(by, str) else by, key)), **st})
    return pd.DataFrame(rows)


def export(path: Path | str, sheets: dict[str, pd.DataFrame],
           trades: pd.DataFrame | None = None) -> Path:
    """Write every table to one workbook; the trade log also as CSV beside it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path) as xw:
        for name, df in sheets.items():
            if df is None or len(df) == 0:
                continue
            keep_index = df.index.name is not None
            df.to_excel(xw, sheet_name=name[:31], index=keep_index)
    if trades is not None and len(trades):
        trades.to_csv(path.with_suffix(".trades.csv"), index=False)
    return path


def stats_frame(stats: dict) -> pd.DataFrame:
    return pd.DataFrame({"metric": list(stats), "value": list(stats.values())})


def default_path(name: str) -> Path:
    return (Path(__file__).resolve().parents[1] / "reports" / "backtests"
            / f"{name}_{dt.datetime.now():%Y%m%d_%H%M}.xlsx")
