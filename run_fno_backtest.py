"""Validate the F&O read: did the OI range, the walls, PCR and max pain hold?

    PYTHONPATH=. .venv/bin/python run_fno_backtest.py --ingest     # download history first
    PYTHONPATH=. .venv/bin/python run_fno_backtest.py              # measure

For every underlying and monthly expiry since January 2024, the chain is read
at 20, 10 and 5 sessions before expiry with exactly the code the live view
runs (strategy/fno.py), and compared with what happened by expiry.

The question is never "how often did price stay in the range" alone -- a wide
enough range always holds. Each measure is set against a NULL: the same
levels, at the same distance from spot, scored against the pooled
distribution of every observation's actual return (same kind, same offset).
If OI placement carries information, the real hit rate beats the null. If it
does not, the walls are just distances.

Writes reports/fno/validation_<date>.md and validation_obs.csv; the narrative
lives in docs/f_o/validation.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

from data import fo_bhavcopy as fb
from scoring.timing import load_config
from strategy import fno

REPORTS = Path(__file__).parent / "reports" / "fno"
MAX_CYCLE_RATIO = 2.0      # spot moved >2x or <0.5x inside a cycle: a split/bonus


# --------------------------------------------------------------- measuring

def observe(con, cfg: dict) -> pd.DataFrame:
    """One row per (date, symbol): what OI said, and what happened."""
    chains = pd.read_sql_query("SELECT * FROM fo_chain", con)
    spot = fb.spot_history(con)
    lots = pd.read_sql_query("SELECT date, symbol, lot FROM fo_spot", con)
    spot = spot.merge(lots, on=["date", "symbol"])
    by_sym = {s: g.sort_values("date").set_index("date") for s, g in spot.groupby("symbol")}
    for side in ("ce", "pe"):
        for f in ("iv", "bid", "ask"):
            chains[f"{side}_{f}"] = np.nan

    rows = []
    for (date, sym, expiry), g in chains.groupby(["date", "symbol", "expiry"], sort=False):
        hist = by_sym.get(sym)
        if hist is None or expiry not in hist.index:
            continue                          # expiry not reached yet
        s0 = float(g["spot"].iloc[0])
        path = hist.loc[(hist.index > date) & (hist.index <= expiry)]
        close = float(hist.loc[expiry, "spot"])
        lot0 = hist.loc[date, "lot"] if date in hist.index else None
        adjusted = (path["lot"].nunique(dropna=True) > 1
                    or (lot0 is not None and pd.notna(lot0)
                        and (path["lot"].dropna() != lot0).any())
                    or not (1 / MAX_CYCLE_RATIO < close / s0 < MAX_CYCLE_RATIO))
        v = fno.analyse(sym, g.sort_values("strike").reset_index(drop=True), s0,
                        dt.date.fromisoformat(expiry),
                        dt.datetime.fromisoformat(date + "T15:30:00"), cfg=cfg)
        rows.append({
            "date": date, "symbol": sym, "expiry": expiry,
            "kind": hist["kind"].iloc[0], "sessions": int(g["sessions"].iloc[0]),
            "spot": s0, "close": close,
            "path_min": float(path["spot"].min()) if len(path) else close,
            "path_max": float(path["spot"].max()) if len(path) else close,
            "adjusted": bool(adjusted),
            "pcr_oi": v.pcr["oi"], "pcr_bias": v.pcr_bias, "bias": v.bias,
            "support": v.support.strike if v.support else np.nan,
            "resistance": v.resistance.strike if v.resistance else np.nan,
            "straddle": v.straddle if v.straddle is not None else np.nan,
            "range_low": v.range_low if v.range_low is not None else np.nan,
            "range_high": v.range_high if v.range_high is not None else np.nan,
            "max_pain": v.max_pain if v.max_pain is not None else np.nan,
        })
    obs = pd.DataFrame(rows)
    if obs.empty:
        return obs
    obs["ret_pct"] = 100 * (obs["close"] / obs["spot"] - 1)
    return obs


def _null_rate(lo_pct: pd.Series, hi_pct: pd.Series, pool: np.ndarray) -> pd.Series:
    """For each row: share of the pooled returns falling inside [lo, hi]."""
    pool = np.sort(pool)
    n = len(pool)
    lo_i = np.searchsorted(pool, lo_pct.to_numpy(), side="left")
    hi_i = np.searchsorted(pool, hi_pct.to_numpy(), side="right")
    return pd.Series((hi_i - lo_i) / n, index=lo_pct.index)


def score(obs: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Every table in the report, keyed by name."""
    o = obs[~obs["adjusted"]].copy()
    rel = lambda col: 100 * (o[col] / o["spot"] - 1)
    o["lo_c"], o["hi_c"] = rel("range_low"), rel("range_high")
    o["sup"], o["res"] = rel("support"), rel("resistance")
    o["mv"] = 100 * o["straddle"] / o["spot"]

    out = {}
    rng_rows, wall_rows, pcr_rows, mp_rows = [], [], [], []
    for (kind, k), g in o.groupby(["kind", "sessions"]):
        pool = g["ret_pct"].to_numpy()
        idx = g.index

        def block(name, lo, hi):
            ok = lo.notna() & hi.notna()
            lo, hi = lo[ok], hi[ok]
            if not ok.any():
                return
            sub = g.loc[ok[ok].index]
            hit = ((sub["ret_pct"] >= lo) & (sub["ret_pct"] <= hi))
            held = ((100 * (sub["path_min"] / sub["spot"] - 1) >= lo)
                    & (100 * (sub["path_max"] / sub["spot"] - 1) <= hi))
            null = _null_rate(lo, hi, pool)
            rng_rows.append({
                "kind": kind, "sessions": k, "range": name, "n": int(ok.sum()),
                "width_pct": (hi - lo).median(),
                "expiry_inside_pct": 100 * hit.mean(),
                "null_pct": 100 * null.mean(),
                "edge_pts": 100 * (hit.mean() - null.mean()),
                "closes_never_left_pct": 100 * held.mean(),
            })

        block("combined (live default)", g.loc[idx, "lo_c"], g.loc[idx, "hi_c"])
        block("walls only (S1-R1)", g.loc[idx, "sup"], g.loc[idx, "res"])
        block("straddle only", -g.loc[idx, "mv"], g.loc[idx, "mv"])

        for name, col, cond in (("support held (close >= S1)", "sup", "ge"),
                                ("resistance held (close <= R1)", "res", "le")):
            lvl = g[col].dropna()
            if lvl.empty:
                continue
            r = g.loc[lvl.index, "ret_pct"]
            hit = (r >= lvl) if cond == "ge" else (r <= lvl)
            null = (_null_rate(lvl, pd.Series(np.inf, index=lvl.index), pool)
                    if cond == "ge" else
                    _null_rate(pd.Series(-np.inf, index=lvl.index), lvl, pool))
            wall_rows.append({
                "kind": kind, "sessions": k, "level": name, "n": len(lvl),
                "distance_pct": lvl.abs().median(),
                "held_pct": 100 * hit.mean(), "null_pct": 100 * null.mean(),
                "edge_pts": 100 * (hit.mean() - null.mean()),
            })

        for bias, b in g.groupby("pcr_bias"):
            pcr_rows.append({
                "kind": kind, "sessions": k, "pcr_bias": bias, "n": len(b),
                "median_pcr": b["pcr_oi"].median(),
                "mean_ret_pct": b["ret_pct"].mean(),
                "up_pct": 100 * (b["ret_pct"] > 0).mean(),
            })

        m = g.dropna(subset=["max_pain"])
        if len(m):
            d_mp = (m["close"] - m["max_pain"]).abs() / m["spot"] * 100
            d_sp = (m["close"] - m["spot"]).abs() / m["spot"] * 100
            mp_rows.append({
                "kind": kind, "sessions": k, "n": len(m),
                "max_pain_closer_pct": 100 * (d_mp < d_sp).mean(),
                "median_miss_max_pain_pct": d_mp.median(),
                "median_miss_no_change_pct": d_sp.median(),
            })

    out["Ranges"] = pd.DataFrame(rng_rows)
    out["Walls"] = pd.DataFrame(wall_rows)
    out["PCR"] = pd.DataFrame(pcr_rows)
    out["Max pain"] = pd.DataFrame(mp_rows)
    out["Sample"] = (obs.groupby(["kind", "sessions"])
                     .agg(observations=("symbol", "size"),
                          underlyings=("symbol", "nunique"),
                          expiries=("expiry", "nunique"),
                          dropped_adjusted=("adjusted", "sum"),
                          first=("date", "min"), last=("date", "max"))
                     .reset_index())
    return out


