"""The one-stop daily guide.

Answers four questions in the order they actually matter:

    1. What is the market doing?      (regime)
    2. How is my book?                (risk monitor -- the urgent half)
    3. What should I own?             (momentum rank, quality-gated)
    4. What should I change?          (drift plan, on rebalance days only)

Read-only throughout. It computes, explains and suggests; you decide and place
every order yourself.
"""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import pandas as pd

import fundamentals as fu
import portfolio as pf
import watchlist as wl
from data import bhavcopy as bc
from data import quality, store
from data.sources import universe as uni
from scoring import pipeline, timing
from strategy import allocation as al
from strategy import combined
from strategy import momentum as mom
from strategy import risk_monitor as rm

RULE = "=" * 72


def section(title: str) -> str:
    return f"\n{RULE}\n{title}\n{RULE}"


def build(con, cfg, symbols, holdings) -> dict:
    """Everything the guide needs, computed once."""
    cuts = quality.report(con, symbols)
    frames = {}
    for s in symbols:
        f = pipeline.scored_frame(con, s, cfg, usable_from=cuts.get(s))
        if not f.empty:
            frames[s] = f
    # Holdings must always be evaluated, even if they left the watchlist.
    for s in holdings:
        if s not in frames:
            f = pipeline.scored_frame(con, s, cfg)
            if not f.empty:
                frames[s] = f
    return frames


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=1_000_000,
                    help="capital this framework governs (the satellite)")
    ap.add_argument("--total-capital", type=float, default=None,
                    help="your whole portfolio, for the core/satellite note")
    ap.add_argument("--universe", default=None,
                    help="nifty50 | nifty200 | nifty500 for the ranking pool")
    ap.add_argument("--rebalance", action="store_true",
                    help="show the rebalance plan regardless of the schedule")
    args = ap.parse_args()

    con = store.connect()
    cfg = timing.load_config()

    holdings = {(h.get("tradingsymbol") or h.get("symbol", "")).upper(): h
                for h in pf.load_holdings()}
    pool = (list(getattr(uni, args.universe)()["symbol"])
            if args.universe else wl.active_symbols())
    symbols = sorted(set(pool) | set(holdings))
    # Holdings join the panel so they can be monitored, but only ISIN-confirmed
    # equities may be ranked -- a held liquid ETF would otherwise rank first.
    equities = bc.require_equity_symbols(
        bc.connect(Path(__file__).parent / "data" / "market.db"))

    frames = build(con, cfg, symbols, holdings)
    bench = store.load_index(con, cfg["signals"]["regime_index"])

    sectors = {}
    try:
        u = uni.nifty200()
        sectors = dict(zip(u["symbol"], u["sector"]))
    except Exception:
        pass
    qual = fu.score_all(symbols, sectors)

    print(f"Daily guide -- {dt.date.today():%d %b %Y}")

    # ---------------------------------------------------------- 1. market
    print(section("1. MARKET"))
    print(f"  {rm.regime_note(bench, cfg)}")

    # ---------------------------------------------------------- 2. my book
    print(section("2. YOUR BOOK -- what needs attention"))
    if not holdings:
        print("  No holdings on file. Connect Kite MCP or write data/holdings.json")
        print("  to get risk monitoring, drift and concentration checks.")
        statuses = []
    else:
        statuses = [rm.holding_status(s, frames.get(s, pd.DataFrame()), h, cfg)
                    for s, h in holdings.items()]
        live = [s for s in statuses if "error" not in s]
        print(f"  {'symbol':<12}{'qty':>7}{'avg':>10}{'last':>10}{'P&L%':>8}"
              f"{'stop':>10}  status")
        for s in sorted(live, key=lambda x: x["pnl_pct"]):
            flag = "ALERT" if s["alerts"] else "ok"
            print(f"  {s['symbol']:<12}{s['qty']:>7.0f}{s['avg_price']:>10.2f}"
                  f"{s['close']:>10.2f}{s['pnl_pct']:>+8.1f}"
                  f"{(s['stop'] or 0):>10.2f}  {flag}")
        for s in live:
            if s["alerts"]:
                print(f"\n  {s['symbol']}:")
                for a in s["alerts"]:
                    print(f"    - {a}")
        notes = rm.portfolio_risk(statuses, cfg)
        if notes:
            print()
            for n in notes:
                print(f"  {n}")
        qflags = [q for q in (qual.get(s) for s in holdings) if q and q.flags]
        if qflags:
            print("\n  fundamental flags on holdings:")
            for q in qflags:
                for f_ in q.flags:
                    print(f"    {q.symbol}: {f_}")
        print("\n  These are review prompts, not instructions. Acting")
        print("  mechanically on them lost money in every backtest run here.")

    # ---------------------------------------------------- 3. what to own
    print(section("3. WHAT TO OWN -- momentum rank, quality-gated"))
    panel = mom.build_panel(frames, cfg)
    ranked = pd.DataFrame()
    if panel.empty:
        print("  Not enough history to rank.")
    else:
        latest = max(panel["date"])
        ranked = combined.combined_rank(panel, latest, cfg, qual,
                                         eligible=equities)
        if ranked.empty:
            print("  Nothing passes the filters today.")
        else:
            n = cfg["momentum_strategy"]["n_hold"]
            print(f"  Top {n} of {len(ranked)} eligible "
                  f"({len(panel[panel['date'] == latest])} in the pool), "
                  f"as of {latest}")
            print(f"  {'#':>3} {'symbol':<12}{'12M%':>8}{'1M%':>7}{'vol%':>7}"
                  f"{'score':>8}{'quality':>9}  held")
            for r in ranked.head(n).itertuples():
                q = "" if pd.isna(r.quality) else f"{r.quality:.0f}"
                f = frames.get(r.symbol)
                r12 = mom.trailing_return(f["close"], 12) if f is not None else None
                r1 = mom.trailing_return(f["close"], 1) if f is not None else None
                print(f"  {r.rank:>3} {r.symbol:<12}"
                      f"{'' if r12 is None else f'{r12:.1f}':>8}"
                      f"{'' if r1 is None else f'{r1:.1f}':>7}"
                      f"{100 * r.vol:>7.1f}{r.ram:>8.0f}{q:>9}"
                      f"  {'yes' if r.symbol in holdings else ''}")
            print(f"\n  score = return from {cfg['momentum_strategy']['lookback_months']}"
                  " months ago to 1 month ago, divided "
                  "by volatility.\n  The latest month (1M%) is left out of it: "
                  "short-term moves tend to reverse.")
            unknown = combined.unknown_quality_names(ranked.head(n))
            if unknown:
                print(f"\n  No fundamentals on file for: {', '.join(unknown)}")
                print("  These pass the quality gate only because nothing is")
                print("  known about them. Fill config/fundamentals.csv.")

    # ------------------------------------- 3b. agreement across lookbacks
    lbs = mom.lookbacks(cfg)
    if len(lbs) > 1 and not ranked.empty:
        n = cfg["momentum_strategy"]["n_hold"]
        rankings = {lbs[0]: ranked}
        for lb in lbs[1:]:
            c = mom.with_lookback(cfg, lb)
            p = mom.build_panel(frames, c)
            rankings[lb] = (combined.combined_rank(p, max(p["date"]), c, qual,
                                                   eligible=equities)
                            if not p.empty else pd.DataFrame())
        both = mom.common_top(rankings, n)
        label = " vs ".join(f"{lb}-MONTH" for lb in lbs)
        print(section(f"3b. {label} -- top {n} on each, and the overlap"))
        cols = [list(r["symbol"].head(n)) if not r.empty else []
                for r in rankings.values()]
        print("  " + "".join(f"{f'{lb}-1 momentum':<22}" for lb in lbs))
        for i in range(n):
            cells = []
            for c in cols:
                sym = c[i] if i < len(c) else ""
                mark = "*" if sym in both else " "
                cells.append(f"{i + 1:>3} {sym:<14}{mark}   " if sym else " " * 22)
            print("  " + "".join(cells))
        print(f"\n  * in the top {n} on every lookback: {len(both)} name(s)")
        if both:
            print("    " + ", ".join(
                f"{s}{' (held)' if s in holdings else ''}" for s in both))
        print("\n  Overlap names are strong on both windows, so less likely to "
              "be an\n  artefact of one. But holding ONLY the overlap was "
              "backtested and did\n  worse than either list (docs/validation.md).")

    # ------------------------------------------------- 4. what to change
    if args.rebalance and not ranked.empty:
        print(section("4. REBALANCE PLAN -- suggestions only"))
        n = cfg["momentum_strategy"]["n_hold"]
        target_syms = mom.target_holdings(ranked, set(holdings), cfg)
        vols = {r.symbol: r.vol for r in ranked.itertuples()}
        m = cfg["momentum_strategy"]
        targets = (al.volatility_parity_weights(target_syms, vols)
                   if m.get("volatility_parity") else al.equal_weights(target_syms))
        prices = {s: float(frames[s]["close"].iloc[-1])
                  for s in set(target_syms) | set(holdings) if s in frames}
        plan = al.build_plan(targets, holdings, prices, args.capital)
        print(f"  {'symbol':<12}{'target%':>9}{'now%':>8}{'drift':>8}"
              f"{'action':>8}{'shares':>9}  why")
        for a in plan:
            if a.action == "HOLD":
                continue
            print(f"  {a.symbol:<12}{a.target_pct:>9.1f}{a.current_pct:>8.1f}"
                  f"{a.drift_pct:>+8.1f}{a.action:>8}{a.shares_delta:>9.0f}"
                  f"  {a.reason}")
        warns = al.concentration_report(holdings, prices, sectors, cfg)
        if warns:
            print("\n  concentration:")
            for w in warns:
                print(f"    {w}")
        clusters = al.correlation_clusters(frames, list(holdings))
        for c in clusters:
            print(f"    {c}")

    if args.total_capital:
        print(section("FRAMING"))
        print("  " + al.core_satellite_note(args.capital, args.total_capital))
    print("\nDecision support only -- not advice. You place every order yourself.")


if __name__ == "__main__":
    main()
