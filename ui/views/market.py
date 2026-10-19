"""Market › Overview: the ranked universe and where entries would trigger.

The old Market tab, moved. Universe size and Recompute sit here and on
Momentum, the two pages that show the ranking they change.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from ui import components as c
from ui import state


def render() -> None:
    snap = state.snapshot()
    state.page_title("Market", f"Data through {snap.as_of}")
    st.caption(snap.regime_detail)
    with st.expander("Ranking settings"):
        state.ranking_settings()
    m = state.with_quality_note(pd.DataFrame(snap.market))
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
                "quality_note": state.QUALITY_NOTE,
                "results": state.RESULTS_COL,
                "Held": st.column_config.CheckboxColumn(),
                alt_label: st.column_config.NumberColumn(
                    format="%d",
                    help=f"Rank on the comparison lookback ({lbs[-1]}-1 "
                         f"momentum). # is the main {main_lb}-1 rank"),
                "Both": st.column_config.CheckboxColumn(
                    help="In the top band on both lookbacks"),
            })

        st.divider()
        entry_levels(view["symbol"].tolist(), snap.market)


@st.fragment
def entry_levels(symbols: list[str], market: list[dict]) -> None:
    """The Market tab's entry-levels panel.

    A fragment, so picking another stock reruns only this panel: the rest
    of the page stays live instead of fading behind the rerun."""
    st.subheader("Entry levels")
    st.caption("The price at which each rule would fire, with the stop and "
               "targets that would apply there. **Not** a recommended price "
               "— reaching a level is necessary, not sufficient.")
    pick = st.selectbox("Stock", symbols)
    row = next(r for r in market if r["symbol"] == pick)

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

    from book import watching as wg
    already = set(wg.symbols())
    if row["symbol"] in already:
        st.caption(f"✓ {row['symbol']} is on your watch list.")
    elif st.button(f"Watch {row['symbol']}", key=f"watch_{row['symbol']}"):
        ok, msg = wg.add(row["symbol"], source="rank")
        (st.success if ok else st.info)(msg)
        state.clear()

    if row["flags"]:
        with st.expander("Fundamental flags"):
            for f_ in row["flags"]:
                st.markdown(f"- {f_}")
