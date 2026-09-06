"""Portfolio advisor: what to buy, add, trim or exit -- and why.

Read-only. Every line is a suggestion with its reasoning; you place the orders.
"""
from __future__ import annotations

import argparse
import datetime as dt

import numpy as np
import pandas as pd

import fundamentals as fu
import instruments as ins
import portfolio as pf
from data import bhavcopy as bc
from data import quality as dq
from data import store
from data.sources import universe as uni
from scoring import pipeline, timing
from strategy import advisor as adv
from strategy import allocation as al
from strategy import buckets as bk
from strategy import momentum as mom
from strategy import risk_monitor as rm

RULE = "=" * 74


def section(t: str) -> str:
    return f"\n{RULE}\n{t}\n{RULE}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe-size", type=int, default=200)
    ap.add_argument("--total-capital", type=float, default=None,
                    help="whole portfolio incl. index funds, for the framing note")
    args = ap.parse_args()

    cfg = timing.load_config()
    con = store.connect()
    mkt = bc.connect("data/market.db")

    holdings = {h["tradingsymbol"]: h for h in pf.load_holdings()}
    if not holdings:
        print("No holdings found. Put them in config/holdings.csv "
              "(a Kite export works as-is).")
        return

    excluded = ins.load_excluded()
    sectors = {}
    try:
        u = uni.nifty500()
        sectors = dict(zip(u["symbol"], u["sector"]))
    except Exception:
        pass
    qual = fu.score_all(sorted(holdings), sectors)

    # Rank the live universe once; holdings are then looked up in it.
    today = dt.date.fromisoformat(sorted(bc.have_days(mkt))[-1])
    pool = bc.universe_on(mkt, today, top_n=args.universe_size)
    frames, cuts = {}, dq.report(con, sorted(set(pool) | set(holdings)))
    for s in set(pool) | set(holdings):
        f = pipeline.scored_frame(con, s, cfg, usable_from=cuts.get(s))
        if not f.empty:
            frames[s] = f
    panel = mom.build_panel(frames, cfg)
    ranked = pd.DataFrame()
    if not panel.empty:
        ranked = mom.rank_on(panel, max(panel["date"]), cfg)
    rank_of = dict(zip(ranked.get("symbol", []), ranked.get("rank", [])))
    vol_of = dict(zip(ranked.get("symbol", []), ranked.get("vol", [])))
    liq_rank = {s: i + 1 for i, s in enumerate(pool)}

    # ---------------------------------------------------------- valuation
    prices, values, tradeable = {}, {}, {}
    untradeable = []
    for sym, h in holdings.items():
        if sym in excluded or sym.endswith("-RR") or sym not in frames:
            why = ("fund/ETF or REIT -- not company equity"
                   if sym in excluded or sym.endswith("-RR")
                   else "no price history on file")
            untradeable.append((sym, why, h))
            continue
        px = float(frames[sym]["close"].iloc[-1])
        prices[sym] = px
        values[sym] = px * float(h["quantity"] or 0)
        tradeable[sym] = h
    book = sum(values.values())

    print(f"Portfolio advisor -- {dt.date.today():%d %b %Y}")
    print(f"  {len(tradeable)} equity holdings worth {book:,.0f} "
          f"({len(untradeable)} instruments not covered)")

    # ------------------------------------------------------ core/satellite
    bucket_of, bucket_reason = {}, {}
    for sym in tradeable:
        q = qual.get(sym)
        # Volatility from the holding's own price history, not only from the
        # momentum panel -- the panel omits anything below its 200 DMA, which
        # silently skipped the volatility check for exactly those holdings.
        vol = bk.realised_vol(frames.get(sym))
        if vol is None:
            vol = vol_of.get(sym)
        b, why = bk.classify(sym, q.score if q else None, vol,
                             liq_rank.get(sym), tradeable[sym].get("bucket", ""))
        bucket_of[sym], bucket_reason[sym] = b, why

    split = bk.suggest_split(tradeable, values, bucket_of)
    print(section("1. CORE vs SATELLITE"))
    print("  Core      = owned for the business. Held THROUGH momentum signals;")
    print("              sold only when the business deteriorates.")
    print("  Satellite = owned for the trend. Rotates on rank -- that rotation")
    print("              is exactly what makes momentum work.")
    print("  Legacy    = bought before this framework existed. Red flags still")
    print("              apply, but it is NOT rotated on momentum: selling on a")
    print("              rule that was not used to buy is applying it")
    print("              retroactively.\n")
    for b in ("core", "satellite", "legacy"):
        names = [s for s in tradeable if bucket_of[s] == b]
        if not names:
            continue
        val = sum(values[s] for s in names)
        print(f"  {b.upper()}  {len(names)} holdings, {val:,.0f} "
              f"({100 * val / book:.0f}% of the book)")
        for s in sorted(names, key=lambda x: -values[x]):
            print(f"    {s:<13} {values[s]:>11,.0f}  {bucket_reason[s][:62]}")
        print()
    if split:
        print(f"  {split['view']}")
    print("\n  Set `bucket` in config/holdings.csv to override any of these.")

    # ------------------------------------------------------------ advice
    sector_value: dict[str, float] = {}
    for s, v in values.items():
        sector_value[sectors.get(s, "Unknown")] = \
            sector_value.get(sectors.get(s, "Unknown"), 0.0) + v
    over_cap = {sec for sec, v in sector_value.items()
                if book and 100 * v / book > cfg["risk"]["max_sector_pct"]}

    advices = []
    for sym, h in tradeable.items():
        risk = rm.holding_status(sym, frames[sym], h, cfg)
        last = frames[sym].iloc[-1]
        advices.append(adv.advise(
            sym, bucket_of[sym], h, prices[sym], values[sym], book,
            qual.get(sym), rank_of.get(sym), len(ranked), risk, cfg,
            sector_over_cap=sectors.get(sym) in over_cap,
            row={"sma20": last.get("sma20"), "sma50": last.get("sma50")}))

    summary = adv.summarise(advices)
    warnings = adv.sanity_warnings(advices, len(ranked), args.universe_size)
    if warnings:
        print(section("!! READ THIS BEFORE THE SUGGESTIONS"))
        for w in warnings:
            print(f"  - {w}\n")

    print(section("2. SUGGESTED ACTIONS"))
    print("  TRIM = the position is too big, not bad (sizing).")
    print("  EXIT = the reason to own it is gone (thesis). No size is correct")
    print("         for a broken thesis, so exits are always the whole holding.\n")

    for action in ("EXIT", "TRIM", "ADD", "HOLD"):
        group = summary["by_action"].get(action) or []
        if not group:
            continue
        print(f"  --- {action} ({len(group)}) " + "-" * (56 - len(action)))
        for a in sorted(group, key=lambda x: -x.value):
            head = (f"  {a.symbol:<13} {a.bucket:<10} {a.current_pct:>5.1f}% of book"
                    f"  P&L {a.pnl_pct:>+7.1f}%")
            if a.action == "TRIM" and a.timing != "now":
                head += "  [WAIT]"
            if a.qty:
                head += f"  -> {a.action.lower()} {a.qty:,.0f} sh"
            if a.urgency == "urgent":
                head += "   [URGENT]"
            print(head)
            for r in a.reasons:
                print(f"        {r}")
            if a.is_sell and a.qty:
                bits = []
                if a.realised_gain:
                    bits.append(f"realises {a.realised_gain:+,.0f}")
                if a.estimated_tax:
                    bits.append(f"est. tax {a.estimated_tax:,.0f}")
                if bits:
                    print(f"        {' | '.join(bits)} -- {a.tax_note}")
                if a.tax_saved:
                    print(f"        saves {a.tax_saved:,.0f} in tax versus "
                          f"delivering short-term shares")
                if a.timing == "wait_for_ltcg":
                    print("        TIMING: hold off -- see above")
                elif a.timing == "wait_for_strength":
                    print("        TIMING: wait for a stronger day")
                elif len(a.tranches) > 1:
                    print(f"        EXECUTE: {len(a.tranches)} tranches of "
                          f"{', '.join(f'{t:,.0f}' for t in a.tranches)} shares, "
                          f"spread over a few sessions")
        print()

    if untradeable:
        print(section("3. NOT COVERED BY THIS FRAMEWORK"))
        for sym, why, h in untradeable:
            print(f"  {sym:<14} {why}")
        print("\n  These are excluded from every calculation above -- they are")
        print("  not scored, ranked or advised on. Judge them separately.")

    if args.total_capital:
        print(section("FRAMING"))
        print("  " + al.core_satellite_note(book, args.total_capital))
    print("\nDecision support only -- not advice. You place every order yourself.")


if __name__ == "__main__":
    main()
