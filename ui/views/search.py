"""Search: one box for stocks and funds. A stock opens Analyze a stock, a fund
opens Compare funds. The matching is the two existing lookups, unchanged:
analysis/lookup.py for NSE listings, mf/lookup.py for AMFI schemes.
"""
from __future__ import annotations

import streamlit as st

from ui import components as c
from ui import state


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def _schemes():
    from mf import navdb
    con = navdb.connect()
    sch = navdb.schemes(con)
    return sch, (sch["last_month"].max() if not sch.empty else None)


def _stocks(q: str) -> list:
    from analysis import lookup as lk
    from ui import analyze_tab
    try:
        d = analyze_tab._directory()
        res = lk.resolve(q, d)
    except Exception:          # noqa: BLE001 -- the directory needs NSE once a day
        return []
    found = [res.match] if res.match else list(res.candidates)
    # A search box, unlike Analyze, should also offer every listed company
    # whose name carries the words typed: "hdfc" is a symbol (the old,
    # merged HDFC Ltd) but the user most likely wants HDFC Bank or HDFC Life.
    words = lk.normalise(q).split()
    if words and not d.empty:
        names = d["name"].fillna("").map(lk.normalise)
        hit = d[d["listed"] & names.map(lambda n: all(w in n.split() for w in words))]
        seen = {s.symbol for s in found}
        found += [lk._listing(r) for _, r in hit.iterrows() if r["symbol"] not in seen]
    return found[:8]


def _funds(q: str) -> list[tuple[int, str]]:
    from mf import lookup
    sch, newest = _schemes()
    if sch.empty:
        return []
    m = lookup.find(q, sch, newest)
    if m.resolved:
        return [(m.code, m.name)]
    return [(code, name) for code, name, score in m.candidates if score >= 0.4][:6]


def render(pages: dict) -> None:
    state.page_title("Search", "Stocks open Analyze a stock · funds open Compare funds")
    st.session_state.setdefault("search_page_q", st.session_state.get("search_q", ""))
    q = st.text_input("Search a stock or fund", placeholder="Ticker, company, ISIN or "
                      "fund name", key="search_page_q")
    st.session_state["search_q"] = q
    if not q.strip():
        st.caption("Try “hdfc”, “INFY”, “INE009A01021” or “parag parikh flexi cap”.")
        return

    left, right = st.columns(2)
    with left:
        st.markdown("#### Stocks")
        stocks = _stocks(q)
        if not stocks:
            st.caption("No NSE stock matches.")
        for i, s in enumerate(stocks):
            cols = st.columns([5, 1.6], vertical_alignment="center")
            cols[0].markdown(f"**{s.symbol}** " + c.muted(f"· {s.name or 'name not on file'}"))
            if cols[1].button("Analyze", key=f"srch_s_{i}", width="stretch"):
                st.session_state["an_query"] = s.symbol
                st.session_state["an_run"] = {"query": s.symbol, "mode": "Both",
                                              "refresh": False}
                st.session_state.pop("an_pick", None)
                st.switch_page(pages["analyze"])
    with right:
        st.markdown("#### Mutual funds")
        funds = _funds(q)
        if not funds:
            st.caption("No fund matches. Old names work too: “Axis Bluechip” finds "
                       "Axis Large Cap.")
        for i, (code, name) in enumerate(funds):
            cols = st.columns([5, 1.6], vertical_alignment="center")
            cols[0].markdown(f"**{name}** " + c.muted(f"· {code}"))
            if cols[1].button("Compare", key=f"srch_f_{i}", width="stretch"):
                st.session_state["mf_compare_pref"] = str(code)
                st.switch_page(pages["compare"])
