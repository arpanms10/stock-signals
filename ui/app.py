"""Stock signals dashboard.

Sections:
  1. MY HOLDINGS -- what you own and what to do about it
  2. MARKET      -- the ranked universe and where entries would trigger
  3. MOMENTUM    -- top picks on both momentum lookbacks, ranked and scored
  4. WATCHING    -- flagged names under observation
  5. F&O         -- PCR, OI support/resistance, expected range (ui/fno_tab.py)

Read-only throughout. Nothing here places an order.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ui import components as c   # noqa: E402
from ui import service           # noqa: E402

st.set_page_config(page_title="Stock Signals", page_icon="📈", layout="wide")


@st.cache_data(show_spinner="Loading market data, scoring, ranking...")
def load(universe_size: int):
    return service.build(universe_size=universe_size)


# Shown next to the Quality bar, filled only when there is no score -- a blank
# bar on its own reads as "scored badly" or "broken".
QUALITY_NOTE = st.column_config.TextColumn(
    "Quality note",
    help="Why there is no quality score. Not fetched: no fundamentals on file "
         "yet -- use 'Refresh fundamentals'. Too little data: fetched, but too "
         "few figures came back to score.")


def with_quality_note(df: pd.DataFrame) -> pd.DataFrame:
    """The note column, empty where there is a score.

    Empty rather than None, which Streamlit renders as a grey "None" on every
    scored row. Snapshots cached by an older version lack the column."""
    if df.empty:
        return df
    df = df.copy()
    if "quality_note" not in df:
        df["quality_note"] = [None if q == q and q is not None else "Not fetched"
                              for q in df["quality"]]
    df["quality_note"] = df["quality_note"].fillna("")
    return df


# ------------------------------------------------------------------ sidebar
st.sidebar.title("Stock Signals")
size = st.sidebar.slider("Universe size (by liquidity)", 50, 500, 500, 50,
                         help="How many of the most liquid NSE stocks to rank. "
                              "NIFTY 500 is roughly the top 500.")
if st.sidebar.button("Recompute", width='stretch'):
    load.clear()
    st.rerun()

st.sidebar.divider()
st.sidebar.caption("**Refresh data**  \nThese fetch from NSE and take minutes.")
for label, args in service.SCRIPTS.items():
    if st.sidebar.button(label, width='stretch', key=f"s_{label}"):
        st.sidebar.write(f"Running `{' '.join(args)}`...")
        box = st.sidebar.empty()
        lines: list[str] = []
        for line in service.run_script(args):
            lines.append(line)
            box.code("\n".join(lines[-14:]), language=None)
        load.clear()
        st.sidebar.success("Done -- press Recompute to reload.")

snap = load(size)

st.sidebar.divider()
st.sidebar.caption(
    f"Data through **{snap.as_of}**  \n"
    f"Market: **{snap.regime}**  \n\n"
    "_Decision support only, not advice. You place every order yourself._")

# ------------------------------------------------------------------- header
st.title("📈 Stock Signals")
st.caption(snap.regime_detail)
for f in snap.freshness:
    if f["label"] == "stale":
        st.error(f"⚠️ {f['message']}")
    elif f["label"] == "ageing":
        st.info(f["message"])
for w in snap.warnings:
    st.warning(w)

tab_hold, tab_market, tab_mom, tab_watch, tab_fno = st.tabs(
    ["  My Holdings  ", "  Market  ", "  Momentum  ", "  Watching  ", "  F&O  "])

# =================================================================== HOLDINGS
with tab_hold:
    h = pd.DataFrame(snap.holdings)
    if h.empty:
        st.info("No holdings found. Put them in `config/holdings.csv` "
                "(a broker export works as-is).")
    else:
        invested = (h["quantity"] * h["avg_price"]).sum()
        pnl = snap.book_value - invested
        cols = st.columns(5)
        cols[0].metric("Book value", c.money(snap.book_value))
        cols[1].metric("Invested", c.money(invested))
        cols[2].metric("Unrealised P&L", c.money(pnl),
                       f"{100 * pnl / invested:+.1f}%" if invested else None)
        cols[3].metric("Holdings", f"{len(h)}")
        acts = h["action"].value_counts()
        cols[4].metric("Needs action",
                       f"{int(acts.get('EXIT', 0) + acts.get('TRIM', 0) + acts.get('ADD', 0))}")

        c.legend()
        st.divider()

        left, right = st.columns([3, 2])
        with left:
            st.subheader("Core / satellite / legacy")
            st.caption("Core is held through momentum signals. Satellite rotates "
                       "on rank. Legacy gets red-flag checks but is never sold on "
                       "a rule that was not used to buy it.")
            for b in ("core", "satellite", "legacy"):
                grp = h[h["bucket"] == b]
                if grp.empty:
                    continue
                val = grp["value"].sum()
                st.markdown(f"**{b.upper()}** — {len(grp)} holdings, "
                            f"{c.money(val)} "
                            f"({100 * val / snap.book_value:.0f}% of book)")
                st.dataframe(
                    with_quality_note(grp)[["symbol", "value", "pct_of_book",
                                            "pnl_pct", "quality", "quality_note",
                                            "rank"]]
                    .rename(columns={"pct_of_book": "% book", "pnl_pct": "P&L %",
                                     "quality": "Quality", "rank": "Rank"})
                    .sort_values("value", ascending=False),
                    hide_index=True, width='stretch',
                    column_config={
                        "value": st.column_config.NumberColumn("Value", format="₹%d"),
                        "% book": st.column_config.NumberColumn(format="%.1f%%"),
                        "P&L %": st.column_config.NumberColumn(format="%+.1f%%"),
                        "Quality": st.column_config.ProgressColumn(
                            min_value=0, max_value=100, format="%d"),
                        "quality_note": QUALITY_NOTE,
                    })
        with right:
            st.subheader("Sector exposure")
            se = pd.DataFrame(snap.sector_exposure)
            if not se.empty:
                st.dataframe(
                    se[["sector", "pct", "over_cap"]].rename(
                        columns={"sector": "Sector", "pct": "% of book",
                                 "over_cap": "Over cap"}),
                    hide_index=True, width='stretch',
                    column_config={
                        "% of book": st.column_config.ProgressColumn(
                            min_value=0, max_value=100, format="%.1f%%")})
                if se["over_cap"].any():
                    st.caption("⚠️ Holdings in one sector are one bet, not several.")
            if snap.split:
                st.caption(snap.split.get("view", ""))

        st.divider()
        st.subheader("What to do")
        order = {"EXIT": 0, "TRIM": 1, "ADD": 2, "WATCH": 3, "HOLD": 4}
        show = st.multiselect("Show", ["EXIT", "TRIM", "ADD", "WATCH", "HOLD"],
                              default=["EXIT", "TRIM", "ADD", "WATCH"])
        if snap.cash:
            cc = snap.cash
            cols = st.columns(4)
            cols[0].metric("Sells raise", c.money(cc["raised"]))
            cols[1].metric("Adds cost", c.money(cc["spent"]))
            cols[2].metric("Left over", c.money(cc["unspent"]))
            cols[3].metric("Unfunded", f"{len(cc['deferred'])}",
                           help="Worth buying, but the sells do not raise enough")
        # Urgency sorts ahead of value, so rows actually sit under the heading
        # that describes them. Printing an "urgent" heading and then listing a
        # calm row beneath it makes the grouping decorative rather than true.
        rows = sorted([r for r in snap.holdings if r["action"] in show],
                      key=lambda r: (order.get(r["action"], 9),
                                     0 if r["urgency"] == "urgent" else 1,
                                     -r["value"]))
        if not rows:
            st.success("Nothing selected needs action.")

        urgent_exits = [r for r in rows
                        if r["urgency"] == "urgent" and r["action"] == "EXIT"]
        calm_exits = [r for r in rows
                      if r["urgency"] != "urgent" and r["action"] == "EXIT"]
        headed_urgent = headed_calm = False
        for r in rows:
            if r["action"] == "EXIT" and urgent_exits:
                if r["urgency"] == "urgent" and not headed_urgent:
                    st.markdown("##### ⚠️ Urgent — a hard red flag, not a "
                                "judgement call")
                    st.caption("These fire on a balance-sheet fact (interest "
                               "cover, promoter pledge) rather than on price or "
                               "rank. They apply whatever the bucket.")
                    headed_urgent = True
                elif r["urgency"] != "urgent" and calm_exits and not headed_calm:
                    st.markdown("##### Other exits — your call on timing")
                    st.caption("Quality or trend no longer justifies holding "
                               "these. Nothing here is on fire.")
                    headed_calm = True
            urgent = r["urgency"] == "urgent"
            head = st.columns([0.5, 3, 2, 2, 2.5])
            head[0].markdown(c.badge(r["action"], urgent), unsafe_allow_html=True)
            head[1].markdown(
                f"**{r['symbol']}** &nbsp; {c.pill(r['bucket'], 'info')}"
                + (f" &nbsp; {c.pill('URGENT', 'bad')}" if urgent else ""),
                unsafe_allow_html=True)
            head[2].markdown(
                f"{c.money(r['value'])} &nbsp;·&nbsp; {r['pct_of_book']:.1f}% of book",
                unsafe_allow_html=True)
            head[3].markdown(
                f"P&L {c.pct(r['pnl_pct'], signed=True)}", unsafe_allow_html=True)
            qt = c.pill(f"quality {r['quality']:.0f}", c.quality_tone(r["quality"])) \
                if r["quality"] is not None else \
                c.pill(f"quality: {(r.get('quality_note') or 'not fetched').lower()}",
                       "warn")
            head[4].markdown(qt, unsafe_allow_html=True)

            with st.expander(
                    f"{r['action']}"
                    + (f" {r['qty_action']:,.0f} shares" if r["qty_action"] else "")
                    + (" — WAIT" if r["timing"] != "now" and r["action"] == "TRIM" else "")
                    + f"  ·  {r['symbol']}"):
                if r["action"] in ("ADD", "BUY"):
                    # Buying today means today's price sets the plan. Showing
                    # targets derived from an older, cheaper average cost would
                    # put both targets below the buy price.
                    st.markdown(
                        c.buy_plan(r["price"], r["add_stop"], r["add_t1"],
                                   r["add_t2"], c.trend_note(r)),
                        unsafe_allow_html=True)
                    if r["t1_hit"]:
                        st.caption(
                            f"Your existing lot (avg {c.money(r['avg_price'])}) "
                            f"has already passed its original targets — the plan "
                            f"above is for the shares you would add today.")
                elif r["action"] == "EXIT":
                    st.markdown(
                        c.trade_plan(r["price"], r["stop"], None, None,
                                     c.trend_note(r)), unsafe_allow_html=True)
                else:
                    st.markdown(
                        c.trade_plan(r["price"], r["stop"], r["t1"], r["t2"],
                                     c.trend_note(r), t1_hit=r["t1_hit"],
                                     t2_hit=r["t2_hit"]),
                        unsafe_allow_html=True)
                st.markdown("**Why**")
                for reason in r["reasons"]:
                    st.markdown(f"- {reason}")
                if r["flags"]:
                    st.markdown("**Fundamental flags**")
                    for f_ in r["flags"]:
                        st.markdown(f"- {f_}")
                if r["action"] in ("EXIT", "TRIM") and r["qty_action"]:
                    m = st.columns(3)
                    m[0].metric("Realises", c.money(r["realised_gain"]))
                    m[1].metric("Est. tax", c.money(r["estimated_tax"]))
                    m[2].metric("Tax saved", c.money(r["tax_saved"]),
                                help="versus delivering short-term shares")
                    st.caption(r["tax_note"])
                    if len(r["tranches"]) > 1:
                        st.caption("Execute in tranches: "
                                   + ", ".join(f"{t:,.0f}" for t in r["tranches"])
                                   + " shares over a few sessions.")
                st.caption(f"Position: {r['quantity']:,.0f} shares at "
                           f"{c.money(r['avg_price'])} average"
                           + (f" · {r['lt_qty']:,.0f} long-term, "
                              f"{r['st_qty']:,.0f} short-term"
                              if r["lt_qty"] is not None else "")
                           + f" · bucket set because {r['bucket_why']}")

        if snap.uncovered:
            with st.expander(f"Not covered by this framework "
                             f"({len(snap.uncovered)})"):
                st.caption("Excluded from every calculation above rather than "
                           "silently averaged in. Judge these separately.")
                st.dataframe(pd.DataFrame(snap.uncovered), hide_index=True,
                             width='stretch')

# ===================================================================== MARKET
with tab_market:
    m = with_quality_note(pd.DataFrame(snap.market))
    # A snapshot cached by an older version lacks newer columns; show them
    # blank until the next Recompute rather than failing the whole tab.
    for col in ("ret_12m", "ret_1m", "rank_alt"):
        if not m.empty and col not in m:
            m[col] = float("nan")
    if not m.empty and "in_both" not in m:
        m["in_both"] = False
    lbs = getattr(snap, "lookbacks", None) or [12]
    alt_label = f"{lbs[1]}M #" if len(lbs) > 1 else "Alt #"
    main_lb = lbs[0]
    if m.empty:
        st.info("No ranked universe. Run the market data refresh first.")
    else:
        cols = st.columns(5)
        cols[0].metric("Ranked", f"{len(m)}",
                       help="Names passing the 200-DMA filter out of the "
                            "liquidity pool")
        cols[1].metric("Market regime", snap.regime)
        cols[2].metric("You hold", f"{int(m['held'].sum())}")
        cols[3].metric("In the top band", f"{int(m['in_top'].sum())}")
        cols[4].metric("Top band on both", f"{int(m['in_both'].sum())}",
                       help="In the top band on BOTH lookbacks "
                            f"({' and '.join(f'{x}-month' for x in lbs)}). "
                            "Holding only these did worse than either list in the backtest.")

        st.caption(f"Ranked by **Score**: the return from {main_lb} months ago "
                   "to 1 month ago, divided by volatility, for names above their "
                   "200-day average. The latest month is left out of the score "
                   "because short-term moves tend to reverse; it is shown as "
                   "1M % so you can see it. Rank is a *state*, not a trigger — "
                   "the entry levels below say what would have to happen.")

        f1, f2, f4, f3 = st.columns([2, 2, 2, 3])
        top_only = f1.checkbox("Top band only", value=False)
        held_only = f2.checkbox("Only what I hold", value=False)
        both_only = f4.checkbox("Top band on both", value=False)
        sect = f3.multiselect("Sector", sorted(m["sector"].unique()))

        view = m.copy()
        if top_only:
            view = view[view["in_top"]]
        if both_only:
            view = view[view["in_both"]]
        if held_only:
            view = view[view["held"]]
        if sect:
            view = view[view["sector"].isin(sect)]

        st.dataframe(
            view[["rank", "rank_alt", "in_both", "symbol", "price", "ret_12m",
                  "ret_1m", "vol", "ram", "timing_score", "quality",
                  "quality_note", "sector", "held"]]
            .rename(columns={"rank": "#", "rank_alt": alt_label,
                             "in_both": "Both", "symbol": "Stock",
                             "price": "Price",
                             "ret_12m": "12M %", "ret_1m": "1M %",
                             "vol": "Vol %", "ram": "Score",
                             "timing_score": "Timing", "quality": "Quality",
                             "sector": "Sector", "held": "Held"}),
            hide_index=True, width='stretch', height=380,
            column_config={
                "Price": st.column_config.NumberColumn(format="₹%.2f"),
                "12M %": st.column_config.NumberColumn(
                    format="%.1f%%",
                    help="Price return over the last 12 months, to today"),
                "1M %": st.column_config.NumberColumn(
                    format="%.1f%%",
                    help="Price return over the last month. Not part of the "
                         "score: short-term moves tend to reverse"),
                "Vol %": st.column_config.NumberColumn(
                    format="%.0f%%",
                    help="Annualised volatility of daily returns, last year"),
                "Score": st.column_config.NumberColumn(
                    format="%.0f",
                    help=f"What the rank sorts by: return from {main_lb} months ago to "
                         "1 month ago, divided by volatility. A calm rise "
                         "scores above a violent one of the same size"),
                "Timing": st.column_config.ProgressColumn(
                    min_value=0, max_value=100, format="%d"),
                "Quality": st.column_config.ProgressColumn(
                    min_value=0, max_value=100, format="%d"),
                "quality_note": QUALITY_NOTE,
                "Held": st.column_config.CheckboxColumn(),
                alt_label: st.column_config.NumberColumn(
                    format="%d",
                    help=f"Rank on the comparison lookback ({lbs[-1]}-1 "
                         f"momentum). # is the main {main_lb}-1 rank"),
                "Both": st.column_config.CheckboxColumn(
                    help="In the top band on both lookbacks"),
            })

        st.divider()
        st.subheader("Entry levels")
        st.caption("The price at which each rule would fire, with the stop and "
                   "targets that would apply there. **Not** a recommended price "
                   "— reaching a level is necessary, not sufficient.")
        pick = st.selectbox("Stock", view["symbol"].tolist())
        row = next(r for r in snap.market if r["symbol"] == pick)

        head = st.columns([0.5, 2, 2, 2, 2])
        head[0].markdown(c.badge("BUY" if row["in_top"] else "HOLD"),
                         unsafe_allow_html=True)
        head[1].markdown(f"**{row['symbol']}** &nbsp; rank {row['rank']}",
                         unsafe_allow_html=True)
        head[2].markdown(f"{c.money(row['price'])}")
        head[3].markdown(
            c.pill(f"quality {row['quality']:.0f}", c.quality_tone(row["quality"]))
            if row["quality"] is not None else c.pill("no quality data", "warn"),
            unsafe_allow_html=True)
        head[4].markdown(c.pill("held", "good") if row["held"] else "",
                         unsafe_allow_html=True)

        st.markdown(f"<div style='color:#6b7280;font-size:13px;'>"
                    f"{c.trend_note(row)}</div>", unsafe_allow_html=True)

        lv = row["levels"]
        if lv["blocked"]:
            st.warning(f"No entry possible: {lv['blocked']}")
        for e in lv["entries"]:
            with st.container(border=True):
                away = c.pill(f"{e['pct_away']:+.1f}% away", "info")
                title = e["rule"].replace("_", " ").title()
                st.markdown(f"**{title}** &nbsp; {away}",
                            unsafe_allow_html=True)
                st.markdown(c.trade_plan(e["price"], e["stop"], e.get("t1"),
                                         e.get("t2"), f"Needs: {e['note']}"),
                            unsafe_allow_html=True)

        import watching as wg
        already = set(wg.symbols())
        if row["symbol"] in already:
            st.caption(f"✓ {row['symbol']} is on your watch list.")
        elif st.button(f"Watch {row['symbol']}", key=f"watch_{row['symbol']}"):
            ok, msg = wg.add(row["symbol"], source="rank")
            (st.success if ok else st.info)(msg)
            load.clear()

        if row["flags"]:
            with st.expander("Fundamental flags"):
                for f_ in row["flags"]:
                    st.markdown(f"- {f_}")


# ================================================================= MOMENTUM
with tab_mom:
    lbs = getattr(snap, "lookbacks", None) or []
    t = with_quality_note(pd.DataFrame(getattr(snap, "momentum_table", None) or []))
    if len(lbs) < 2 or t.empty:
        st.info("Needs a ranked universe and `compare_lookback_months` in "
                "config/scoring.yaml. Press Recompute after a data refresh.")
    else:
        a, b = lbs[0], lbs[1]
        # Rows not exclusive to the comparison list are the main top band.
        n_top = int((t["status"] != f"{b}M only").sum())
        both = t[t["status"] == "Both"]
        st.caption(
            f"Every stock in the top {n_top} on either momentum lookback. "
            f"**{a}M** is the main ranking: return from {a} months ago to 1 "
            f"month ago, divided by volatility (a calm rise scores above a "
            f"violent one of the same size). **{b}M** is the same on {b} "
            "months, shown for comparison. *Both* = top band on each.")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric(f"Top {n_top} on both", f"{len(both)}")
        m2.metric(f"{a}M only", f"{int((t['status'] == f'{a}M only').sum())}")
        m3.metric(f"{b}M only", f"{int((t['status'] == f'{b}M only').sum())}")
        m4.metric("Market regime", snap.regime,
                  help="Shown for context only. Neither the advice nor the "
                       "backtest changes in RISK-OFF: holding fewer names then "
                       "did not reduce drawdowns when tested.")

        f1, f2 = st.columns([3, 2])
        pick = f1.segmented_control(
            "Show", ["All", "Both", f"{a}M only", f"{b}M only"],
            default="All", key="mom_filter")
        held_only = f2.checkbox("Only what I hold", value=False, key="mom_held")
        view = t if pick in (None, "All") else t[t["status"] == pick]
        if held_only:
            view = view[view["held"]]

        st.dataframe(
            view[["symbol", "status", "rank_main", "score_main", "rank_cmp",
                  "score_cmp", "price", "ret_main", "ret_cmp", "ret_1m", "vol",
                  "quality", "quality_note", "sector", "held"]]
            .rename(columns={
                "symbol": "Stock", "status": "On list",
                "rank_main": f"{a}M rank", "score_main": f"{a}M score",
                "rank_cmp": f"{b}M rank", "score_cmp": f"{b}M score",
                "price": "Price", "ret_main": f"{a}M %", "ret_cmp": f"{b}M %",
                "ret_1m": "1M %", "vol": "Vol %", "quality": "Quality",
                "sector": "Sector", "held": "Held"}),
            hide_index=True, width='stretch',
            height=min(38 + 35 * len(view), 1100),
            column_config={
                f"{a}M rank": st.column_config.NumberColumn(
                    format="%d", help=f"Main ranking ({a}-1 momentum)"),
                f"{a}M score": st.column_config.NumberColumn(
                    format="%.0f", help=f"Return from {a} months ago to 1 "
                                        "month ago, divided by volatility"),
                f"{b}M rank": st.column_config.NumberColumn(
                    format="%d", help=f"Rank on {b}-1 momentum; blank if the "
                                      "stock does not qualify there"),
                f"{b}M score": st.column_config.NumberColumn(
                    format="%.0f", help=f"Return from {b} months ago to 1 "
                                        "month ago, divided by volatility"),
                "Price": st.column_config.NumberColumn(format="₹%.2f"),
                f"{a}M %": st.column_config.NumberColumn(
                    format="%.1f%%", help=f"Plain price return, last {a} months"),
                f"{b}M %": st.column_config.NumberColumn(
                    format="%.1f%%", help=f"Plain price return, last {b} months"),
                "1M %": st.column_config.NumberColumn(
                    format="%.1f%%", help="Last month. Left out of both scores: "
                                          "short-term moves tend to reverse"),
                "Vol %": st.column_config.NumberColumn(
                    format="%.0f%%", help="Annualised volatility, last year"),
                "Quality": st.column_config.ProgressColumn(
                    min_value=0, max_value=100, format="%d"),
                "quality_note": QUALITY_NOTE,
                "Held": st.column_config.CheckboxColumn(),
            })
        st.caption("Scores are only comparable within one column: a 9M and a "
                   "12M score measure different windows.")

        with st.expander("How did each approach do in the backtest?"):
            st.markdown(
                "Tested 2026-10-04 on NSE equities, top 200 by liquidity, "
                "top 15, monthly rebalance, full costs, Dec 2017 – Oct 2026. "
                "CAGR % across four start dates (the rebalance day alone "
                "moves it):\n\n"
                "| | CAGR range | average | worst fall |\n|---|---|---|---|\n"
                "| 9-month (main) | 10.9 – 15.9 | 12.5 | −39 to −43% |\n"
                "| 12-month | 11.6 – 13.4 | 12.5 | −43 to −45% |\n"
                "| Both only (~11 names) | 10.0 – 12.1 | 11.1 | −46 to −47% |\n"
                "| NIFTY 500 | ≈ 10.4 | | −37% |\n\n"
                "The two lookbacks tie: neither is proven better, which is "
                "why both are shown. Holding only the *Both* names did worse "
                "than either list. Momentum beat random picks from the same "
                "universe by about 3 standard deviations on both lookbacks, "
                "but beats the index by only about 2 points a year, with "
                "deeper falls. Details in `docs/validation.md`.")

# ==================================================================== WATCHING
with tab_watch:
    st.subheader("Suggestions under close observation")
    st.caption(
        "Measured from the date the framework **flagged** each name, not from "
        "when you added it here. A watchlist that resets the baseline on add "
        "would flatter every late addition, and you would never notice.")

    w = pd.DataFrame(snap.watching)
    if w.empty:
        st.info("Nothing being watched yet. Add candidates from the Market tab, "
                "or run `run_daily.py --watch-add SYMBOL`.")
    else:
        settled = w[w["days"].fillna(0) >= 21]
        cols = st.columns(4)
        cols[0].metric("Watching", f"{len(w)}")
        cols[1].metric("Old enough to judge", f"{len(settled)}",
                       help="21+ days since the framework flagged it")
        if not settled.empty:
            cols[2].metric("Average move", c.pct(settled["move_pct"].mean(),
                                                 signed=True))
            cols[3].metric("Went up", f"{int((settled['move_pct'] > 0).sum())}"
                                      f" of {len(settled)}")
        else:
            cols[2].metric("Average move", "--")
            cols[3].metric("Went up", "--")

        from scoring import timing as _timing
        _h = (_timing.load_config().get("targets", {})
              .get("horizon_sessions") or {})
        st.markdown(
            c.watching_table(snap.watching,
                             {k: v.get("median") for k, v in _h.items()}),
            unsafe_allow_html=True)
        st.caption(
            "**T1:17** and **T2:37** are how many sessions each target normally "
            "takes — measured across 9,650 real paths, not forecast. "
            "**Status** compares this position against that: *early*, *due*, "
            "*late*, *stalled*, or how fast it was reached. Calibration, not a "
            "rule: slow positions returned 7.4% over the next six months "
            "against 9.6% for fast ones, too small a gap to trade on.")

        young = w[w["days"].fillna(0) < 21]
        if len(young):
            st.caption(f"{len(young)} of these are under 21 days old — too "
                       f"early to read anything into the move.")

        for r in snap.watching:
            if r["notes"]:
                st.markdown(f"**{r['symbol']}** — {r['notes']}")

    st.divider()
    left, right = st.columns(2)
    with left:
        add_sym = st.text_input("Add a symbol", placeholder="e.g. CUPID",
                                key="watch_add_input")
        if st.button("Add to watch list") and add_sym.strip():
            import watching as wg
            ok, msg = wg.add(add_sym.strip().upper(), source="manual")
            (st.success if ok else st.info)(msg)
            load.clear()
    with right:
        if not w.empty:
            drop = st.selectbox("Stop watching", [""] + list(w["symbol"]),
                                key="watch_drop")
            if st.button("Remove") and drop:
                import watching as wg
                st.info(wg.remove(drop)[1])
                load.clear()

# ========================================================================= F&O
with tab_fno:
    from ui import fno_tab
    fno_tab.render()
