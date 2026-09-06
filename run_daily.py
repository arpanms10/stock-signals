"""Nightly run: refresh prices, score the watchlist, emit signals.

Read-only with respect to your broker. It computes and explains; you decide and
place the order yourself.
"""
from __future__ import annotations

import argparse
import datetime as dt

import pandas as pd

import watchlist as wl
from data import ingest, quality, store
from scoring import pipeline, timing
from signals import engine
from signals import state as st


def evaluate(con, cfg: dict, portfolio_value: float,
             on_date: dt.date | None = None,
             symbols: list[str] | None = None) -> list[st.Signal]:
    symbols = symbols or wl.active_symbols()
    bench = store.load_index(con, cfg["signals"]["regime_index"])
    positions = st.load_positions(con)
    regime = engine.regime_ok(bench, on_date or dt.date.today(), cfg) if not bench.empty else True

    out: list[st.Signal] = []
    cuts = quality.report(con, symbols)
    for sym in symbols:
        f = pipeline.scored_frame(con, sym, cfg, usable_from=cuts.get(sym))
        if len(f) < 2:
            continue
        if on_date is not None:
            f = f[f["date"] <= on_date]
            if len(f) < 2:
                continue
        row, prev = f.iloc[-1], f.iloc[-2]
        today = row["date"]
        components = timing.explain(row, cfg)

        if sym in positions:
            pos = engine.update_trailing(positions[sym], row, cfg)
            st.save_position(con, pos)
            hit = engine.check_exit(row, prev, pos, cfg)
            if hit:
                out.append(st.Signal(sym, today, "SELL", hit[0], float(row["close"]),
                                     row.get("timing_score"), pos.stop_price, pos.qty,
                                     hit[1], components))
                continue
            trim = engine.check_trim(row, prev, cfg)
            if trim:
                out.append(st.Signal(sym, today, "TRIM", trim[0], float(row["close"]),
                                     row.get("timing_score"), pos.stop_price,
                                     round(pos.qty / 2), trim[1], components))
            continue

        if not regime:
            continue
        if len(positions) >= cfg["risk"]["max_positions"]:
            continue
        if engine.in_cooldown(st.last_signal_date(con, sym), today, cfg):
            continue
        fired = engine.check_entry(row, prev, cfg)
        if fired:
            px = float(row["close"])
            stop = engine.initial_stop(px, float(row.get("atr14") or 0), cfg)
            qty, binding = engine.position_size(px, stop, portfolio_value, cfg)
            t1 = t2 = None
            if cfg.get("targets", {}).get("enabled"):
                t1, t2 = engine.targets(px, stop, cfg)
            out.append(st.Signal(sym, today, "BUY", fired[0], px,
                                 row.get("timing_score"), stop, qty,
                                 f"{fired[1]}. Size set by the {binding}.", components,
                                 t1=t1, t2=t2))
    return out


BACKTEST_WARNING = (
    "!! These rules FAILED their own backtest (2.37% CAGR vs 17.73% for simply\n"
    "!! holding the same stocks, and worse than a randomised control).\n"
    "!! Signals below are shown for development only. Do not trade them."
)


