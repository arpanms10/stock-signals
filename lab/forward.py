"""Forward records: what each forward strategy picks each month, kept
before the outcome is known, and how those picks then did.

history/lab/<strategy>.csv, append-only, one block of rows per month:
the first run of a month records it and later runs that month leave it
alone, like the momentum record (scripts/record_momentum.py). Each row is
a held name with its rank, score and the close it was recorded at, plus
the NIFTY 500 close the same day.

A month is held from its price date to the next month's price date, or to
the latest close for the open month: equal weight, no costs, so it reads
as the ranking's own forward return against the index. Holdings carry the
engine's hysteresis -- last month's names stay while they rank within
exit_rank -- so the record turns over as the backtest does.

Only strategies with status="forward" in lab/strategies.py are recorded.
"""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import pit_engine as pit
from lab.strategy import Strategy

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "history" / "lab"
COLUMNS = ["month", "recorded_on", "price_date", "strategy", "symbol", "rank",
           "score", "price", "benchmark_close"]


def path_for(name: str, folder: Path = DIR) -> Path:
    return folder / f"{name}.csv"


def read(name: str, folder: Path = DIR) -> pd.DataFrame:
    p = path_for(name, folder)
    if not p.exists():
        return pd.DataFrame(columns=COLUMNS)
    return pd.read_csv(p, dtype={"month": str})


