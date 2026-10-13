"""Does the swing score pick better trades than the setups alone?

Every signal is played out with the exact plan the Swing tab shows (buy-stop,
structure stop, 1.5R / 3R, 15-session limit, costs). Trades are then grouped
so each layer of the strategy is tested against the one below it:

  all eligible  -- every uptrend stock, every day, no setup required. The
                   baseline: what the plan earns on names picked for trend
                   alone.
  any setup     -- the pullback / breakout rules, unscored.
  top N         -- the N best-scored setups each day: what the tab suggests.

If top N does not beat any setup, the score is decoration; if any setup does
not beat all eligible, the setups are. Results are also split by regime and
by setup type, and a simple portfolio run turns them into a CAGR.

Survivorship: the universe is today's index list applied backwards, which
flatters absolute returns (today's members are the ones that did well). The
comparisons between groups share that bias, so they are the honest part.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from strategy import swing as sw


def _arrays(frames: dict[str, pd.DataFrame]) -> dict[str, tuple]:
    return {s: (f["open"].to_numpy(float), f["high"].to_numpy(float),
                f["low"].to_numpy(float), f["close"].to_numpy(float),
                f["date"].to_numpy())
            for s, f in frames.items()}


def play(panel: pd.DataFrame, frames: dict[str, pd.DataFrame], cfg: dict,
         rows: pd.DataFrame | None = None) -> pd.DataFrame:
    """Simulate a trade for every row of `rows` (default: every eligible row)."""
    arr = _arrays(frames)
    rows = panel[panel["eligible"]] if rows is None else rows
    out = []
    for r in rows[["symbol", "bar", "entry", "stop", "date"]].itertuples(index=True):
        a = arr.get(r.symbol)
        if a is None or not (r.entry == r.entry and r.stop == r.stop):
            continue
        t = sw.simulate(a[0], a[1], a[2], a[3], int(r.bar), float(r.entry),
                        float(r.stop), cfg)
        if t is None:
            continue
        out.append({"row": r.Index, "symbol": r.symbol, "date": r.date,
                    "entry_date": a[4][t.entry_idx], "exit_date": a[4][t.exit_idx],
                    "fill": t.entry, "r_net": t.r_net, "r_gross": t.r_gross,
                    "ret_pct": t.ret_pct, "reason": t.exit_reason,
                    "sessions": t.sessions})
    trades = pd.DataFrame(out)
    if trades.empty:
        return trades
    keep = ["score", "setup_pullback", "setup_breakout", "stop_pct", "rank_day"]
    keep = [k for k in keep if k in panel.columns]
    return trades.join(panel[keep], on="row")


def rank_daily(panel: pd.DataFrame) -> pd.DataFrame:
    """Rank of each setup row by score within its date (1 = best)."""
    p = panel.copy()
    has = p["eligible"] & (p["setup_pullback"] | p["setup_breakout"])
    p["rank_day"] = np.nan
    p.loc[has, "rank_day"] = (p.loc[has].groupby("date")["score"]
                              .rank(ascending=False, method="first"))
    return p


def summarise(t: pd.DataFrame) -> dict:
    if t.empty:
        return {"trades": 0}
    win = t["r_net"] > 0
    return {
        "trades": int(len(t)),
        "win_pct": round(100 * float(win.mean()), 1),
        "avg_r": round(float(t["r_net"].mean()), 3),
        "median_r": round(float(t["r_net"].median()), 3),
        "avg_ret_pct": round(float(t["ret_pct"].mean()), 2),
        "avg_win_r": round(float(t.loc[win, "r_net"].mean()), 2) if win.any() else None,
        "avg_loss_r": round(float(t.loc[~win, "r_net"].mean()), 2) if (~win).any() else None,
        "avg_sessions": round(float(t["sessions"].mean()), 1),
        "t2_pct": round(100 * float((t["reason"] == "t2").mean()), 1),
        "stopped_pct": round(100 * float((t["reason"] == "stop").mean()), 1),
    }


def groups(trades: pd.DataFrame, top_n: int) -> dict[str, pd.DataFrame]:
    setup = trades["setup_pullback"] | trades["setup_breakout"]
    return {
        "all eligible": trades,
        "any setup": trades[setup],
        f"top {top_n}": trades[setup & (trades["rank_day"] <= top_n)],
        "pullback": trades[trades["setup_pullback"]],
        "breakout": trades[trades["setup_breakout"]],
    }


def by_score_band(trades: pd.DataFrame) -> list[dict]:
    """Setup trades in score quintiles: does a higher score mean a better trade?"""
    t = trades[trades["setup_pullback"] | trades["setup_breakout"]].copy()
    if len(t) < 50:
        return []
    t["band"] = pd.qcut(t["score"], 5, labels=False, duplicates="drop")
    out = []
    for b, g in t.groupby("band"):
        out.append({"band": int(b) + 1,
                    "score": f"{g['score'].min():.0f}-{g['score'].max():.0f}",
                    **summarise(g)})
    return out


def by_year(trades: pd.DataFrame, top_n: int) -> list[dict]:
    t = trades.copy()
    t["year"] = pd.to_datetime(t["date"]).dt.year
    setup = t["setup_pullback"] | t["setup_breakout"]
    out = []
    for y, g in t.groupby("year"):
        s = setup.loc[g.index]
        top = g[s & (g["rank_day"] <= top_n)]
        out.append({"year": int(y), "eligible_r": summarise(g).get("avg_r"),
                    "setup_r": summarise(g[s]).get("avg_r"),
                    "top_r": summarise(top).get("avg_r"),
                    "top_trades": int(len(top))})
    return out


def portfolio(trades: pd.DataFrame, cfg: dict, capital: float = 1_000_000,
              max_positions: int = 8) -> dict:
    """Take the day's suggestions in score order into free slots.

    Sized by risk (risk_per_trade_pct, capped at max_position_pct), one
    position per stock, P&L booked at exit. Not marked to market daily, so
    the drawdown here is on closed trades and understates the open-position
    swing; the CAGR is unaffected.
    """
    s = cfg["swing"]
    t = trades[(trades["setup_pullback"] | trades["setup_breakout"])
               & (trades["rank_day"] <= s["top_n"])].sort_values(["entry_date", "rank_day"])
    if t.empty:
        return {}
    cash, open_, equity = capital, [], []
    held: set[str] = set()
    taken = 0
    for day, g in t.groupby("entry_date", sort=True):
        # Close what finished before today, in exit order, marking equity at
        # each exit with the rest of the book still at cost.
        done = sorted((p for p in open_ if p["exit_date"] < day),
                      key=lambda p: p["exit_date"])
        open_ = [p for p in open_ if p["exit_date"] >= day]
        for i, p in enumerate(done):
            cash += p["value"] * (1 + p["ret_pct"] / 100)
            held.discard(p["symbol"])
            rest = sum(q["value"] for q in open_) + sum(q["value"] for q in done[i + 1:])
            equity.append((p["exit_date"], cash + rest))
        book = cash + sum(p["value"] for p in open_)
        for r in g.itertuples():
            if len(open_) >= max_positions or r.symbol in held:
                continue
            risk_frac = r.stop_pct / 100
            value = min(book * s["risk_per_trade_pct"] / 100 / max(risk_frac, 1e-6),
                        book * s["max_position_pct"] / 100, cash)
            if value < book * 0.01:
                continue
            cash -= value
            open_.append({"symbol": r.symbol, "value": value, "ret_pct": r.ret_pct,
                          "exit_date": r.exit_date})
            held.add(r.symbol)
            taken += 1
    open_.sort(key=lambda p: p["exit_date"])
    for i, p in enumerate(open_):
        cash += p["value"] * (1 + p["ret_pct"] / 100)
        equity.append((p["exit_date"], cash + sum(q["value"] for q in open_[i + 1:])))
    eq = pd.Series([v for _, v in equity], index=[d for d, _ in equity])
    start, end = t["entry_date"].min(), max(d for d, _ in equity)
    years = max((pd.Timestamp(end) - pd.Timestamp(start)).days / 365.25, 1e-9)
    final = float(eq.iloc[-1]) if len(eq) else capital
    peak = eq.cummax()
    return {"trades_taken": taken, "final": round(final),
            "cagr_pct": round(100 * ((final / capital) ** (1 / years) - 1), 2),
            "closed_trade_max_dd_pct": round(float(100 * (eq / peak - 1).min()), 1),
            "years": round(years, 1), "max_positions": max_positions}


def benchmark_cagr(bench: pd.DataFrame, start, end) -> float | None:
    b = bench[(bench["date"] >= start) & (bench["date"] <= end)]
    if len(b) < 2:
        return None
    years = (pd.Timestamp(b["date"].iloc[-1]) - pd.Timestamp(b["date"].iloc[0])).days / 365.25
    return round(100 * ((b["close"].iloc[-1] / b["close"].iloc[0]) ** (1 / years) - 1), 2)


def results_split(trades: pd.DataFrame, results: dict, days: int) -> dict:
    """Setup trades with results due within `days` of the signal, against the
    rest -- only over the span the stored calendar covers (2024 onward)."""
    if not results or not days:
        return {}
    first = min(d for v in results.values() for d in v)
    t = trades[(trades["setup_pullback"] | trades["setup_breakout"])
               & (pd.to_datetime(trades["date"]) >= pd.Timestamp(first))].copy()
    if t.empty:
        return {}
    soon = []
    for sym, d in zip(t["symbol"], t["date"]):
        lo, hi = pd.Timestamp(d), pd.Timestamp(d) + pd.Timedelta(days=days)
        soon.append(any(lo <= pd.Timestamp(x) <= hi for x in results.get(sym, ())))
    t["soon"] = soon
    return {"from": str(first), "days": days,
            "results soon": summarise(t[t["soon"]]),
            "no results": summarise(t[~t["soon"]])}


def run(panel: pd.DataFrame, frames: dict, bench: pd.DataFrame, cfg: dict,
        universe: str, results: dict | None = None) -> dict:
    """Everything the CLI prints and the Swing tab reads."""
    top_n = cfg["swing"]["top_n"]
    p = rank_daily(panel)
    trades = play(p, frames, cfg)
    if trades.empty:
        return {"error": "no trades"}
    reg = sw.regime_series(bench, panel, cfg)
    trades = trades.merge(reg[["date", "regime"]], on="date", how="left")
    g = groups(trades, top_n)
    top = g[f"top {top_n}"]
    regimes = {}
    for name in ("favourable", "mixed", "unfavourable"):
        regimes[name] = {"any setup": summarise(g["any setup"][g["any setup"]["regime"] == name]),
                         f"top {top_n}": summarise(top[top["regime"] == name])}
    start, end = trades["date"].min(), trades["date"].max()
    return {
        "run_on": dt.date.today().isoformat(), "universe": universe,
        "from": str(start), "to": str(end), "top_n": top_n,
        "groups": {k: summarise(v) for k, v in g.items()},
        "score_bands": by_score_band(trades),
        "regimes": regimes,
        "years": by_year(trades, top_n),
        "portfolio": portfolio(trades, cfg),
        "results": results_split(trades, results or {},
                                 cfg["swing"].get("results_within_days", 0)),
        "benchmark_cagr_pct": benchmark_cagr(bench, start, end),
        "plan": {k: cfg["swing"][k] for k in
                 ("t1_r", "t2_r", "max_hold_sessions", "round_trip_cost_pct",
                  "risk_per_trade_pct")},
    }