def format_digest(signals: list[st.Signal], scores: pd.DataFrame,
                  regime: bool, cfg: dict, levels: dict | None = None) -> str:
    lines = [f"Signals for {dt.date.today():%d %b %Y}",
             f"Market regime: {'RISK-ON' if regime else 'RISK-OFF -- new buys suppressed'}",
             "", BACKTEST_WARNING, ""]

    lines.append("=" * 68)
    lines.append("ACTIONABLE SIGNALS -- events that fired today")
    lines.append("=" * 68)
    if not signals:
        lines.append("None. No entry or exit rule triggered on any watchlist stock.")
    for s in signals:
        lines.append(s.line())
        if s.components:
            lines.append(f"    {s.components}")
        lines.append("")

    if not scores.empty:
        lines += ["", "=" * 68,
                  "CONTEXT ONLY -- NOT BUY SIGNALS, NOT BUY PRICES",
                  "=" * 68,
                  "Current standing of the watchlist by timing score. A high score",
                  "describes a state, not a trigger: these stocks have often been",
                  "scoring high for weeks, and buying an extended move is a good way",
                  "to enter just before a pullback. A signal fires on an *event* --",
                  "a fresh cross above the threshold, a pullback-and-reclaim, or a",
                  "volume-confirmed breakout -- and every one of those appears in the",
                  "section above, never here. 'last close' is where the stock last",
                  "traded. It is not a recommended entry price.",
                  ""]
        signal_syms = {s.symbol for s in signals}
        lines.append(f"  {'symbol':<12} {'score':>6}   {'last close':>11}")
        for r in scores.head(8).itertuples():
            mark = "  <- has a signal above" if r.symbol in signal_syms else ""
            lines.append(f"  {r.symbol:<12} {r.timing_score:6.1f}   {r.close:>11.2f}{mark}")

        if levels:
            lines += ["", "-" * 68,
                      "TRIGGER LEVELS -- where a rule would fire, not a price to pay",
                      "-" * 68,
                      "These are computed backwards from the entry rules: the price at",
                      "which each rule's condition becomes true. Set a price alert on",
                      "them if you like. They are not recommendations, and reaching a",
                      "level is not sufficient on its own -- a breakout still needs its",
                      "volume confirmation, and a pullback still needs the RSI dip and",
                      "the reclaim. The signal section above is what confirms a rule",
                      "actually fired.", ""]
            for sym, lv in levels.items():
                lines.append(f"  {sym}")
                if lv["blocked"]:
                    lines.append(f"      no entry possible: {lv['blocked']}")
                for l in lv["levels"]:
                    t = ""
                    if l.get("t1"):
                        t = f"  T1 {l['t1']:.2f}  T2 {l['t2']:.2f}"
                    lines.append(f"      {l['rule']:<9} {l['price']:>10.2f} "
                                 f"({l['pct_away']:+5.1f}%)   stop {l['stop']:.2f}{t}")
                    lines.append(f"        needs: {l['note']}")

    lines.append("\nDecision support only -- not advice. You place every order yourself.")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--portfolio", type=float, default=1_000_000,
                    help="portfolio value used for position sizing")
    ap.add_argument("--no-fetch", action="store_true", help="use cached prices only")
    ap.add_argument("--as-of", type=str, default=None, help="evaluate as of YYYY-MM-DD")
    ap.add_argument("--symbols", nargs="+", default=None,
                    help="evaluate only these symbols (need not be on the watchlist)")
    args = ap.parse_args()

    con = store.connect()
    cfg = timing.load_config()
    symbols = [x.upper() for x in args.symbols] if args.symbols else wl.active_symbols()

    # A symbol named on the command line need not be on the watchlist, so it may
    # have no cached history at all. Fetch it rather than silently reporting
    # nothing -- "no signal" and "no data" look identical otherwise.
    missing = [s for s in symbols if store.last_date(con, "stock", s) is None]
    if missing:
        if args.no_fetch:
            print(f"No cached data for {', '.join(missing)}. "
                  f"Re-run without --no-fetch to download it.")
            symbols = [s for s in symbols if s not in missing]
            if not symbols:
                return
        else:
            print(f"Fetching history for {', '.join(missing)}...")
            ingest.backfill(con, missing, years=10, verbose=False)

    if not args.no_fetch:
        print("Refreshing prices...")
        ingest.backfill(con, symbols, years=10, verbose=False)

    cuts = quality.report(con, symbols)

    on_date = dt.date.fromisoformat(args.as_of) if args.as_of else None
    signals = evaluate(con, cfg, args.portfolio, on_date, symbols)
    for s in signals:
        st.log_signal(con, s)

    bench = store.load_index(con, cfg["signals"]["regime_index"])
    regime = engine.regime_ok(bench, on_date or dt.date.today(), cfg) if not bench.empty else True
    scores = pipeline.latest_scores(con, symbols, cfg)

    levels = {}
    held = set(st.load_positions(con))
    shortlist = symbols if args.symbols else (
        list(scores.head(5)["symbol"]) if not scores.empty else [])
    for sym in shortlist:
        if sym in held:
            continue          # already holding it; exits matter, not entries
        f = pipeline.scored_frame(con, sym, cfg, usable_from=cuts.get(sym))
        if not f.empty:
            levels[sym] = engine.entry_levels(f.iloc[-1], cfg)

    print(format_digest(signals, scores, regime, cfg, levels))


if __name__ == "__main__":
    main()
