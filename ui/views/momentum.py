"""Ideas › Momentum: every stock in the top band on either lookback.

The old Momentum tab, moved, with the ranking settings beside it.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from ui import components as c
from ui import state


def render() -> None:
    snap = state.snapshot()
    state.page_title("Momentum", f"Data through {snap.as_of} · market {snap.regime}")
    c.evidence("tested", "Beat random picks from the same universe by about 3 standard "
                         "deviations in the backtest, but the index by only about 2 points "
                         "a year, with deeper falls. Details below.")
    with st.expander("Ranking settings"):
        state.ranking_settings()
    lbs = getattr(snap, "lookbacks", None) or []
    t = state.with_results(state.with_quality_note(
        pd.DataFrame(getattr(snap, "momentum_table", None) or [])))
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
                  "quality", "quality_note", "results", "sector", "held"]]
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
                "quality_note": state.QUALITY_NOTE,
                "results": state.RESULTS_COL,
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
