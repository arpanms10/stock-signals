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
REACH_PATH = Path(__file__).parent / "config" / "fno_reach.json"
CALIBRATION_PATH = Path(__file__).parent / "config" / "fno_calibration.json"
SPLIT = "2025-07-01"       # reach calibration: curve built before, tested after
MAX_CYCLE_RATIO = 2.0      # spot moved >2x or <0.5x inside a cycle: a split/bonus


# --------------------------------------------------------------- measuring

def _regime(start: str) -> pd.Series:
    """date -> 'rising' / 'falling': NIFTY 500 above or below its 200-day
    average (the dashboard's own regime definition), from signals.db."""
    from data import store
    try:
        con = store.connect()
        idx = store.load_index(con, "NIFTY 500")
        con.close()
    except Exception:
        return pd.Series(dtype=object)
    if idx.empty:
        return pd.Series(dtype=object)
    c = idx.set_index(idx["date"].astype(str))["close"]
    ma = c.rolling(200).mean()
    return pd.Series(np.where(c > ma, "rising", "falling"), index=c.index)[ma.notna()]


def observe(con, cfg: dict) -> pd.DataFrame:
    """One row per (date, symbol, expiry): what the chain said, its volatility
    context, and what happened by expiry."""
    chains = pd.read_sql_query("SELECT * FROM fo_chain", con)
    spot = fb.spot_history(con)
    # Indices: the index's real daily high/low beats one estimated from the future.
    ohlc = pd.read_sql_query("SELECT date, symbol, high AS ih, low AS il FROM fo_index_ohlc", con)
    spot = spot.merge(ohlc, on=["date", "symbol"], how="left")
    spot["spot_high"] = spot["ih"].fillna(spot["spot_high"])
    spot["spot_low"] = spot["il"].fillna(spot["spot_low"])
    by_sym = {s: g.sort_values("date").set_index("date") for s, g in spot.groupby("symbol")}
    res = pd.read_sql_query("SELECT symbol, date FROM fo_results", con)
    results = {s: sorted(g["date"]) for s, g in res.groupby("symbol")}
    regime = _regime("2019-01-01")
    for side in ("ce", "pe"):
        for f in ("iv", "bid", "ask"):
            chains[f"{side}_{f}"] = np.nan

    rows = []
    for (date, sym, expiry), g in chains.groupby(["date", "symbol", "expiry"], sort=False):
        hist = by_sym.get(sym)
        if hist is None or expiry not in hist.index or date not in hist.index:
            continue                          # expiry not reached yet
        s0 = float(g["spot"].iloc[0])
        path = hist.loc[(hist.index > date) & (hist.index <= expiry)]
        close = float(hist.loc[expiry, "spot"])
        lot0 = hist.loc[date, "lot"]
        adjusted = (path["lot"].nunique(dropna=True) > 1
                    or (pd.notna(lot0) and (path["lot"].dropna() != lot0).any())
                    or not (1 / MAX_CYCLE_RATIO < close / s0 < MAX_CYCLE_RATIO))
        kind = hist["kind"].iloc[0]
        cycle = g["cycle"].iloc[0] or "monthly"
        v = fno.analyse(sym, g.sort_values("strike").reset_index(drop=True), s0,
                        dt.date.fromisoformat(expiry),
                        dt.datetime.fromisoformat(date + "T15:30:00"), cfg=cfg,
                        kind=kind)
        old_lo, old_hi, _, _ = fno.expected_range(s0, v.straddle, v.support, v.resistance)
        before = hist.loc[hist.index < date]
        today = hist.loc[date]
        iv = today["iv_straddle"]
        pct, _ = fno.iv_percentile(iv if pd.notna(iv) else None, before["iv_straddle"])
        rv = fno.realized_vol(pd.concat([before["spot"], pd.Series([s0])]))
        r_dates = [d for d in results.get(sym, []) if date < d <= expiry]
        rows.append({
            "date": date, "symbol": sym, "expiry": expiry,
            "kind": kind, "cycle": cycle,
            "group": f"{kind} weekly" if cycle == "weekly" else kind,
            "src": today.get("src"), "sessions": int(g["sessions"].iloc[0]),
            "spot": s0, "close": close,
            "path_min": float(path["spot"].min()) if len(path) else close,
            "path_max": float(path["spot"].max()) if len(path) else close,
            "path_low": float(path["spot_low"].min()) if path["spot_low"].notna().any() else np.nan,
            "path_high": float(path["spot_high"].max()) if path["spot_high"].notna().any() else np.nan,
            "adjusted": bool(adjusted),
            "pcr_oi": v.pcr["oi"], "pcr_bias": v.pcr_bias, "bias": v.bias,
            "support": v.support.strike if v.support else np.nan,
            "resistance": v.resistance.strike if v.resistance else np.nan,
            "straddle": v.straddle if v.straddle is not None else np.nan,
            "range_low": v.range_low if v.range_low is not None else np.nan,
            "range_high": v.range_high if v.range_high is not None else np.nan,
            "max_pain": v.max_pain if v.max_pain is not None else np.nan,
            "old_low": old_lo if old_lo is not None else np.nan,
            "old_high": old_hi if old_hi is not None else np.nan,
            "iv": iv, "iv_pct": pct, "rv20": rv,
            "iv_rv": iv / rv if pd.notna(iv) and rv else np.nan,
            "skew": today["iv_put_wing"] - today["iv_call_wing"]
            if pd.notna(today["iv_put_wing"]) and pd.notna(today["iv_call_wing"]) else np.nan,
            "term": today["iv_next"] - iv if pd.notna(today["iv_next"]) and pd.notna(iv) else np.nan,
            "results_in_cycle": bool(r_dates) if kind == "equity" else None,
            "regime": regime.get(date),
        })
    obs = pd.DataFrame(rows)
    if obs.empty:
        return obs
    obs["ret_pct"] = 100 * (obs["close"] / obs["spot"] - 1)
    # The realised move in units of the priced move: 1.0 = exactly the straddle.
    obs["moves"] = (obs["close"] - obs["spot"]).abs() / obs["straddle"]
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
    o["lo_o"], o["hi_o"] = rel("old_low"), rel("old_high")
    o["sup"], o["res"] = rel("support"), rel("resistance")

    out = {}
    rng_rows, wall_rows, pcr_rows, mp_rows = [], [], [], []
    for (kind, k), g in o.groupby(["group", "sessions"]):
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

        block("±straddle (live)", g.loc[idx, "lo_c"], g.loc[idx, "hi_c"])
        block("tighter of wall/straddle (pre-2026-10-08)", g.loc[idx, "lo_o"],
              g.loc[idx, "hi_o"])
        block("walls only (S1-R1)", g.loc[idx, "sup"], g.loc[idx, "res"])

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
    out["Sample"] = (obs.groupby(["group", "src", "sessions"])
                     .agg(observations=("symbol", "size"),
                          underlyings=("symbol", "nunique"),
                          expiries=("expiry", "nunique"),
                          dropped_adjusted=("adjusted", "sum"),
                          first=("date", "min"), last=("date", "max"))
                     .reset_index())
    return out