def rank_on(strategy: Strategy, con, base: dict, on: dt.date,
            universe_size: int = 200) -> pd.DataFrame:
    """The strategy's ranking of its eligible universe at the close of `on`:
    the same universe, gate, extension cap and blend as the engine's
    rebalance. Columns symbol, rank, score, price."""
    cfg = strategy.config(base)
    m = cfg["momentum_strategy"]
    lb = m.get("lookback_months", 12) * pit.TRADING_DAYS_MONTH
    vol_w = m.get("vol_window", 252)
    warmup = max(lb + vol_w, m.get("warmup_sessions", 0))
    aliases = pit.symbol_aliases(con)
    px = pit.load_wide(con, on - dt.timedelta(days=int(warmup * 1.6)), on, aliases)
    px = px[px.index <= pd.Timestamp(on)]
    if px.empty:
        return pd.DataFrame(columns=["symbol", "rank", "score", "price"])
    rets = px.pct_change()
    vol = rets.rolling(vol_w, min_periods=vol_w // 2).std() * np.sqrt(252)
    sma200 = px.rolling(200, min_periods=200).mean()
    parts = strategy.score(pit.Inputs(px, rets, vol, sma200, m))
    if isinstance(parts, pd.DataFrame):
        parts = [(parts, 1.0)]
    ts = px.index[-1]
    row, ma = px.loc[ts], sma200.loc[ts]
    eligible = set(pit.liquid_universe(con, ts.date(), universe_size,
                                       min_price=m.get("min_price", 0.0),
                                       min_turnover_cr=m.get("min_turnover_cr", 0.0),
                                       aliases=aliases))
    gate = m.get("require_above_200dma", True)
    max_ext = m.get("max_extension_pct", 0.0)
    cand = []
    for s in eligible:
        p = row.get(s, np.nan)
        if any(pd.isna(f.loc[ts].get(s, np.nan)) for f, _ in parts) or pd.isna(p):
            continue
        a = ma.get(s, np.nan)
        if gate and (pd.isna(a) or p <= a):
            continue
        if max_ext and not pd.isna(a) and 100 * (p / a - 1) > max_ext:
            continue
        cand.append(s)
    if not cand:
        return pd.DataFrame(columns=["symbol", "rank", "score", "price"])
    if len(parts) > 1:
        def z(x):
            sd = x.std()
            return (x - x.mean()) / sd if sd else x * 0
        score = sum(w * z(f.loc[ts, cand]) for f, w in parts)
    else:
        score = parts[0][0].loc[ts, cand]
    out = pd.DataFrame({"symbol": cand, "score": score[cand].to_numpy(),
                        "price": row[cand].to_numpy()})
    out = out.sort_values(["score", "symbol"], ascending=[False, True]).reset_index(drop=True)
    out["rank"] = np.arange(1, len(out) + 1)
    return out[["symbol", "rank", "score", "price"]]


def hold(ranked: pd.DataFrame, previous: list[str], settings: dict) -> list[str]:
    """The engine's hysteresis: keep a name while it ranks within exit_rank,
    fill up from the top to n_hold with names within enter_rank."""
    n = settings.get("n_hold", 15)
    order = dict(zip(ranked["symbol"], ranked["rank"]))
    keep = sorted([s for s in previous if order.get(s, 10**6) <= settings.get("exit_rank", 2 * n)],
                  key=lambda s: order[s])
    for s in ranked["symbol"]:
        if len(keep) >= n:
            break
        if s not in keep and order[s] <= settings.get("enter_rank", n):
            keep.append(s)
    return keep[:n]


def record(strategy: Strategy, con, base: dict, on: dt.date, bench_close: float,
           folder: Path = DIR, today: dt.date | None = None, force: bool = False) -> int:
    """Record this month's picks once. Returns the number of rows written."""
    month = f"{on:%Y-%m}"
    past = read(strategy.name, folder)
    if month in set(past["month"]) and not force:
        return 0
    if force:
        past = past[past["month"] != month]
    prev_month = sorted(set(past["month"]))[-1:] if len(past) else []
    previous = list(past[past["month"] == prev_month[0]]["symbol"]) if prev_month else []
    ranked = rank_on(strategy, con, base, on)
    held = hold(ranked, previous, strategy.config(base)["momentum_strategy"])
    rows = ranked[ranked["symbol"].isin(held)]
    today = today or dt.date.today()
    out = [{"month": month, "recorded_on": today.isoformat(), "price_date": on.isoformat(),
            "strategy": strategy.name, "symbol": r.symbol, "rank": int(r.rank),
            "score": round(float(r.score), 4), "price": round(float(r.price), 2),
            "benchmark_close": round(float(bench_close), 2)} for r in rows.itertuples()]
    folder.mkdir(parents=True, exist_ok=True)
    p = path_for(strategy.name, folder)
    if force:
        past.to_csv(p, index=False)
    new = not p.exists()
    with p.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        if new:
            w.writeheader()
        w.writerows(out)
    return len(out)


def score(name: str, closes: pd.DataFrame, bench: pd.Series,
          folder: Path = DIR) -> pd.DataFrame:
    """Each recorded month's equal-weight return to the next month's price
    date (or the latest close), against the index over the same days.
    closes: wide split-adjusted closes covering the record."""
    rec = read(name, folder)
    if rec.empty:
        return pd.DataFrame(columns=["month", "from", "to", "names", "return_pct",
                                     "index_pct", "excess_pct", "open"])
    months = sorted(set(rec["month"]))
    dates = {mo: rec[rec["month"] == mo]["price_date"].iloc[0] for mo in months}
    last = closes.index[-1]
    rows = []
    for i, mo in enumerate(months):
        frm = pd.Timestamp(dates[mo])
        to = pd.Timestamp(dates[months[i + 1]]) if i + 1 < len(months) else last
        names = list(rec[rec["month"] == mo]["symbol"])
        window = closes.loc[(closes.index >= frm) & (closes.index <= to), names].ffill()
        if len(window) < 2:
            ret = np.nan
        else:
            ret = float((window.iloc[-1] / window.iloc[0] - 1).mean())
        b = bench[(bench.index >= frm) & (bench.index <= to)]
        bret = float(b.iloc[-1] / b.iloc[0] - 1) if len(b) > 1 else np.nan
        rows.append({"month": mo, "from": frm.date(), "to": to.date(), "names": len(names),
                     "return_pct": round(100 * ret, 2), "index_pct": round(100 * bret, 2),
                     "excess_pct": round(100 * (ret - bret), 2),
                     "open": i + 1 == len(months)})
    return pd.DataFrame(rows)
