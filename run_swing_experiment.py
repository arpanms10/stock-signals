"""Alternative swing rankings, train / holdout.

    PYTHONPATH=. .venv/bin/python run_swing_experiment.py                       # train only
    PYTHONPATH=. .venv/bin/python run_swing_experiment.py --holdout --ranking X # ONE shot

Same discipline as run_experiment.py: rankings are compared on TRAIN
(2018-2022) against a rule fixed in advance, at most one is chosen, and the
HOLDOUT (2023 onward) is run once for it. Each ranking is a hypothesis about
what makes one setup better than another, not a weight sweep.

The setups and the trade plan are the ones the Swing tab uses; only the order
of the day's setups changes. RSI(2) mean reversion is a different setup with
its own plan, tested against the same bar.
"""
from __future__ import annotations

import argparse
import datetime as dt

import numpy as np
import pandas as pd

import swing_report as sr
from backtest import swing_backtest as sb
from data import store
from scoring import timing
from strategy import momentum as mom
from strategy import swing as sw

TRAIN = (dt.date(2018, 1, 1), dt.date(2022, 12, 31))
HOLDOUT = (dt.date(2023, 1, 1), dt.date(2026, 12, 31))

# Pass rule on TRAIN, fixed before any run (2026-10-13).
MIN_EDGE_R = 0.05          # top N avg R minus all-setups avg R
MIN_BAND_RHO = 0.7         # Spearman of quintile index vs quintile avg R
MIN_GOOD_YEARS = 4         # of 5 train years with top N above all setups

# column -> (ascending?, hypothesis). Ascending = smaller value ranks first.
RANKINGS: dict[str, tuple[str, bool, str]] = {
    "score": ("score", False, "the shipped 0-100 score (control)"),
    "momentum": ("mom_ram", False, "pullbacks in momentum leaders: 9-1 "
                 "momentum / volatility, the repo's validated monthly signal"),
    "reversal": ("ret5", True, "deeper short-term dip: 1-month reversal says "
                 "the biggest recent losers bounce most"),
    "low_vol": ("vol60", True, "calmest names: low volatility trends persist"),
    "rel_strength": ("rs_raw", False, "3/6-month return over NIFTY 500 alone"),
    "random": ("rand", False, "random order: what any ranking must beat"),
}
# Seen in the 2023-26 data before this experiment was set up, so its holdout
# is contaminated: reported on train, never eligible to be chosen.
PEEKED = {"stop_width": ("stop_pct", False, "wider stop, so the fixed costs "
                         "are a smaller share of R")}


def add_extras(frames: dict, cfg: dict) -> dict:
    out = {}
    for s, f in frames.items():
        f = f.copy()
        f["mom_ram"] = mom.risk_adjusted_momentum(f["close"], 9, 1, 252)
        f["ret5"] = 100 * (f["close"] / f["close"].shift(5) - 1)
        f["vol60"] = f["close"].pct_change().rolling(60, min_periods=40).std() * np.sqrt(252)
        f["rsi2"] = sw.core.rsi(f["close"], 2)
        f["sma5"] = f["close"].rolling(5).mean()
        out[s] = f
    return out


def in_period(t: pd.DataFrame, period) -> pd.DataFrame:
    d = pd.to_datetime(t["date"])
    return t[(d >= pd.Timestamp(period[0])) & (d <= pd.Timestamp(period[1]))]


def evaluate(t: pd.DataFrame, col: str, asc: bool, top_n: int, cfg: dict,
             bench: pd.DataFrame, period) -> dict:
    """Top N by `col` each day against every setup trade, same period."""
    t = in_period(t, period).dropna(subset=[col]).copy()
    t["rank_day"] = t.groupby("date")[col].rank(ascending=asc, method="first")
    top = t[t["rank_day"] <= top_n]
    base = float(t["r_net"].mean())
    edge = float(top["r_net"].mean()) - base
    q = pd.qcut(t[col].rank(method="first", ascending=not asc), 5, labels=False)
    bands = t.groupby(q)["r_net"].mean()          # band 4 = what the ranking prefers
    # Spearman by hand (pearson on ranks): scipy is not a dependency.
    rho = float(pd.Series(range(5), dtype=float).corr(
        bands.reset_index(drop=True).rank()))
    t["year"] = pd.to_datetime(t["date"]).dt.year
    good = sum(1 for _, g in t.groupby("year")
               if g[g["rank_day"] <= top_n]["r_net"].mean() > g["r_net"].mean())
    years = t["year"].nunique()
    p = sb.portfolio(top.assign(setup_pullback=True, setup_breakout=False), cfg)
    start, end = top["date"].min(), top["date"].max()
    return {"trades_top": int(len(top)), "top_r": round(float(top["r_net"].mean()), 3),
            "all_r": round(base, 3), "edge_r": round(edge, 3),
            "bands_r": [round(float(x), 3) for x in bands], "band_rho": round(rho, 2),
            "good_years": f"{good}/{years}", "_good": good,
            "cagr_pct": p.get("cagr_pct"), "dd_pct": p.get("closed_trade_max_dd_pct"),
            "nifty500_pct": sb.benchmark_cagr(bench, start, end)}


def passes(r: dict) -> bool:
    return (r["edge_r"] >= MIN_EDGE_R and r["band_rho"] >= MIN_BAND_RHO
            and r["_good"] >= MIN_GOOD_YEARS)