def md_table(t: pd.DataFrame) -> str:
    if t.empty:
        return "_none_"
    fmt = lambda x: (f"{x:,.1f}" if isinstance(x, float) else str(x))
    lines = ["| " + " | ".join(t.columns) + " |",
             "|" + "|".join("---" for _ in t.columns) + "|"]
    lines += ["| " + " | ".join(fmt(x) for x in row) + " |"
              for row in t.itertuples(index=False)]
    return "\n".join(lines)


def to_markdown(tables: dict[str, pd.DataFrame]) -> str:
    parts = [f"# F&O validation -- run {dt.date.today()}\n"]
    for name, t in tables.items():
        parts += [f"## {name}\n", md_table(t), ""]
    return "\n".join(parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ingest", action="store_true",
                    help="download/refresh the F&O bhavcopy history first")
    ap.add_argument("--start", type=dt.date.fromisoformat, default=fb.FIRST_DAY)
    ap.add_argument("--end", type=dt.date.fromisoformat,
                    default=dt.date.today() - dt.timedelta(days=1))
    a = ap.parse_args(argv)

    con = fb.connect()
    if a.ingest:
        print(f"Ingesting F&O bhavcopy {a.start} .. {a.end} (skips days held)")
        fb.ingest_range(con, a.start, a.end)

    obs = observe(con, load_config())
    if obs.empty:
        print("No observations with a completed expiry. Run with --ingest first.")
        return 1
    tables = score(obs)
    REPORTS.mkdir(parents=True, exist_ok=True)
    md = to_markdown(tables)
    path = REPORTS / f"validation_{dt.date.today():%Y%m%d}.md"
    path.write_text(md)
    obs.to_csv(REPORTS / "validation_obs.csv", index=False)
    print(md)
    print(f"\nWritten: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
