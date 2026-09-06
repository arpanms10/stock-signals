"""Stock signals dashboard.

Two sections:
  1. MY HOLDINGS -- what you own and what to do about it
  2. MARKET      -- the ranked universe and where entries would trigger

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


# ------------------------------------------------------------------ sidebar
st.sidebar.title("Stock Signals")
size = st.sidebar.slider("Universe size (by liquidity)", 50, 500, 200, 50,
                         help="How many of the most liquid NSE stocks to rank. "
                              "NIFTY 200 is roughly the top 200.")
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
for w in snap.warnings:
    st.warning(w)

tab_hold, tab_market = st.tabs(["  My Holdings  ", "  Market  "])

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
                    grp[["symbol", "value", "pct_of_book", "pnl_pct",
                         "quality", "rank"]]
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
        order = {"EXIT": 0, "TRIM": 1, "ADD": 2, "HOLD": 3}
        show = st.multiselect("Show", ["EXIT", "TRIM", "ADD", "HOLD"],
                              default=["EXIT", "TRIM", "ADD"])
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
            qt = "" if r["quality"] is None else \
                c.pill(f"quality {r['quality']:.0f}", c.quality_tone(r["quality"]))
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
    m = pd.DataFrame(snap.market)
    if m.empty:
        st.info("No ranked universe. Run the market data refresh first.")
    else:
        cols = st.columns(4)
        cols[0].metric("Ranked", f"{len(m)}",
                       help="Names passing the 200-DMA filter out of the "
                            "liquidity pool")
        cols[1].metric("Market regime", snap.regime)
        cols[2].metric("You hold", f"{int(m['held'].sum())}")
        cols[3].metric("In the top band", f"{int(m['in_top'].sum())}")

        st.caption("Ranked by 12-1 momentum divided by volatility, filtered to "
                   "names above their 200-day average. Rank is a *state*, not a "
                   "trigger — the entry levels below say what would have to "
                   "happen.")

        f1, f2, f3 = st.columns([2, 2, 3])
        top_only = f1.checkbox("Top band only", value=False)
        held_only = f2.checkbox("Only what I hold", value=False)
        sect = f3.multiselect("Sector", sorted(m["sector"].unique()))

        view = m.copy()
        if top_only:
            view = view[view["in_top"]]
        if held_only:
            view = view[view["held"]]
        if sect:
            view = view[view["sector"].isin(sect)]

        st.dataframe(
            view[["rank", "symbol", "price", "momentum", "vol", "timing_score",
                  "quality", "sector", "held"]]
            .rename(columns={"rank": "#", "symbol": "Stock", "price": "Price",
                             "momentum": "12-1 mom %", "vol": "Vol %",
                             "timing_score": "Timing", "quality": "Quality",
                             "sector": "Sector", "held": "Held"}),
            hide_index=True, width='stretch', height=380,
            column_config={
                "Price": st.column_config.NumberColumn(format="₹%.2f"),
                "12-1 mom %": st.column_config.NumberColumn(format="%.1f%%"),
                "Vol %": st.column_config.NumberColumn(format="%.0f%%"),
                "Timing": st.column_config.ProgressColumn(
                    min_value=0, max_value=100, format="%d"),
                "Quality": st.column_config.ProgressColumn(
                    min_value=0, max_value=100, format="%d"),
                "Held": st.column_config.CheckboxColumn(),
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

        if row["flags"]:
            with st.expander("Fundamental flags"):
                for f_ in row["flags"]:
                    st.markdown(f"- {f_}")