# ---------------------------------------------------------- RSI(2) reversion

def mr_trades(panel: pd.DataFrame, frames: dict, cfg: dict) -> pd.DataFrame:
    """RSI(2) below 10 in an uptrend; buy the next open, sell the first close
    above the 5-day average or after 5 sessions, 3-ATR disaster stop. R is
    measured against that stop so results compare with the swing plan's."""
    s = cfg["swing"]
    rows = panel[panel["eligible"] & (panel["rsi2"] < 10)]
    arr = {k: (f["open"].to_numpy(float), f["low"].to_numpy(float),
               f["close"].to_numpy(float), f["sma5"].to_numpy(float),
               f["atr14"].to_numpy(float)) for k, f in frames.items()}
    out = []
    for r in rows[["symbol", "bar", "date", "rsi2"]].itertuples(index=False):
        o, lo, c, s5, atr = arr[r.symbol]
        i = int(r.bar)
        if i + 1 >= len(c) or not atr[i] == atr[i]:
            continue
        fill = o[i + 1]
        stop = fill - 3 * atr[i]
        exit_px = None
        for j in range(i + 1, min(i + 6, len(c))):
            if j > i + 1 and o[j] <= stop:
                exit_px = o[j]
                break
            if lo[j] <= stop:
                exit_px = stop
                break
            if c[j] > s5[j]:
                exit_px = c[j]
                break
        if exit_px is None:
            j = min(i + 5, len(c) - 1)
            exit_px = c[j]
        risk = fill - stop
        cost = fill * s["round_trip_cost_pct"] / 100
        out.append({"symbol": r.symbol, "date": r.date, "rsi2": r.rsi2,
                    "entry_date": r.date, "exit_date": r.date,
                    "r_net": (exit_px - fill - cost) / risk,
                    "ret_pct": 100 * (exit_px - fill - cost) / fill,
                    "stop_pct": 100 * risk / fill})
    return pd.DataFrame(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", choices=sr.UNIVERSES, default="nifty200")
    ap.add_argument("--holdout", action="store_true")
    ap.add_argument("--ranking", default=None)
    args = ap.parse_args()
    if args.holdout and args.ranking not in RANKINGS:
        raise SystemExit(f"--holdout needs --ranking, one of {list(RANKINGS)}")

    cfg = timing.load_config()
    top_n = cfg["swing"]["top_n"]
    con = store.connect()
    symbols, _ = sr.universe_symbols(args.universe)
    frames = add_extras(sr.load_frames(con, cfg, symbols), cfg)
    panel = sw.build_panel(frames, cfg, start=TRAIN[0])
    bench = store.load_index(con, cfg["signals"]["regime_index"])
    setup = panel["eligible"] & (panel["setup_pullback"] | panel["setup_breakout"])
    trades = sb.play(panel, frames, cfg, rows=panel[setup])
    extra = ["mom_ram", "ret5", "vol60", "rs_raw", "rsi2"]
    trades = trades.join(panel[extra], on="row")
    trades["rand"] = np.random.default_rng(7).random(len(trades))
    trades["entry_date"] = pd.to_datetime(trades["entry_date"]).dt.date
    trades["exit_date"] = pd.to_datetime(trades["exit_date"]).dt.date

    period = HOLDOUT if args.holdout else TRAIN
    label = "HOLDOUT" if args.holdout else "TRAIN"
    print(f"\n=== Swing rankings, {label} {period[0]} to {period[1]}, "
          f"{args.universe}, top {top_n} of the day's setups ===")
    names = [args.ranking] if args.holdout else list(RANKINGS)
    table = {**RANKINGS, **({} if args.holdout else PEEKED)}
    for name in names + ([] if args.holdout else list(PEEKED)):
        col, asc, why = table[name]
        r = evaluate(trades, col, asc, top_n, cfg, bench, period)
        flag = ("peeked -- not eligible" if name in PEEKED else
                "PASS" if passes(r) else "fail")
        r.pop("_good")
        print(f"\n  {name:>13} [{flag}]  {why}")
        print(f"  {'':>13} {r}")

    if not args.holdout:
        mr = in_period(mr_trades(panel, frames, cfg), period)
        mr["entry_date"] = mr["date"]
        print(f"\n  RSI(2) reversion, all signals: {len(mr)} trades, "
              f"avg R {mr['r_net'].mean():+.3f}, avg % {mr['ret_pct'].mean():+.2f}, "
              f"win {100 * (mr['r_net'] > 0).mean():.1f}%")
        r = evaluate(mr.assign(row=0), "rsi2", True, top_n, cfg, bench, period)
        # The portfolio needs real entry/exit dates; these trades carry only the
        # signal date, so a CAGR here would be wrong. Per-trade R is right.
        for k in ("_good", "cagr_pct", "dd_pct", "nifty500_pct"):
            r.pop(k)
        print(f"  RSI(2) reversion, lowest RSI(2) first: {r}")
        print(f"\n  Pass rule (fixed in advance): edge >= {MIN_EDGE_R}R over all "
              f"setups, quintile rho >= {MIN_BAND_RHO}, top N ahead in >= "
              f"{MIN_GOOD_YEARS} train years. Run --holdout for at most one.")


if __name__ == "__main__":
    main()
