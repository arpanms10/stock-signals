"""Train/holdout experiment harness.

The discipline this file exists to enforce: variants are compared on the TRAIN
period only, one is chosen, and the HOLDOUT is then run exactly once. Looking at
holdout results before choosing -- or going back to try another variant after
seeing them -- turns the holdout into training data and destroys the only
independent evidence available.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt

import watchlist as wl
from backtest import engine as bt
from backtest.costs import CostModel
from data import quality, store
from scoring import pipeline, timing

TRAIN = (dt.date(2016, 9, 1), dt.date(2022, 12, 31))
HOLDOUT = (dt.date(2023, 1, 1), dt.date(2026, 9, 30))


def override(cfg: dict, patch: dict) -> dict:
    """Deep-copy cfg with nested keys replaced, e.g. {'risk': {'stop_atr_mult': 3.5}}."""
    out = copy.deepcopy(cfg)
    for section, values in patch.items():
        if isinstance(values, dict):
            out.setdefault(section, {}).update(values)
        else:
            out[section] = values
    return out


# Structural variants, not threshold sweeps. Each embodies one hypothesis about
# *why* the baseline failed, so a win is interpretable rather than accidental.
VARIANTS: dict[str, dict] = {
    "baseline": {},
    "wide_stops": {
        # Hypothesis: stops sit inside normal noise and cut good trades.
        "risk": {"stop_atr_mult": 3.5, "stop_max_loss_pct": 25.0,
                 "trail_atr_mult": 4.0, "trail_activate_pct": 20.0},
    },
    "trend_200": {
        # Hypothesis: exiting on the 50 DMA ends positions during ordinary
        # pullbacks, long before the trend has actually broken.
        "signals": {"trend_break_sma": 200, "exit_score": 20.0},
    },
    "let_winners_run": {
        # Both of the above, plus: no fixed targets, no death-cross exit, and no
        # cooldown -- the position ends only when the long trend genuinely fails.
        "risk": {"stop_atr_mult": 3.5, "stop_max_loss_pct": 25.0,
                 "trail_atr_mult": 4.0, "trail_activate_pct": 20.0},
        "signals": {"trend_break_sma": 200, "exit_score": None,
                    "use_death_cross": False},
        "targets": {"enabled": False},
    },
}


def build_frames(con, cfg, symbols, window):
    cuts = quality.report(con, symbols)
    frames = {}
    for s in symbols:
        f = pipeline.scored_frame(con, s, cfg, usable_from=cuts.get(s))
        if f.empty:
            continue
        f = f[(f["date"] >= window[0]) & (f["date"] <= window[1])]
        if len(f) > 250:
            frames[s] = f.reset_index(drop=True)
    return frames


def buy_and_hold(frames) -> float:
    """Equal-weight buy-and-hold of the same names -- the bar that matters."""
    import numpy as np
    rets, spans = [], []
    for f in frames.values():
        rets.append(f["close"].iloc[-1] / f["close"].iloc[0])
        spans.append((f["date"].iloc[-1] - f["date"].iloc[0]).days / 365.25)
    if not rets:
        return float("nan")
    return 100 * (float(np.mean(rets)) ** (1 / max(float(np.mean(spans)), 1e-9)) - 1)


def evaluate(con, base_cfg, symbols, window, names, capital):
    frames_cache = {}
    rows = []
    for name in names:
        cfg = override(base_cfg, VARIANTS[name])
        key = str(VARIANTS[name].get("signals", {}))
        if key not in frames_cache:
            frames_cache[key] = build_frames(con, cfg, symbols, window)
        frames = frames_cache[key]
        bench = store.load_index(con, cfg["signals"]["regime_index"])
        res = bt.run(frames, bench, cfg, capital, CostModel())
        m = res.metrics
        rows.append((name, m))
    return rows, buy_and_hold(next(iter(frames_cache.values())))


def table(title, rows, bh):
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")
    print(f"  {'variant':<18} {'CAGR%':>8} {'maxDD%':>8} {'Sharpe':>7} "
          f"{'trades':>7} {'hit%':>6} {'avgWin%':>8} {'holdDays':>9}")
    for name, m in rows:
        print(f"  {name:<18} {m.get('cagr_pct', 0):>8.2f} "
              f"{m.get('max_drawdown_pct', 0):>8.2f} {m.get('sharpe', 0):>7.2f} "
              f"{m.get('trades', 0):>7} {m.get('hit_rate_pct', 0):>6.1f} "
              f"{m.get('avg_win_pct', 0):>8.2f} {m.get('avg_holding_days', 0):>9.1f}")
    if rows:
        print(f"  {'-' * 74}")
        print(f"  {'NIFTY 500 index':<18} {rows[0][1].get('benchmark_cagr_pct', 0):>8.2f}")
        print(f"  {'buy & hold same':<18} {bh:>8.2f}   <- the bar to beat")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=1_000_000)
    ap.add_argument("--holdout", action="store_true",
                    help="run the sealed holdout. Do this ONCE, after choosing on train.")
    ap.add_argument("--variant", default=None,
                    help="with --holdout: the single variant chosen on train")
    args = ap.parse_args()

    con = store.connect()
    base = timing.load_config()
    symbols = wl.all_symbols()

    if not args.holdout:
        rows, bh = evaluate(con, base, symbols, TRAIN, list(VARIANTS), args.capital)
        table(f"TRAIN  {TRAIN[0]} .. {TRAIN[1]}   (choose here)", rows, bh)
        print("\n  Choose one variant from this table, then run:")
        print("    python run_experiment.py --holdout --variant <name>")
        return

    if not args.variant:
        raise SystemExit("--holdout requires --variant: choose on train first.")
    rows, bh = evaluate(con, base, symbols, HOLDOUT, [args.variant], args.capital)
    table(f"HOLDOUT  {HOLDOUT[0]} .. {HOLDOUT[1]}   (one shot)", rows, bh)
    print("\n  This period was never used to choose anything. If the variant")
    print("  does not beat buy & hold here, it does not work -- do not go back")
    print("  and try another variant against this table.")


if __name__ == "__main__":
    main()
