"""Calibrate the Today ranges: how often did the close land inside them?

    PYTHONPATH=. .venv/bin/python run_range_backtest.py

For every F&O underlying on every day in data/fo_history.db (indices since
2019, stocks since 2024), the next-session and next-5-session bands are built
with exactly the code Today runs (strategy/ranges.py), from both the implied
and the realised volatility, and scored against the close that followed.

A one-standard-deviation band should hold ~68% of the time if the volatility
is right. Above that, the band is too wide (the options market over-prices
moves -- the volatility risk premium); below, too narrow.

Windows where the underlying moved more than 60% are dropped as splits or
bonuses (the bhavcopy spot is unadjusted); the report says how many.

Writes config/range_calibration.json (read by Today) and
reports/ranges/calibration_<date>.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from data import fo_bhavcopy as fb
from strategy import ranges as rg

ROOT = Path(__file__).parent
CALIBRATION_PATH = ROOT / "config" / "range_calibration.json"
REPORTS = ROOT / "reports" / "ranges"
MAX_LOG_MOVE = math.log(1.6)
NORMAL_HIT = 68.3


def _wide(spot: pd.DataFrame, col: str) -> pd.DataFrame:
    return spot.pivot(index="date", columns="symbol", values=col).sort_index()


def observe(con) -> pd.DataFrame:
    """One row per (date, symbol, horizon, method): the band and the close."""
    spot = fb.spot_history(con)
    spot = spot[spot["spot"] > 0]
    kind = spot.groupby("symbol")["kind"].first()
    close = _wide(spot, "spot")
    logret = np.log(close).diff()
    # A split shows up as a huge one-day "return"; keep it out of the vol.
    logret = logret.where(logret.abs() < MAX_LOG_MOVE)
    real = logret.rolling(rg.REALISED_WINDOW, min_periods=rg.REALISED_WINDOW - 2).std()
    # Realised vol must use only returns up to and including the day.
    iv_n, iv_x, sl = (_wide(spot, c) for c in ("iv_straddle", "iv_next", "sessions_left"))
    near = sl.le(rg.NEAR_EXPIRY_SESSIONS)
    iv_x_ok = iv_x.where(iv_x > 0)
    iv_n_ok = iv_n.where(iv_n > 0)
    iv_used = iv_x_ok.where(near | iv_n_ok.isna(), iv_n_ok)
    iv_used = iv_used.fillna(iv_n_ok)
    implied = iv_used / 100 / math.sqrt(rg.SESSIONS_PER_YEAR)

    res = pd.read_sql_query("SELECT symbol, date FROM fo_results", con)
    res_by = {s: np.array(sorted(g["date"])) for s, g in res.groupby("symbol")}
    dates = close.index.to_numpy()

    frames = []
    for h in rg.HORIZONS:
        fut = close.shift(-h)
        move = np.log(fut / close)
        for method, sig in (("implied", implied), ("realised", real)):
            m = sig * math.sqrt(h)
            df = pd.DataFrame({
                "sigma": sig.stack(future_stack=True),
                "spot": close.stack(future_stack=True),
                "close": fut.stack(future_stack=True),
                "move": move.stack(future_stack=True),
                "band": m.stack(future_stack=True),
            }).dropna(subset=["sigma", "spot"])
            df["horizon"], df["method"] = h, method
            frames.append(df.reset_index())
    obs = pd.concat(frames, ignore_index=True)
    obs["kind"] = obs["symbol"].map(kind)
    obs["scored"] = obs["close"].notna()
    obs["adjusted"] = obs["move"].abs() >= MAX_LOG_MOVE
    obs["inside"] = obs["move"].abs() <= obs["band"]
    obs["z"] = obs["move"].abs() / obs["band"]

    # Results date inside (date, date + h sessions]: stocks only.
    pos = {d: i for i, d in enumerate(dates)}
    end_date = []
    for d, h in zip(obs["date"], obs["horizon"]):
        j = pos[d] + h
        end_date.append(dates[j] if j < len(dates) else None)
    obs["end"] = end_date
    flag = np.zeros(len(obs), dtype=bool)
    for sym, idx in obs[obs["kind"] == "equity"].groupby("symbol").groups.items():
        r = res_by.get(sym)
        if r is None or not len(r):
            continue
        sub = obs.loc[idx]
        ends = sub["end"].fillna("9999").to_numpy(dtype=str)
        lo = np.searchsorted(r, sub["date"].to_numpy(dtype=str), side="right")
        hi = np.searchsorted(r, ends, side="right")
        flag[obs.index.get_indexer(idx)] = hi > lo
    obs["results"] = flag
    obs["year"] = obs["date"].str[:4]
    return obs


def _summ(g: pd.DataFrame) -> pd.Series:
    return pd.Series({"n": len(g), "hit_pct": 100 * g["inside"].mean(),
                      "band_pct": 100 * (np.exp(g["band"]) - 1).median(),
                      "median_z": g["z"].median(),
                      "beyond_2sd_pct": 100 * (g["z"] > 2).mean()})


def tables(obs: pd.DataFrame) -> dict[str, pd.DataFrame]:
    ok = obs[obs["scored"] & ~obs["adjusted"]]
    t = {}
    t["Hit rate by method, kind and horizon (normal: 68.3%)"] = (
        ok.groupby(["method", "kind", "horizon"]).apply(_summ, include_groups=False))
    both = ok.pivot_table(index=["date", "symbol", "horizon"], columns="method",
                          values="inside").dropna()
    if not both.empty:
        b = both.reset_index().merge(ok[["symbol", "kind"]].drop_duplicates(), on="symbol")
        t["Same rows, both methods"] = b.groupby(["kind", "horizon"])[
            ["implied", "realised"]].mean().mul(100).round(1)
    eq = ok[ok["kind"] == "equity"]
    t["Stocks: results date inside the window"] = (
        eq.groupby(["method", "horizon", "results"]).apply(_summ, include_groups=False))
    t["By year (stability)"] = (
        ok.groupby(["method", "kind", "horizon", "year"])["inside"].mean()
        .mul(100).round(1).unstack("year"))
    nifty = ok[ok["symbol"] == "NIFTY"]
    t["NIFTY alone"] = nifty.groupby(["method", "horizon"]).apply(_summ, include_groups=False)
    return t


def calibration(obs: pd.DataFrame) -> dict:
    ok = obs[obs["scored"] & ~obs["adjusted"]]
    out = {}
    for (m, k, h), g in ok.groupby(["method", "kind", "horizon"]):
        out[f"{m}|{k}|{h}"] = round(100 * g["inside"].mean(), 1)
    for (m, h, r), g in ok[ok["kind"] == "equity"].groupby(["method", "horizon", "results"]):
        out[f"{m}|equity|{h}|{'results' if r else 'clear'}"] = round(100 * g["inside"].mean(), 1)
    for (m, h), g in ok[ok["symbol"] == "NIFTY"].groupby(["method", "horizon"]):
        out[f"{m}|NIFTY|{h}"] = round(100 * g["inside"].mean(), 1)
    n = {f"{m}|{k}|{h}": int(len(g))
         for (m, k, h), g in ok.groupby(["method", "kind", "horizon"])}
    return {"hit_pct": out, "n": n}


def md_table(t: pd.DataFrame) -> str:
    t = t.round(2).reset_index()
    head = "| " + " | ".join(map(str, t.columns)) + " |"
    sep = "|" + "---|" * len(t.columns)
    rows = ["| " + " | ".join("" if pd.isna(v) else str(v) for v in r) + " |"
            for r in t.itertuples(index=False)]
    return "\n".join([head, sep, *rows])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.parse_args(argv)
    con = fb.connect()
    obs = observe(con)
    if obs.empty:
        print("No F&O history. Run run_fno_backtest.py --ingest first.")
        return 1
    scored = obs[obs["scored"]]
    dropped = int(scored["adjusted"].sum())
    ts = tables(obs)
    cal = calibration(obs)
    first, last = obs["date"].min(), obs["date"].max()
    cal.update({"built": dt.date.today().isoformat(), "from": first, "to": last,
                "dropped_as_splits": dropped,
                "source": "run_range_backtest.py: share of closes inside spot +/- one "
                          "standard deviation (strategy/ranges.py), by method|kind|sessions"})
    CALIBRATION_PATH.write_text(json.dumps(cal, indent=1))
    md = [f"# Range calibration, {dt.date.today()}",
          f"\nF&O history {first} to {last}. {len(scored):,} scored bands; "
          f"{dropped} dropped as splits/bonuses (move >60%).\n"]
    for name, t in ts.items():
        md += [f"\n## {name}\n", md_table(t)]
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"calibration_{dt.date.today():%Y%m%d}.md"
    path.write_text("\n".join(md) + "\n")
    print("\n".join(md))
    print(f"\nWritten: {path} and {CALIBRATION_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
