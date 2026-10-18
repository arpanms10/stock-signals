"""Post-results reaction drift as a trading rule, after costs.

run_results_backtest.py found that stocks which rallied on their results
(>= 1 ATR on the reaction day) kept beating those that sold off. That is a
spread across events; this asks whether a long-only book built on it, paying
costs on every trade, beats simply holding the universe.

The rule. After each filing in the point-in-time universe whose reaction was
"rallied", buy at the reaction-day close and sell at the close `hold`
sessions later. The book has `slots` equal slots: each buy takes NAV / slots
(the position cap), and a rally that finds every slot taken -- or the name
already held -- is skipped. Same-day rallies fill slots in broadcast order.
Capital not in a position sits in the NIFTY 500 (an index fund, no cost
charged), so the book is always fully in equities and the comparison with
the universe is about which stocks, not how much market.

Costs: half the round trip on each buy, half on each sale. A holding that
stops trading (delisted, merged, suspended) keeps its last price and is sold
at it on schedule.

Prices are a calendar x symbol matrix of adjusted closes on NIFTY 500 trading
days, carried forward over gaps; `live` marks the days a stock really traded.
"""
from __future__ import annotations

import bisect
import datetime as dt
import math

import numpy as np
import pandas as pd


def price_matrix(symbols, load, calendar: list[dt.date]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(closes carried forward, traded-that-day mask), calendar x symbol."""
    cols, live = {}, {}
    cal = pd.Index(calendar)
    for s in sorted(set(symbols)):
        df = load(s)
        if df is None or df.empty:
            continue
        c = df.drop_duplicates("date", keep="last").set_index("date")["close"]
        c = c[c > 0].reindex(cal)
        live[s] = c.notna()
        cols[s] = c.ffill()
    return pd.DataFrame(cols, index=cal), pd.DataFrame(live, index=cal)


def membership(members: pd.DataFrame, calendar: list[dt.date],
               columns) -> pd.DataFrame:
    """Calendar x symbol: a member as of the latest universe date on or
    before that day (results.pit_universe.on)."""
    known = sorted(members["date"].unique())
    by_date = {d: set(g["symbol"]) for d, g in members.groupby("date")}
    out = pd.DataFrame(False, index=pd.Index(calendar), columns=list(columns))
    for d in calendar:
        i = bisect.bisect_right(known, d) - 1
        if i >= 0:
            syms = [s for s in by_date[known[i]] if s in out.columns]
            out.loc[d, syms] = True
    return out


def ew_universe(px: pd.DataFrame, member: pd.DataFrame, cost_pct: float) -> pd.Series:
    """Daily return (fraction) of the equal-weight universe: each day the
    mean return of yesterday's members with a price on both days. At each
    change of membership the names swapped pay the round trip."""
    r = px.pct_change(fill_method=None)
    held = member.shift(1, fill_value=False) & r.notna()
    ret = (r.where(held).sum(axis=1) / held.sum(axis=1)).fillna(0.0)
    m = member.astype(int)
    n = m.sum(axis=1).replace(0, np.nan)
    swapped = (m.diff().abs().sum(axis=1) / 2 / n).fillna(0.0)
    swapped.iloc[0] = 0.0
    return ret - cost_pct / 100 * swapped


def entries(events: pd.DataFrame, calendar: list[dt.date], hold: int) -> pd.DataFrame:
    """Rallied events as (symbol, entry index, exit index), broadcast order.
    The exit can lie past the last day; the book then marks it at the end."""
    pos = {d: i for i, d in enumerate(calendar)}
    e = events[events["reaction"] == "rallied"].copy()
    e["reaction_date"] = pd.to_datetime(e["reaction_date"]).dt.date
    e["i0"] = e["reaction_date"].map(pos)
    e = e.dropna(subset=["i0"])
    e["i0"] = e["i0"].astype(int)
    e["i1"] = e["i0"] + hold
    return e.sort_values(["i0", "broadcast"])[["symbol", "i0", "i1", "to_date"]] \
        .reset_index(drop=True)


def simulate(trades: pd.DataFrame, px: np.ndarray, col: dict[str, int],
             park: np.ndarray, slots: int, cost_pct: float, start: int = 0) -> dict:
    """Run the slot book from calendar index `start` to the end.

    `px` is the price matrix as an array, `col` symbol -> column, `park` the
    NIFTY 500 closes (the home of idle capital). Returns the daily NAV
    (starting at 1.0), positions held each day, total bought, buys and skips."""
    half = cost_pct / 200
    n_days = px.shape[0]
    by_day: dict[int, list] = {}
    for s, i0, i1 in zip(trades["symbol"], trades["i0"], trades["i1"]):
        if s in col:
            by_day.setdefault(int(i0), []).append((s, int(i1)))
    idle, book = 1.0, []          # book: [symbol, column, value, exit index]
    nav = np.empty(n_days - start)
    held_n = np.empty(n_days - start, dtype=int)
    bought, buys, skipped = 0.0, 0, 0
    for t in range(start, n_days):
        if t > start:
            idle *= park[t] / park[t - 1]
            for p in book:
                p[2] *= px[t, p[1]] / px[t - 1, p[1]]
            keep = []
            for p in book:
                if p[3] == t:
                    idle += p[2] * (1 - half)
                else:
                    keep.append(p)
            book = keep
        for s, i1 in by_day.get(t, ()):
            c = col[s]
            if len(book) >= slots or any(p[0] == s for p in book) \
                    or not np.isfinite(px[t, c]):
                skipped += 1
                continue
            total = idle + sum(p[2] for p in book)
            size = min(total / slots, idle)
            if size <= 0:
                skipped += 1
                continue
            idle -= size
            book.append([s, c, size * (1 - half), i1])
            bought += size
            buys += 1
        nav[t - start] = idle + sum(p[2] for p in book)
        held_n[t - start] = len(book)
    return {"nav": nav, "held": held_n, "bought": bought, "buys": buys,
            "skipped": skipped}


def t_stat(x) -> float | None:
    x = pd.Series(x, dtype=float).dropna()
    if len(x) < 3 or x.std(ddof=1) == 0:
        return None
    return round(float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x)))), 2)


def cagr(nav: pd.Series) -> float | None:
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    if years <= 0:
        return None
    return round(100 * ((nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1), 1)


def max_drawdown(nav: pd.Series) -> float:
    return round(float(100 * (nav / nav.cummax() - 1).min()), 1)


def quarterly(nav: pd.Series) -> pd.Series:
    """Calendar-quarter returns in %, from the quarter-end NAVs (the first
    quarter starts from the first day)."""
    s = nav.copy()
    s.index = pd.to_datetime(s.index)
    ends = s.groupby(s.index.to_period("Q")).last()
    prev = ends.shift(1)
    prev.iloc[0] = s.iloc[0]
    return 100 * (ends / prev - 1)


def window_return(px: pd.DataFrame, s: str, i0: int, i1: int) -> float | None:
    """% return of one symbol from the close at i0 to the close at i1 (or the
    last day), carried-forward prices."""
    if s not in px:
        return None
    i1 = min(i1, len(px) - 1)
    a, b = px[s].iloc[i0], px[s].iloc[i1]
    if not (np.isfinite(a) and np.isfinite(b)) or a <= 0:
        return None
    return 100 * (b / a - 1)


def event_test(events: pd.DataFrame, px: pd.DataFrame, universe_level: pd.Series,
               calendar: list[dt.date], hold: int, cost_pct: float) -> dict:
    """Per filing quarter: rallied net of the round trip less the equal-weight
    universe over the same window, and rallied minus sold off with both legs
    paying (reference). Only complete windows. t across filing quarters."""
    pos = {d: i for i, d in enumerate(calendar)}
    lvl = universe_level.to_numpy()
    rows = []
    for e in events.itertuples():
        i0 = pos.get(pd.Timestamp(e.reaction_date).date())
        if i0 is None or i0 + hold >= len(calendar):
            continue
        r = window_return(px, e.symbol, i0, i0 + hold)
        if r is None:
            continue
        u = 100 * (lvl[i0 + hold] / lvl[i0] - 1)
        rows.append({"to_date": e.to_date, "reaction": e.reaction, "ret": r,
                     "excess_ew": r - cost_pct - u})
    df = pd.DataFrame(rows)
    out = {"hold": hold}
    if df.empty:
        return out
    long_q, spread_q = {}, {}
    for q, g in df.groupby("to_date"):
        a = g.loc[g["reaction"] == "rallied", "ret"]
        b = g.loc[g["reaction"] == "sold off", "ret"]
        if len(a) >= 3:
            long_q[q] = g.loc[g["reaction"] == "rallied", "excess_ew"].mean()
        if len(a) >= 3 and len(b) >= 3:
            spread_q[q] = a.mean() - b.mean() - 2 * cost_pct
    long_q, spread_q = pd.Series(long_q, dtype=float), pd.Series(spread_q, dtype=float)
    out.update({
        "rallied_events": int((df["reaction"] == "rallied").sum()),
        "rallied_net_vs_ew_mean": round(float(long_q.mean()), 2),
        "rallied_net_vs_ew_t": t_stat(long_q), "quarters": len(long_q),
        "rallied_net_vs_ew_positive": int((long_q > 0).sum()),
        "spread_net_mean": round(float(spread_q.mean()), 2),
        "spread_net_t": t_stat(spread_q), "spread_quarters": len(spread_q)})
    return out
