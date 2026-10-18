"""The strip under the top navigation bar, on every page: what this is, the
search box, and the data chip.

The chip is the data's age at a glance. It turns amber or red when a source
is behind and opens to say which, with the button that fixes it; anything
stale also gets a warning line under the strip, because a page full of
week-old prices looks exactly like a current one.
"""
from __future__ import annotations

import streamlit as st

from ui import service, state

LEVEL = {"fresh": 0, "ageing": 1, "stale": 2}
CHIP = {0: ("Data OK", ":material/check_circle:"),
        1: ("Data ageing", ":material/schedule:"),
        2: ("{n} stale", ":material/warning:")}


def _go_search() -> None:
    q = st.session_state.get("hdr_search", "").strip()
    if q:
        st.session_state["search_q"] = q
        st.session_state["search_page_q"] = q
        st.session_state["hdr_search"] = ""
        st.session_state["go_search"] = True


def header(pages: dict) -> None:
    fresh = state.freshness()
    worst = max((LEVEL.get(f["label"], 0) for f in fresh), default=0)
    n_stale = sum(1 for f in fresh if f["label"] == "stale")

    note, search, chip = st.columns([5, 3.2, 1.6], vertical_alignment="center")
    note.caption("Decision support only, not advice. Nothing here places an order.")
    search.text_input("Search a stock or fund", placeholder="Search a stock or fund",
                      key="hdr_search", on_change=_go_search,
                      label_visibility="collapsed")
    label, icon = CHIP[worst]
    with chip.popover(label.format(n=n_stale), icon=icon, width="stretch"):
        st.markdown("**Data**")
        for f in fresh:
            colour = {"fresh": "green", "ageing": "orange", "stale": "red"}.get(
                f["label"], "gray")
            st.markdown(f":{colour}[**{f['label']}**] · {f['message']}")
            fix = service.FIXES.get(f["kind"])
            if f["label"] != "fresh" and fix and st.button(
                    fix[1], key=f"chip_fix_{f['kind']}", width="stretch"):
                st.session_state["run_job"] = fix[1]
                st.switch_page(pages["data"])
        st.page_link(pages["data"], label="All data and jobs", icon=":material/database:")

    if st.session_state.pop("go_search", False):
        st.switch_page(pages["search"])
    for f in fresh:
        if f["label"] == "stale":
            st.warning(f"⚠️ {f['message']} Use the data chip at the top right, or "
                       "Data and jobs, to refresh.")
