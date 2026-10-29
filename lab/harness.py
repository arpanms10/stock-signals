"""One test for every strategy: the same universe, costs, controls and split.

    train    2018-07-01 .. 2022-12-31, the first date every ranking can rank
    holdout  2023-01-01 .. the latest day in data/market.db, run once

Each run measures the strategy against three bars:

  NIFTY 500            the index, price only like the strategy
  random picks         SEEDS runs drawing the same number of names at random
                       from the strategy's own eligible set (same universe,
                       same gate) -- what the ranking has to add
  the universe         every eligible name, equal-weighted, rebalanced on the
                       same schedule: buy-and-hold of the names it picks from

METRICS names what a pass rule (lab/rules.py) can check.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from backtest.portfolio_engine import PortfolioResult
from lab import runner
from lab.strategy import Strategy

TRAIN = (dt.date(2018, 7, 1), dt.date(2022, 12, 31))
HOLDOUT_START = dt.date(2023, 1, 1)
SEEDS = 5

METRICS = {
    "cagr_pct": "CAGR after costs, %",
    "sharpe": "annualised Sharpe of daily returns",
    "max_drawdown_pct": "worst fall from a peak, % (negative)",
    "excess_cagr_pct": "CAGR minus NIFTY 500's, points",
    "cagr_vs_bench": "same as excess_cagr_pct (for rules that allow a shortfall)",
    "sharpe_vs_bench": "Sharpe minus NIFTY 500's",
    "drawdown_vs_bench_pts": "how much shallower the worst fall is than NIFTY 500's, points",
    "edge_vs_controls_pct": "CAGR minus the mean of the random-pick controls, points",
    "edge_vs_controls_sd": "that edge in standard deviations of the controls",
    "excess_vs_universe_pct": "CAGR minus the equal-weighted universe's, points",
    "avg_turnover_pct": "share of the book traded per rebalance, %",
    "cost_drag_pct_yr": "costs per year, % of starting capital",
}


def control_score(strategy: Strategy):
    """A ranking with no information: the strategy's own score times zero,
    so the eligible set (where the score exists) is unchanged and the random
    draw does not depend on how the strategy happened to order it."""
    def score(x):
        parts = strategy.score(x)
        if isinstance(parts, pd.DataFrame):
            return parts * 0.0
        return [(f * 0.0, w) for f, w in parts]
    return score


def _variant(strategy: Strategy, suffix: str, score=None, **settings) -> Strategy:
    return Strategy(f"{strategy.name}:{suffix}", strategy.family, strategy.summary,
                    score or strategy.score, {**strategy.settings, **settings},
                    strategy.status)


def _sharpe(curve: pd.Series) -> float:
    r = curve.pct_change().dropna()
    return float(np.sqrt(252) * r.mean() / r.std()) if len(r) > 1 and r.std() else 0.0


def _dd(curve: pd.Series) -> float:
    return float(100 * (curve / curve.cummax() - 1).min()) if len(curve) else float("nan")


def evaluate(strategy: Strategy, con, base: dict, start: dt.date, end: dt.date,
             bench: pd.DataFrame, *, seeds: int = SEEDS, universe_size: int = 200,
             universe_skip: int = 0, controls: bool = True, progress=None) -> dict:
    """Run the strategy and its bars over [start, end]; return every metric
    plus the curves (not JSON: popped by callers that save)."""
    say = progress or (lambda msg: None)
    kw = dict(universe_size=universe_size, universe_skip=universe_skip)
    say(f"{strategy.name} {start} .. {end}")
    res: PortfolioResult = runner.run(strategy, con, base, start, end, bench, **kw)
    m = dict(res.metrics)
    b = res.benchmark
    out = {"strategy": strategy.name, "start": str(start), "end": str(end),
           "universe": f"liquidity ranks {universe_skip + 1}-{universe_skip + universe_size}"}
    bench_m = {"cagr_pct": m.get("benchmark_cagr_pct"),
               "max_drawdown_pct": m.get("benchmark_max_drawdown_pct"),
               "sharpe": round(_sharpe(b), 2)}
    m["cagr_vs_bench"] = m.get("excess_cagr_pct")
    m["sharpe_vs_bench"] = round(m.get("sharpe", 0.0) - bench_m["sharpe"], 2)
    m["drawdown_vs_bench_pts"] = round(m.get("max_drawdown_pct", 0.0)
                                       - (bench_m["max_drawdown_pct"] or 0.0), 2)
    curves = {"strategy": res.equity, "benchmark": b}

    if controls:
        cagrs = []
        for seed in range(1, seeds + 1):
            say(f"  random picks, seed {seed}/{seeds}")
            r = runner.run(_variant(strategy, "control", control_score(strategy)),
                           con, base, start, end, bench, shuffle_seed=seed, **kw)
            cagrs.append(float(r.metrics.get("cagr_pct", 0.0)))
            curves[f"control_{seed}"] = r.equity
        mean, sd = float(np.mean(cagrs)), float(np.std(cagrs))
        out["controls"] = {"cagr_pct": cagrs, "mean": round(mean, 2), "sd": round(sd, 2)}
        m["edge_vs_controls_pct"] = round(m.get("cagr_pct", 0.0) - mean, 2)
        m["edge_vs_controls_sd"] = round(m["edge_vs_controls_pct"] / sd, 2) if sd else None

        say("  equal-weighted universe")
        big = 10 ** 6
        u = runner.run(_variant(strategy, "universe", control_score(strategy),
                                n_hold=big, enter_rank=big, exit_rank=big),
                       con, base, start, end, bench, **kw)
        out["universe_ew"] = {k: u.metrics.get(k) for k in
                              ("cagr_pct", "sharpe", "max_drawdown_pct")}
        m["excess_vs_universe_pct"] = round(m.get("cagr_pct", 0.0)
                                            - u.metrics.get("cagr_pct", 0.0), 2)
        curves["universe_ew"] = u.equity

    out["metrics"] = m
    out["benchmark"] = bench_m
    out["holdings_log"] = [[str(d), h] for d, h in res.holdings_log]
    out["_curves"] = curves
    return out


def judge(strategy_name: str, split: str, result: dict) -> dict | None:
    from lab.rules import rule_for
    rule = rule_for(strategy_name)
    if rule is None:
        return None
    return {"rule": rule.fingerprint(), **rule.judge(split, result["metrics"])}


def table(result: dict) -> pd.DataFrame:
    """The headline rows: strategy and its three bars."""
    m, b = result["metrics"], result["benchmark"]
    rows = [{"": result["strategy"], "CAGR %": m.get("cagr_pct"), "Sharpe": m.get("sharpe"),
             "Max DD %": m.get("max_drawdown_pct"),
             "Turnover %/rebal": m.get("avg_turnover_pct"),
             "Cost drag %/yr": m.get("cost_drag_pct_yr")},
            {"": "NIFTY 500", "CAGR %": b.get("cagr_pct"), "Sharpe": b.get("sharpe"),
             "Max DD %": b.get("max_drawdown_pct")}]
    if "universe_ew" in result:
        u = result["universe_ew"]
        rows.append({"": "universe, equal weight", "CAGR %": u.get("cagr_pct"),
                     "Sharpe": u.get("sharpe"), "Max DD %": u.get("max_drawdown_pct")})
    if "controls" in result:
        c = result["controls"]
        rows.append({"": f"random picks ({len(c['cagr_pct'])} seeds)",
                     "CAGR %": c["mean"]})
    return pd.DataFrame(rows).set_index("")
