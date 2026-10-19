"""Did the fund state predict how the fund did next? (scripts/run_mf.py --backtest)

At every month-end from mid-2018 (SEBI categories in force), every Direct
Growth fund in a judged category is given its state with exactly the live
rules, using only NAVs up to that month. Its next 12 and 36 months are then
measured against the category median, with funds that later closed or merged
kept in (carried at the median after their last NAV -- see
metrics.forward_excess).

The measure is forward rank in the category (share of peers beaten; 0.5 =
no information). What would make the verdicts worth acting on:
  WEAK funds go on ranking below 0.5     -> EXIT has value
  STRONG funds go on ranking above it    -> ADD has value
measured on the holdout years, which the thresholds never saw. Mean excess
return is reported for size, but not tested: fund returns are skewed, so
every group's mean sits above the median.

Overlapping windows (a 12-month outcome is measured every month) make
observations dependent, so t-statistics use Newey-West errors on the monthly
series of cross-sectional means, with lag = horizon - 1 -- and are not given
at all when the period holds fewer than two non-overlapping horizons (the
36-month outcomes in either half).

Known limitation: a fund's category is its latest AMFI label, not the one it
had at the time (AMFI publishes no history of labels). Mostly this re-files
2018-2020 multi caps as flexi caps, which is where they ended up.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from mf import metrics, navdb, verdict

ROOT = Path(__file__).resolve().parents[1]
CALIBRATION = ROOT / "config" / "mf_calibration.json"
START = pd.Period("2018-06", "M")
SPLIT = pd.Period("2022-01", "M")      # holdout from here on
HORIZONS = (12, 36)
STATES = [verdict.WEAK, verdict.SLIPPING, verdict.OK, verdict.STRONG]


def observations(P: pd.DataFrame, category: pd.Series, rules: dict,
                 start: pd.Period = START, log=print) -> pd.DataFrame:
    judged = set(rules["categories"])
    idx = rules.get("index_funds") or {}
    last = P.index.max() - min(HORIZONS)
    rows = []
    t = start
    while t <= last:
        snap = metrics.snapshot(P, category, t, idx, categories=judged)
        if not snap.empty:
            st = pd.Series({c: verdict.state(snap.loc[c], rules)[0] for c in snap.index})
            df = pd.DataFrame({"month": str(t), "code": snap.index,
                               "category": snap["category"].values, "state": st.values})
            for h in HORIZONS:
                fo = metrics.forward_outcome(P, category, t, h, list(snap.index))
                df[f"fwd_{h}"] = df["code"].map(fo["excess"])
                df[f"rank_{h}"] = df["code"].map(fo["rank"])
            rows.append(df)
        if t.month == 12:
            log(f"  through {t}")
        t += 1
    return pd.concat(rows, ignore_index=True)


def newey_west_t(x: pd.Series, lag: int) -> float:
    """None when the series spans fewer than two non-overlapping horizons:
    22 monthly 36-month outcomes are one independent observation, and no
    standard error fixes that."""
    x = x.dropna().to_numpy()
    n = len(x)
    if n < 6 or n < 2 * (lag + 1):
        return float("nan")
    m = x.mean()
    e = x - m
    var = (e @ e) / n
    for k in range(1, min(lag, n - 1) + 1):
        w = 1 - k / (lag + 1)
        var += 2 * w * (e[k:] @ e[:-k]) / n
    se = np.sqrt(var / n) if var > 0 else float("nan")
    return float(m / se) if se else float("nan")


def summarise(obs: pd.DataFrame) -> dict:
    out = {}
    for period, part in (("train", obs[obs["month"] < str(SPLIT)]),
                         ("holdout", obs[obs["month"] >= str(SPLIT)]),
                         ("all", obs)):
        res = {}
        for h in HORIZONS:
            col, rk = f"fwd_{h}", f"rank_{h}"
            p = part.dropna(subset=[rk])
            by = {}
            for s in STATES:
                q = p[p["state"] == s]
                if q.empty:
                    continue
                monthly = q.groupby("month")[rk].mean() - 0.5
                by[s] = {
                    "n": int(len(q)), "funds": int(q["code"].nunique()),
                    "months": int(monthly.size),
                    "mean_rank": float(q[rk].mean()),
                    "share_beat_median": float((q[rk] > 0.5).mean()),
                    "median_excess": float(q[col].median()),
                    "t": newey_west_t(monthly, h - 1),
                }
            # The decision that matters: STRONG vs WEAK, month by month.
            ms = p.pivot_table(index="month", columns="state", values=rk, aggfunc="mean")
            if {verdict.STRONG, verdict.WEAK} <= set(ms.columns):
                spread = (ms[verdict.STRONG] - ms[verdict.WEAK]).dropna()
                by["STRONG-WEAK"] = {"months": int(spread.size),
                                     "mean_rank": float(spread.mean()),
                                     "t": newey_west_t(spread, h - 1)}
            res[f"{h}m"] = by
        out[period] = res
    return out


def _print(summary: dict) -> None:
    nan = float("nan")
    for period in ("train", "holdout"):
        print(f"\n{period.upper()} ({'2018-06 to 2021-12' if period == 'train' else '2022-01 on'})")
        for h, by in summary[period].items():
            print(f"  next {h}: rank in category (0.50 = no information)")
            print(f"    {'state':<12}{'obs':>7}{'funds':>7}{'rank':>8}{'beat':>7}"
                  f"{'med exc':>9}{'t':>7}")
            for s, r in by.items():
                print(f"    {s:<12}{r.get('n', r['months']):>7}{r.get('funds', ''):>7}"
                      f"{r['mean_rank']:>8.2f}{r.get('share_beat_median', nan) * 100:>6.0f}%"
                      f"{r.get('median_excess', nan) * 100:>8.1f}%{r['t']:>7.1f}")


def main(con=None) -> dict:
    con = con or navdb.connect()
    rules = verdict.load_rules()
    sch = navdb.schemes(con)
    category = sch.set_index("code")["category"]
    P = navdb.panel(con)
    print(f"Panel: {P.shape[1]} schemes, {P.index.min()} to {P.index.max()}")
    alive_now = P.iloc[-1].notna()
    judged = category[category.isin(rules["categories"])].index.intersection(P.columns)
    print(f"Judged categories: {len(judged)} funds ever, "
          f"{int((~alive_now[judged]).sum())} of them since closed or merged")
    obs = observations(P, category, rules)
    summary = summarise(obs)
    _print(summary)
    payload = {"generated": pd.Timestamp.now().isoformat(timespec="seconds"),
               "panel_end": str(P.index.max()), "split": str(SPLIT), "summary": summary}
    CALIBRATION.write_text(json.dumps(payload, indent=2))
    obs_path = ROOT / "reports" / "mf" / "mf_backtest_observations.csv"
    obs_path.parent.mkdir(parents=True, exist_ok=True)
    obs.to_csv(obs_path, index=False)
    print(f"\nSaved {CALIBRATION.relative_to(ROOT)} and {obs_path.relative_to(ROOT)}")
    return summary


def load_calibration(path: Path = CALIBRATION) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


def calibration_line(cal: dict | None = None) -> str | None:
    """One line for the live view: what WEAK and STRONG were worth, holdout years."""
    cal = cal or load_calibration()
    if not cal:
        return None
    tr = cal["summary"]["train"].get("12m", {})
    ho = cal["summary"]["holdout"].get("12m", {})
    if not all(k in d for d in (tr, ho) for k in ("WEAK", "STRONG", "STRONG-WEAK")):
        return None
    return (f"Backtest, next 12 months (rank in category, 0.50 = no information): "
            f"holdout {cal['split']} on, WEAK {ho['WEAK']['mean_rank']:.2f} / STRONG "
            f"{ho['STRONG']['mean_rank']:.2f} (gap t {ho['STRONG-WEAK']['t']:.1f}); "
            f"2018-2021, WEAK {tr['WEAK']['mean_rank']:.2f} / STRONG {tr['STRONG']['mean_rank']:.2f}. "
            f"A weak, unstable edge: treat EXIT as 'look at alternatives', not a forecast.")
