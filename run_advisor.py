"""Portfolio advisor: what to buy, add, trim or exit -- and why.

Read-only. Every line is a suggestion with its reasoning; you place the orders.
"""
from __future__ import annotations

import argparse
import datetime as dt

import numpy as np
import pandas as pd

import decision_log as dlog
import fundamentals as fu
import instruments as ins
import portfolio as pf
from data import bhavcopy as bc
from data import freshness as fr
from data import quality as dq
from data import store
from data.sources import universe as uni
from scoring import pipeline, timing
from strategy import advisor as adv
from strategy import allocation as al
from strategy import buckets as bk
from strategy import momentum as mom
from strategy import risk_monitor as rm
from strategy import tax as tx

RULE = "=" * 74


def section(t: str) -> str:
    return f"\n{RULE}\n{t}\n{RULE}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe-size", type=int, default=200)
    ap.add_argument("--total-capital", type=float, default=None,
                    help="whole portfolio incl. index funds, for the framing note")
    ap.add_argument("--cash", type=float, default=0.0,
                    help="cash on hand to fund adds, beyond what the sells raise")
    ap.add_argument("--no-log", action="store_true",
                    help="do not record this run in the decision log")
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
    equities = bc.require_equity_symbols(mkt)
    excluded |= {h for h in holdings if h in bc.non_equity_symbols(mkt)}
    pool = bc.universe_on(mkt, today, top_n=args.universe_size)
    frames, cuts = {}, dq.report(con, sorted(set(pool) | set(holdings)))
    for s in set(pool) | set(holdings):
        f = pipeline.scored_frame(con, s, cfg, usable_from=cuts.get(s))
        if not f.empty:
            frames[s] = f
    panel = mom.build_panel(frames, cfg)
    ranked = pd.DataFrame()
    if not panel.empty:
        ranked = mom.rank_on(panel, max(panel["date"]), cfg, eligible=equities)
    rank_of = dict(zip(ranked.get("symbol", []), ranked.get("rank", [])))
    vol_of = dict(zip(ranked.get("symbol", []), ranked.get("vol", [])))
    liq_rank = bc.liquidity_ranks(mkt, today)

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

    checks = [fr.assess(mkt), fr.assess_prices(con, []), fr.assess_fundamentals()]
    stale = [c for c in checks if c.label != "fresh"]
    print(f"Portfolio advisor -- {dt.date.today():%d %b %Y}")
    for c in stale:
        marker = "!!" if c.is_stale else " *"
        print(f"  {marker} {c.message}")
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

    # Sector trims are planned across the whole book, after the exits and
    # sizing trims that happen anyway (advise_book runs two passes).
    inputs = {}
    for sym, h in tradeable.items():
        last = frames[sym].iloc[-1]
        inputs[sym] = dict(
            symbol=sym, bucket=bucket_of[sym], holding=h, price=prices[sym],
            value=values[sym], book_value=book, quality=qual.get(sym),
            rank=rank_of.get(sym), rank_universe=len(ranked),
            risk=rm.holding_status(sym, frames[sym], h, cfg), cfg=cfg,
            row={"sma20": last.get("sma20"), "sma50": last.get("sma50")})
    advices = list(adv.advise_book(inputs, values, sectors, qual, book,
                                   cfg).values())

    cash = adv.apply_cash_constraint(
        advices, prices, qual, args.cash, sectors=sectors, values=values,
        book=book, max_sector_pct=cfg["risk"]["max_sector_pct"])

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

    print(f"  Sells raise about {cash['raised']:,.0f}"
          + (f" plus {cash['cash_available']:,.0f} cash on hand" if cash['cash_available'] else "")
          + f"; adds are funded from that, best first.")
    if cash["deferred"]:
        print(f"  {len(cash['deferred'])} add(s) held back -- no cash left, or "
              f"their sector would go over its cap -- and listed as WATCH.")
    print()

    for action in ("EXIT", "TRIM", "ADD", "WATCH", "HOLD"):
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

    if not args.no_log:
        rows = [{"symbol": a.symbol, "action": a.action, "bucket": a.bucket,
                 "urgency": a.urgency, "price": prices.get(a.symbol, 0.0),
                 "qty_action": a.qty, "pct_of_book": a.current_pct,
                 "pnl_pct": a.pnl_pct,
                 "quality": (qual[a.symbol].score if a.symbol in qual else None),
                 "rank": rank_of.get(a.symbol),
                 "timing_score": None, "stop": None, "reasons": a.reasons}
                for a in advices]
        log = dlog.connect()
        # Prices come from the newest session on file, which on a weekend run
        # is not today.
        price_date = max((frames[s]["date"].iloc[-1] for s in frames), default=None)
        n = dlog.record(log, rows, price_date=price_date)
        # Also log the top-ranked names you do not own -- that is what the
        # ranking is asserting, and it was going unrecorded.
        market_rows = [{"symbol": r.symbol, "price": float(frames[r.symbol]["close"].iloc[-1]),
                        "rank": int(r.rank), "quality": (qual[r.symbol].score
                                                         if r.symbol in qual else None),
                        "momentum": float(r.mom), "vol": 100 * float(r.vol),
                        "lookback": cfg["momentum_strategy"]["lookback_months"],
                        "held": r.symbol in tradeable}
                       for r in ranked.head(20).itertuples() if r.symbol in frames]
        c = dlog.record_candidates(log, market_rows, price_date=price_date)
        scored = dlog.score_past_advice(log, prices)
        stats = dlog.summary(scored)
        print(section("DECISION LOG"))
        print(f"  {n} holding decisions and {c} ranked candidates recorded.")
        if stats:
            o = stats["_overall"]
            print(f"  {o['n']} past directional calls old enough to judge "
                  f"(21+ days): {o['hit_rate_pct']:.0f}% went the right way, "
                  f"average {o['avg_outcome_pct']:+.1f}%.")
            if not o["enough_to_judge"]:
                print("  Too few to mean anything yet -- 30 is the point at "
                      "which this stops being a story about luck.")
        else:
            print("  Nothing old enough to score yet. Come back in a month.")

    sells = [{"symbol": a.symbol,
              "lt_gain": max(a.realised_gain, 0.0) if a.tax_saved or a.estimated_tax else 0.0,
              "estimated_tax": a.estimated_tax}
             for a in advices if a.is_sell and a.qty]
    if sells:
        ex = tx.apply_ltcg_exemption(sells, cfg)
        print(section("TAX ON THIS PLAN"))
        print(f"  long-term gains realised   {ex['long_term_gain']:>12,.0f}")
        print(f"  annual LTCG exemption      {ex['exemption']:>12,.0f}")
        print(f"  tax at the flat rate       {ex['tax_before_exemption']:>12,.0f}")
        print(f"  tax after the exemption    {ex['tax_after_exemption']:>12,.0f}")
        if ex["fully_covered"]:
            print(f"\n  The exemption covers this plan entirely -- "
                  f"{ex['exemption_left']:,.0f} of it is still unused this "
                  f"financial year.")
        else:
            print(f"\n  The exemption is exhausted by this plan. Gains beyond "
                  f"it are taxed at {cfg['tax']['ltcg_rate_pct']}%.")
        print("  Per-holding figures above apply the rate flat, so they are an "
              "upper bound.")

    print(section("CASH"))
    print(f"  raised by sells      {cash['raised']:>12,.0f}")
    if cash["cash_available"]:
        print(f"  cash on hand         {cash['cash_available']:>12,.0f}")
    print(f"  available            {cash['budget']:>12,.0f}")
    print(f"  allocated to adds    {cash['spent']:>12,.0f}")
    print(f"  left over            {cash['unspent']:>12,.0f}")
    if cash["wanted"] > cash["budget"]:
        print(f"\n  Adds worth {cash['wanted']:,.0f} were wanted against "
              f"{cash['budget']:,.0f} available. The shortfall is shown rather "
              f"than hidden -- the WATCH list is what did not fit.")

    if args.total_capital:
        print(section("FRAMING"))
        print("  " + al.core_satellite_note(book, args.total_capital))
    print("\nDecision support only -- not advice. You place every order yourself.")


if __name__ == "__main__":
    main()
