"""52-week-high and low-volatility, on the survivorship-free universe.

    PYTHONPATH=. .venv/bin/python run_factor_backtest.py            # train only
    PYTHONPATH=. .venv/bin/python run_factor_backtest.py --holdout  # once, after

Same engine, universe, costs and execution as the momentum backtest
(backtest/pit_engine.py): the 200 most liquid NSE equities rebuilt at every
monthly rebalance from data/market.db, delisted names included, 15 names
equal-weighted, enter at rank 15 and leave past 30. Only the ranking differs:

  high52   close / highest close of the last 252 sessions, nearest first
           (George & Hwang 2004: the 52-week high anchors and under-reacts)
  lowvol   lowest 252-session realised volatility first (the low-volatility
           anomaly; its claim is risk-adjusted return, not raw return)

No 200-DMA gate: these test the factor itself. Momentum (as configured) and
random picks from the same universe run alongside as references.

PASS RULE -- fixed 2026-10-19, committed before the first run.

  Train  2018-07-01 .. 2022-12-31 (the first date every score can rank).
    high52 passes if BOTH
      excess CAGR over NIFTY 500 >= +2.0 pts a year, after costs, and
      CAGR beats the mean of 5 random-pick controls by >= 2.0 pts AND >= 1 sd.
    lowvol passes if ALL of
      Sharpe >= NIFTY 500's Sharpe + 0.20,
      max drawdown shallower than NIFTY 500's by >= 5 pts, and
      CAGR >= NIFTY 500's CAGR - 1.0 pt.
  Holdout 2023-01-01 .. latest, run ONCE (--holdout), only for what passed:
    high52  excess CAGR > 0 and CAGR above the control mean;
    lowvol  Sharpe above NIFTY 500's and max drawdown shallower.
  A factor that passes both may become a page; anything else is recorded as
  no edge in docs/validation.md and goes no further.

Liquidity ranks 201-500 over the whole period are printed as a robustness
read; they are not part of the rule.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import pit_engine as pit
from backtest.costs import CostModel
from data import bhavcopy as bc
from data import store
from scoring import timing

ROOT = Path(__file__).parent
CALIBRATION_PATH = ROOT / "config" / "factor_calibration.json"
TRAIN = (dt.date(2018, 7, 1), dt.date(2022, 12, 31))
HOLDOUT_START = dt.date(2023, 1, 1)
SEEDS = 5
FACTORS = ("high52", "lowvol")


def cfg_for(base: dict, score: str) -> dict:
    cfg = dict(base)
    ms = {**base["momentum_strategy"]}
    if score in FACTORS:
        ms.update(score=score, require_above_200dma=False)
    cfg["momentum_strategy"] = ms
    return cfg


def bench_stats(bcurve: pd.Series) -> dict:
    r = bcurve.pct_change().dropna()
    return {"sharpe": round(float(np.sqrt(252) * r.mean() / r.std()), 2) if r.std() else 0.0}


def run_window(con, base, bench, start, end, size=200, skip=0, controls=True) -> dict:
    out = {}
    for score in (*FACTORS, "momentum"):
        res = pit.run(con, cfg_for(base, score), start, end, bench, 1_000_000,
                      CostModel(), size, universe_skip=skip)
        out[score] = res.metrics
        if score == "momentum":
            out["benchmark"] = {"cagr_pct": res.metrics.get("benchmark_cagr_pct"),
                                "max_drawdown_pct": res.metrics.get("benchmark_max_drawdown_pct"),
                                **bench_stats(res.benchmark)}
    if controls:
        cagrs = []
        for seed in range(1, SEEDS + 1):
            m = pit.run(con, cfg_for(base, "lowvol"), start, end, bench, 1_000_000,
                        CostModel(), size, shuffle_seed=seed, universe_skip=skip).metrics
            cagrs.append(m.get("cagr_pct", 0.0))
        out["controls"] = {"cagr_pct": cagrs, "mean": round(float(np.mean(cagrs)), 2),
                           "sd": round(float(np.std(cagrs)), 2)}
    return out


def judge_train(w: dict) -> dict:
    b, c = w["benchmark"], w["controls"]
    h, lv = w["high52"], w["lowvol"]
    edge = h["cagr_pct"] - c["mean"]
    high52 = (h["excess_cagr_pct"] >= 2.0 and edge >= 2.0 and edge >= c["sd"])
    lowvol = (lv["sharpe"] >= b["sharpe"] + 0.20
              and lv["max_drawdown_pct"] - b["max_drawdown_pct"] >= 5.0
              and lv["cagr_pct"] >= b["cagr_pct"] - 1.0)
    return {"high52": bool(high52), "lowvol": bool(lowvol)}


def judge_holdout(w: dict) -> dict:
    b, c = w["benchmark"], w["controls"]
    h, lv = w["high52"], w["lowvol"]
    return {"high52": bool(h["excess_cagr_pct"] > 0 and h["cagr_pct"] > c["mean"]),
            "lowvol": bool(lv["sharpe"] > b["sharpe"]
                           and lv["max_drawdown_pct"] > b["max_drawdown_pct"])}


def table(w: dict) -> pd.DataFrame:
    rows = []
    for k in (*FACTORS, "momentum", "benchmark"):
        m = w[k]
        rows.append({"": "NIFTY 500" if k == "benchmark" else k,
                     "CAGR %": m.get("cagr_pct"), "Sharpe": m.get("sharpe"),
                     "Max DD %": m.get("max_drawdown_pct"),
                     "Turnover %/rebal": m.get("avg_turnover_pct"),
                     "Cost drag %/yr": m.get("cost_drag_pct_yr")})
    if "controls" in w:
        rows.append({"": f"random picks ({SEEDS} seeds)", "CAGR %": w["controls"]["mean"],
                     "Sharpe": None, "Max DD %": None,
                     "Turnover %/rebal": None, "Cost drag %/yr": None})
    return pd.DataFrame(rows).set_index("")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--holdout", action="store_true",
                    help="run the 2023- holdout for factors that passed train (once)")
    ap.add_argument("--db", default="data/market.db")
    a = ap.parse_args(argv)
    con = bc.connect(a.db)
    base = timing.load_config()
    bench = store.load_index(store.connect(), base["signals"]["regime_index"])
    last = dt.date.fromisoformat(sorted(bc.have_days(con))[-1])

    saved = json.loads(CALIBRATION_PATH.read_text()) if CALIBRATION_PATH.exists() else {}
    if not a.holdout:
        print(f"Train {TRAIN[0]} .. {TRAIN[1]}, liquidity ranks 1-200")
        w = run_window(con, base, bench, *TRAIN)
        print(table(w).to_string())
        print(f"random-pick CAGRs: {w['controls']['cagr_pct']} (sd {w['controls']['sd']})")
        verdict = judge_train(w)
        print("\nPass rule (train):", verdict)
        print("\nRobustness, liquidity ranks 201-500, whole period (not in the rule):")
        r = run_window(con, base, bench, TRAIN[0], last, size=300, skip=200, controls=False)
        print(table(r).to_string())
        saved.update({"built": dt.date.today().isoformat(), "train": w,
                      "train_pass": verdict, "ranks_201_500": r})
    else:
        if "train_pass" not in saved:
            print("Run the train split first.")
            return 1
        if saved.get("holdout"):
            print(f"The holdout was already run on {saved['holdout_run']}: it is spent.")
            return 1
        print(f"Holdout {HOLDOUT_START} .. {last}, liquidity ranks 1-200 -- run once")
        w = run_window(con, base, bench, HOLDOUT_START, last)
        print(table(w).to_string())
        verdict = {k: v and saved["train_pass"][k] for k, v in judge_holdout(w).items()}
        print("\nPass rule (holdout, only for train passes):", verdict)
        saved.update({"holdout": w, "holdout_pass": verdict,
                      "holdout_run": dt.date.today().isoformat()})
    CALIBRATION_PATH.write_text(json.dumps(saved, indent=1, default=str))
    print(f"\nWritten: {CALIBRATION_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