FACTORS = {"iv_pct": "IV percentile", "iv_rv": "IV / realised vol",
           "skew": "skew (put - call IV, pts)", "term": "next month - this month IV, pts"}


def _quintiles(g: pd.DataFrame, col: str) -> pd.Series:
    x = g[col]
    return pd.qcut(x.rank(method="first"), 5, labels=[1, 2, 3, 4, 5])


def context_tables(obs: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Does the volatility context predict the SIZE of the move (in priced
    units, `moves`), and does skew predict its direction?

    A factor matters if the share of expiries inside the straddle, and the
    average move in straddles, shift steadily across its quintiles. Spearman
    rho between factor and realised move summarises it; with n in the
    thousands, |rho| below ~0.03 is noise.
    """
    o = obs[(~obs["adjusted"]) & (obs["cycle"] == "monthly")
            & obs["sessions"].isin([5, 10])].dropna(subset=["straddle"]).copy()
    o["inside"] = o["moves"] <= 1
    o["signed"] = (o["close"] - o["spot"]) / o["straddle"]
    size_rows, dir_rows, summ = [], [], []
    for kind, g in o.groupby("kind"):
        for col, label in FACTORS.items():
            h = g.dropna(subset=[col])
            if len(h) < 100:
                continue
            q = _quintiles(h, col)
            rho = h[col].rank().corr(h["moves"].rank())     # Spearman, without scipy
            summ.append({"kind": kind, "factor": label, "n": len(h),
                         "rho_vs_move_size": rho,
                         "inside_q1_pct": 100 * h.loc[q == 1, "inside"].mean(),
                         "inside_q5_pct": 100 * h.loc[q == 5, "inside"].mean(),
                         "moves_q1": h.loc[q == 1, "moves"].mean(),
                         "moves_q5": h.loc[q == 5, "moves"].mean()})
            for b, hb in h.groupby(q, observed=True):
                size_rows.append({"kind": kind, "factor": label, "quintile": int(b),
                                  "n": len(hb), "factor_median": hb[col].median(),
                                  "inside_straddle_pct": 100 * hb["inside"].mean(),
                                  "mean_moves": hb["moves"].mean()})
            if col == "skew":
                for b, hb in h.groupby(q, observed=True):
                    dir_rows.append({"kind": kind, "skew_quintile": int(b), "n": len(hb),
                                     "skew_median": hb["skew"].median(),
                                     "up_pct": 100 * (hb["signed"] > 0).mean(),
                                     "mean_signed_moves": hb["signed"].mean()})
    out = {"Move size vs volatility context (summary)": pd.DataFrame(summ),
           "Move size by quintile": pd.DataFrame(size_rows),
           "Direction vs skew": pd.DataFrame(dir_rows)}

    eq = o[o["kind"] == "equity"].dropna(subset=["results_in_cycle"])
    if len(eq):
        out["Results inside the cycle (stocks)"] = (
            eq.groupby("results_in_cycle")
            .agg(n=("moves", "size"), inside_straddle_pct=("inside", "mean"),
                 mean_moves=("moves", "mean"), median_iv=("iv", "median"))
            .assign(inside_straddle_pct=lambda t: 100 * t["inside_straddle_pct"])
            .reset_index())
    rg = o.dropna(subset=["regime"])
    if len(rg):
        out["Market regime (NIFTY 500 vs its 200-day average)"] = (
            rg.groupby(["kind", "regime"])
            .agg(n=("moves", "size"), inside_straddle_pct=("inside", "mean"),
                 mean_moves=("moves", "mean"), up_pct=("signed", lambda x: (x > 0).mean()))
            .assign(inside_straddle_pct=lambda t: 100 * t["inside_straddle_pct"],
                    up_pct=lambda t: 100 * t["up_pct"])
            .reset_index())
    return out


LOW_IV_PCT = 20


def conditional_rates(obs: pd.DataFrame) -> dict:
    """Stocks only (the index sample is too small to split): the +/- straddle
    hit rate by IV-percentile band and by results-inside-the-cycle, pooled
    over 5 and 10 sessions out. Only these two passed the within-date check
    (docs/f_o/validation.md); the rest are not used."""
    o = obs[(~obs["adjusted"]) & (obs["cycle"] == "monthly") & (obs["kind"] == "equity")
            & obs["sessions"].isin([5, 10])].dropna(subset=["straddle"])
    inside = o["moves"] <= 1
    out = {"overall": round(100 * inside.mean(), 1), "n": int(len(o))}
    # Only the bottom band: it was the weakest in 2024 and in 2025-26 alike,
    # and held up within dates. The middle bands' higher rates appeared in
    # one period only, so they are reported, not quoted.
    p = o.dropna(subset=["iv_pct"])
    low = p[p["iv_pct"] < LOW_IV_PCT]
    out["low_iv_pct"] = {"below": LOW_IV_PCT, "n": int(len(low)),
                         "pct": round(100 * (low["moves"] <= 1).mean(), 1)}
    r = o.dropna(subset=["results_in_cycle"])
    out["results_in_cycle"] = {
        str(bool(k)).lower(): {"n": int(len(g)), "pct": round(100 * (g["moves"] <= 1).mean(), 1)}
        for k, g in r.groupby("results_in_cycle")}
    return out


def calibration(tables: dict) -> dict:
    """Measured +/- straddle hit rates by group and sessions, for the live view."""
    r = tables["Ranges"]
    r = r[r["range"] == "±straddle (live)"]
    out: dict = {}
    for row in r.itertuples():
        out.setdefault(row.kind, {})[int(row.sessions)] = round(float(row.expiry_inside_pct), 1)
    return out


def reach_calibration(obs: pd.DataFrame, split: str = SPLIT) -> pd.DataFrame:
    """Out of sample: build the reach curve on obs before `split`, then
    check its probabilities -- and the model's -- on obs after it.

    Levels tested per observation: both walls and spot +/- straddle, i.e.
    the targets and stops someone would actually look up. Binned by the
    predicted probability; a calibrated estimate has actual ~= predicted
    in every bin.
    """
    o = obs[(~obs["adjusted"]) & (obs["cycle"] == "monthly")].dropna(subset=["straddle"])
    curve = fno.reach_curve(o[o["date"] < split])
    test = o[(o["date"] >= split) & o["sessions"].isin([5, 10])]
    rows = []
    for r in test.itertuples():
        for lvl in (r.support, r.resistance, r.spot + r.straddle, r.spot - r.straddle):
            if pd.isna(lvl):
                continue
            x = fno.reach(r.spot, r.straddle, float(lvl), r.kind, curve)
            if x is None or x.hist_touch is None:
                continue
            touched = (r.path_max >= lvl) if x.side == "up" else (r.path_min <= lvl)
            beyond = (r.close >= lvl) if x.side == "up" else (r.close <= lvl)
            ext = r.path_high if x.side == "up" else r.path_low
            hit_i = (np.nan if pd.isna(ext) else
                     bool(ext >= lvl) if x.side == "up" else bool(ext <= lvl))
            rows.append({"kind": r.kind, "hist_touch": x.hist_touch,
                         "hist_expiry": x.hist_expiry, "model_expiry": x.model_expiry,
                         "hist_touch_intraday": x.hist_touch_intraday,
                         "touched": bool(touched), "beyond": bool(beyond),
                         "touched_intraday": hit_i})
    t = pd.DataFrame(rows)
    out = []
    if t.empty:
        return pd.DataFrame(out)
    bins = [0, .1, .2, .3, .4, .5, .7, 1.0001]
    for kind, g in t.groupby("kind"):
        for pred, actual in (("hist_touch", "touched"), ("hist_expiry", "beyond"),
                             ("model_expiry", "beyond"),
                             ("hist_touch_intraday", "touched_intraday")):
            g2 = g.dropna(subset=[pred, actual])
            if g2.empty:
                continue
            g2 = g2.assign(**{actual: g2[actual].astype(float)})
            b = pd.cut(g2[pred], bins, right=False)
            for interval, h in g2.groupby(b, observed=True):
                out.append({"kind": kind, "estimate": pred, "bin": str(interval),
                            "n": len(h), "predicted_pct": 100 * h[pred].mean(),
                            "actual_pct": 100 * h[actual].mean()})
    return pd.DataFrame(out)


def write_reach_curve(obs: pd.DataFrame, path: Path = REACH_PATH) -> dict:
    import json
    curves = fno.reach_curve(obs[obs["cycle"] == "monthly"])
    path.write_text(json.dumps({
        "built": dt.date.today().isoformat(),
        "source": "run_fno_backtest.py: F&O bhavcopy, 5 and 10 sessions before "
                  "monthly expiries, up and down pooled, daily closes",
        "first": str(obs["date"].min()), "last": str(obs["date"].max()),
        "curves": curves,
    }, indent=1))
    return curves


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
    ap.add_argument("--ingest-old", action="store_true",
                    help="also download the pre-2024 index history (once; ~1 hour)")
    ap.add_argument("--start", type=dt.date.fromisoformat, default=fb.FIRST_DAY)
    ap.add_argument("--end", type=dt.date.fromisoformat,
                    default=dt.date.today() - dt.timedelta(days=1))
    a = ap.parse_args(argv)

    con = fb.connect()
    if a.ingest:
        print(f"Ingesting F&O bhavcopy {a.start} .. {a.end} (skips days held)")
        fb.ingest_range(con, a.start, a.end)
        print("Index OHLC and results dates")
        last = con.execute("SELECT MAX(date) FROM fo_index_ohlc").fetchone()[0]
        fb.ingest_index_ohlc(con, dt.date.fromisoformat(last) if last else fb.OLD_FIRST_DAY,
                             a.end)
        fb.ingest_results(con, a.start, a.end + dt.timedelta(days=60))
    if a.ingest_old:
        print("Old-format index history, 2019-2023 (skips days held)")
        fb.ingest_old_range(con, fb.OLD_FIRST_DAY, fb.FIRST_DAY - dt.timedelta(days=1))

    obs = observe(con, load_config())
    if obs.empty:
        print("No observations with a completed expiry. Run with --ingest first.")
        return 1
    tables = score(obs)
    tables.update(context_tables(obs))
    tables["Reach calibration (out of sample)"] = reach_calibration(obs)
    curves = write_reach_curve(obs)
    import json
    CALIBRATION_PATH.write_text(json.dumps({
        "built": dt.date.today().isoformat(),
        "source": "run_fno_backtest.py: share of expiry closes inside spot +/- ATM "
                  "straddle, by group and sessions before expiry",
        "range_hit_pct": calibration(tables),
        "equity_conditional": conditional_rates(obs)}, indent=1))
    REPORTS.mkdir(parents=True, exist_ok=True)
    md = to_markdown(tables)
    path = REPORTS / f"validation_{dt.date.today():%Y%m%d}.md"
    path.write_text(md)
    obs.to_csv(REPORTS / "validation_obs.csv", index=False)
    print(md)
    print(f"\nWritten: {path}")
    sizes = ", ".join(f"{k} n={v['n']}" for k, v in curves.items())
    print(f"Reach curves ({sizes}) -> {REACH_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
