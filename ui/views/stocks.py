"""Portfolio › Stocks: your holdings with a verdict each, and the watch list.

Holdings and Watching are one page with a switch, since the watch list is
the not-yet-bought half of the same list. The code is the old My Holdings and
Watching tabs, moved; what they compute is unchanged.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from ui import components as c
from ui import service, state

VIEWS = ["Holdings", "Watching"]


def render() -> None:
    snap = state.snapshot()
    top = st.columns([6, 1.5, 1.5], vertical_alignment="bottom")
    with top[0]:
        state.page_title("Stocks", f"Prices to {snap.as_of} · market {snap.regime}")
    if top[1].button("Suggest buckets", width="stretch", key="stocks_buckets",
                     help="Runs scripts/set_buckets.py: pre-fills core or satellite "
                          "for holdings without one, then reloads."):
        state.run_and_reload("Suggest buckets", service.SCRIPTS["Suggest buckets"])
    label = "Refresh prices (watchlist + holdings)"
    if top[2].button("Refresh prices", width="stretch", key="stocks_prices",
                     help="Runs scripts/run_backfill.py, then reloads."):
        state.run_and_reload(label, service.SCRIPTS[label])
    state.refreshed_notice()
    c.evidence("tested", "Trade verdicts follow the momentum rank, which held up in a "
                         "survivorship-free backtest. Exits on red flags are rules on "
                         "balance-sheet facts, not forecasts.")
    for w in snap.warnings:
        st.warning(w)

    if pref := st.session_state.pop("stocks_view_pref", None):
        st.session_state["stocks_view"] = pref
    st.session_state.setdefault("stocks_view", VIEWS[0])
    view = st.segmented_control("View", VIEWS, key="stocks_view",
                                label_visibility="collapsed") or VIEWS[0]
    if view == "Watching":
        _watching(snap)
    else:
        _holdings(snap)


def _holdings(snap) -> None:
    h = pd.DataFrame(snap.holdings)
    if h.empty:
        st.info("No holdings yet. Fill in `config/holdings.xlsx` (its *How to "
                "fill* sheet explains each column), or run `scripts/import_kite_holdings.py` "
                "on a broker download. `setup_project.py` creates the blank file.")
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
            st.subheader("Core / Satellite")
            st.caption("Core: owned for a decade or more, held through momentum "
                       "signals. Satellite: one to ten years -- red flags and "
                       "sizing apply, and falling out of the ranking asks for a "
                       "review, never a sale. Trade (bucket=trade in "
                       "config/holdings.csv): rotates on rank and its stop.")
            for b in ("core", "satellite", "trade"):
                grp = h[h["bucket"] == b]
                if grp.empty:
                    continue
                val = grp["value"].sum()
                st.markdown(f"**{b.capitalize()}** — {len(grp)} holdings, "
                            f"{c.money(val)} "
                            f"({100 * val / snap.book_value:.0f}% of book)")
                st.dataframe(
                    state.with_results(state.with_quality_note(grp))[
                        ["symbol", "value", "pct_of_book", "pnl_pct", "quality",
                         "quality_note", "results", "rank"]]
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
                        "quality_note": state.QUALITY_NOTE,
                        "results": state.RESULTS_COL,
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
                f"**{r['symbol']}** &nbsp; {c.pill(r['bucket'].capitalize(), 'info')}"
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


def _watching(snap) -> None:
    st.subheader("Suggestions under close observation")
    st.caption(
        "Measured from the date the framework **flagged** each name, not from "
        "when you added it here. A watchlist that resets the baseline on add "
        "would flatter every late addition, and you would never notice.")

    w = pd.DataFrame(snap.watching)
    if w.empty:
        st.info("Nothing being watched yet. Add candidates from the Market or "
                "Swing tab, or run `scripts/run_daily.py --watch-add SYMBOL`.")
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
                             {k: v.get("median") for k, v in _h.items()}, _h),
            unsafe_allow_html=True)
        st.caption(
            "**T1:17** and **T2:37** are how many sessions each target normally "
            "takes — measured across 9,650 real paths, not forecast. Hover a column "
            "heading or a status for what it means. Calibration, not a rule: slow "
            "positions returned 7.4% over the next six months against 9.6% for "
            "fast ones, too small a gap to trade on.")
        with st.expander("What each status means"):
            st.markdown(c.watching_legend(_h), unsafe_allow_html=True)

        if "outcome" in w:
            hit = lambda col: int((w[col].fillna("") != "").sum())
            out = w[w["stopped_on"].fillna("") != ""]
            at_entry = int((out["t1_hit_on"].fillna("") != "").sum())
            st.caption(
                f"**Outcomes so far:** {hit('t1_hit_on')} reached T1, "
                f"{hit('t2_hit_on')} reached T2, {len(out)} stopped out"
                + (f" ({at_entry} at the flagged price after T1)" if at_entry else "")
                + f", {int((w['outcome'] == 'open').sum())} still open. Checked "
                "against each day's high and low since the flag, after every price "
                "refresh. The stop moves up to the flagged price once T1 is "
                "reached; a day that touched both the stop and a target counts "
                "as stopped.")

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
            from book import watching as wg
            ok, msg = wg.add(add_sym.strip().upper(), source="manual")
            (st.success if ok else st.info)(msg)
            state.clear()
    with right:
        if not w.empty:
            drop = st.selectbox("Stop watching", [""] + list(w["symbol"]),
                                key="watch_drop")
            if st.button("Remove") and drop:
                from book import watching as wg
                st.info(wg.remove(drop)[1])
                state.clear()
