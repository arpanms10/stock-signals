"""Data in, picks out -- today, or on any past date.

The universe's quarterly lines live in data/results/universe_quarters.csv
(shared with run_results_backtest.py). `refresh()` adds new filings to it;
the raw filings are cached, so only new quarters touch NSE.
"""
from __future__ import annotations

import bisect
import datetime as dt
import math

import numpy as np
import pandas as pd

from results import filings as rf
from results import store

from . import metrics as pm
from . import score as ps

UNIVERSE = store.DIR / "universe_quarters.csv"


def load_config() -> dict:
    from scoring import timing
    return timing.load_config()["picker"]


def universe(name: str) -> pd.DataFrame:
    """symbol, sector for an index universe."""
    from data.sources import universe as uni
    return getattr(uni, name)()[["symbol", "sector"]]


def refresh(symbols: list[str], quarters: int = 24, log=print) -> pd.DataFrame:
    rows, failed = [], []
    for i, sym in enumerate(symbols, 1):
        try:
            rows.extend(rf.history(sym, quarters))
        except Exception as exc:
            failed.append(sym)
            log(f"{sym}: failed ({exc})")
        if i % 25 == 0:
            log(f"{i}/{len(symbols)}")
    if failed:
        log(f"failed: {', '.join(failed)}")
    return store.save_quarters(rows, UNIVERSE)


class Prices:
    """Raw closes per symbol, for 'the close on or before this date'."""

    def __init__(self, con, adjusted: bool = False):
        self.con, self.adjusted, self._cache = con, adjusted, {}

    def frame(self, symbol: str) -> pd.DataFrame:
        if symbol not in self._cache:
            from data import store as price_store
            self._cache[symbol] = price_store.load_prices(self.con, symbol,
                                                          adjusted=self.adjusted)
        return self._cache[symbol]

    def on(self, symbol: str):
        df = self.frame(symbol)
        dates = list(df["date"]) if not df.empty else []
        closes = list(df["close"]) if not df.empty else []

        def price_on(d: dt.date):
            i = bisect.bisect_right(dates, d) - 1
            # Stale beyond a week means suspended or delisted: no price.
            if i < 0 or (d - dates[i]).days > 7:
                return None
            return float(closes[i])
        return price_on


def evaluate(quarters: pd.DataFrame, as_of: dt.date, prices: Prices,
             sectors: dict[str, str], cfg: dict) -> pd.DataFrame:
    """Metrics and both scores for every symbol on `as_of`."""
    out = []
    for sym, g in quarters.groupby("symbol"):
        m = pm.compute(g.to_dict("records"), as_of, prices.on(sym))
        if m is not None and m.get("price"):
            out.append(m)
    return ps.score(pd.DataFrame(out), cfg, sectors)


def rebalance_dates(cfg: dict, start: dt.date, end: dt.date) -> list[dt.date]:
    out = []
    for y in range(start.year, end.year + 1):
        for md in cfg["rebalance"]:
            m, d = map(int, md.split("-"))
            day = dt.date(y, m, d)
            if start <= day <= end:
                out.append(day)
    return sorted(out)


def _period_return(adj: Prices, symbols: list[str], a: dt.date, b: dt.date) -> dict:
    """Close-to-close return per symbol over [a, b], adjusted for splits.
    A symbol with no price at b (delisted, suspended) keeps its last price,
    which is the honest assumption for a holder who could not sell."""
    out = {}
    for s in symbols:
        df = adj.frame(s)
        if df.empty:
            continue
        before_a = df[df["date"] <= a]
        if before_a.empty:
            continue
        p0 = float(before_a["close"].iloc[-1])
        p1 = float(df[df["date"] <= b]["close"].iloc[-1])
        out[s] = 100 * (p1 / p0 - 1)
    return out


def backtest(quarters: pd.DataFrame, sectors: dict[str, str], cfg: dict, con,
             start: dt.date, end: dt.date, index: pd.DataFrame | None = None,
             log=print) -> dict:
    """Rebalance on each date, equal weight, hold to the next date.

    Benchmarks on the same dates: the equal-weighted universe (every stock
    with a price -- the fair comparison, same survivors) and the index."""
    raw, adj = Prices(con), Prices(con, adjusted=True)
    dates = rebalance_dates(cfg, start, end)
    n, cost = cfg["top_n"], cfg["round_trip_cost_pct"]
    held = {"garp": set(), "qarp": set()}
    periods, picks_log = [], []
    ix = index.set_index("date")["close"] if index is not None and not index.empty else None
    for a, b in zip(dates, dates[1:]):
        scored = evaluate(quarters, a, raw, sectors, cfg)
        if scored.empty:
            continue
        row = {"start": a, "end": b, "universe_n": len(scored)}
        allret = _period_return(adj, list(scored["symbol"]), a, b)
        row["universe"] = float(np.mean(list(allret.values()))) if allret else None
        if ix is not None:
            i0, i1 = ix[ix.index <= a], ix[ix.index <= b]
            row["index"] = 100 * (i1.iloc[-1] / i0.iloc[-1] - 1) if len(i0) and len(i1) else None
        for method in ("garp", "qarp"):
            top = ps.picks(scored, method, n)
            syms = list(top["symbol"])
            r = _period_return(adj, syms, a, b)
            turnover = (len(set(syms) - held[method]) / max(len(syms), 1)) if syms else 0
            held[method] = set(syms)
            row[f"{method}_n"] = len(syms)
            row[method] = (float(np.mean(list(r.values()))) - cost * turnover) if r else None
            picks_log += [{"date": a, "method": method, "symbol": s,
                           "score": sc_, "ret": r.get(s)}
                          for s, sc_ in zip(syms, top[f"{method}_score"])]
        periods.append(row)
        log(f"{a} -> {b}: universe {row['universe']:+.1f}%  "
            f"GARP {row['garp_n']} {_fmt(row['garp'])}  "
            f"QARP {row['qarp_n']} {_fmt(row['qarp'])}")
    return {"periods": pd.DataFrame(periods), "picks": pd.DataFrame(picks_log)}


def _fmt(v) -> str:
    return "n/a" if v is None else f"{v:+.1f}%"


def summarise(periods: pd.DataFrame) -> dict:
    """Compounded return, excess over the equal-weight universe per period
    and its t-statistic across periods, hit rate, worst period."""
    out = {"periods": len(periods)}
    if periods.empty:
        return out
    years = (periods["end"].iloc[-1] - periods["start"].iloc[0]).days / 365.25
    out["from"], out["to"] = str(periods["start"].iloc[0]), str(periods["end"].iloc[-1])
    for col in ("garp", "qarp", "universe", "index"):
        if col not in periods:
            continue
        r = periods[col].dropna()
        if r.empty:
            continue
        growth = float(np.prod(1 + r / 100))
        s = {"cagr": round(100 * (growth ** (1 / years) - 1), 1) if years > 0 else None,
             "total": round(100 * (growth - 1), 1),
             "worst_period": round(float(r.min()), 1)}
        if col in ("garp", "qarp"):
            ex = (periods[col] - periods["universe"]).dropna()
            s["excess_mean"] = round(float(ex.mean()), 2)
            s["excess_t"] = (round(float(ex.mean() / (ex.std(ddof=1) / math.sqrt(len(ex)))), 2)
                             if len(ex) >= 3 and ex.std(ddof=1) > 0 else None)
            s["beat_universe_pct"] = round(100 * float((ex > 0).mean()), 0)
            s["avg_names"] = round(float(periods[f"{col}_n"].mean()), 1)
        out[col] = s
    return out
