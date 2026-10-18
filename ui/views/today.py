"""Today: everything that wants a decision, from every page, in one list.

Nothing here is computed afresh: the rows are the verdicts the Stocks,
Mutual funds and Watching views already show, gathered so most visits need
only this page. Each row opens the page it came from.
"""
from __future__ import annotations

import datetime as dt

import streamlit as st

from ui import components as c
from ui import state

ACT = {"EXIT", "TRIM", "STOP"}
RECENT_DAYS = 10


def _recent(iso, today: dt.date) -> bool:
    try:
        return (today - dt.date.fromisoformat(str(iso)[:10])).days <= RECENT_DAYS
    except ValueError:
        return False


def _stock_rows(snap) -> list[dict]:
    order = {"EXIT": 0, "TRIM": 1, "ADD": 2, "WATCH": 3}
    out = []
    for r in sorted(snap.holdings, key=lambda r: (order.get(r["action"], 9),
                                                  0 if r["urgency"] == "urgent" else 1,
                                                  -r["value"])):
        if r["action"] not in order:
            continue
        why = r["reasons"][0] if r["reasons"] else ""
        out.append({"action": r["action"], "urgent": r["urgency"] == "urgent",
                    "name": r["symbol"], "what": "stock", "why": why,
                    "page": "stocks", "view": "Holdings"})
    return out


def _fund_rows() -> list[dict]:
    from ui import mf_tab
    try:
        ev = mf_tab._evaluate((), True)
    except SystemExit:
        return []
    out = []
    for d in ev.decisions:
        if d.weight is None or d.action not in ("EXIT", "TRIM", "ADD", "WATCH"):
            continue
        size = (f"{d.weight:.0f}% → {d.target:.0f}% of book" if d.target is not None
                else f"{d.weight:.0f}% of book")
        why = d.reasons[-1] if d.action in ("TRIM", "ADD") and d.reasons else \
            (d.reasons[0] if d.reasons else "")
        out.append({"action": d.action, "urgent": False, "name": d.name, "what": "fund",
                    "why": f"{size} · {why}", "page": "funds"})
    return out


def _watch_rows(snap, today: dt.date) -> list[dict]:
    out = []
    for w in snap.watching:
        if w.get("stopped_on") and _recent(w["stopped_on"], today):
            out.append({"action": "STOP", "urgent": False, "name": w["symbol"],
                        "what": f"watch list ({w.get('source') or 'manual'})",
                        "why": f"stop reached {str(w['stopped_on'])[:10]}",
                        "page": "stocks", "view": "Watching"})
        elif w.get("t1_hit_on") and _recent(w["t1_hit_on"], today) \
                and w.get("outcome") == "t1":
            out.append({"action": "HOLD", "urgent": False, "name": w["symbol"],
                        "what": f"watch list ({w.get('source') or 'manual'})",
                        "why": f"first target reached {str(w['t1_hit_on'])[:10]}; the stop "
                               "moves up to the flagged price",
                        "page": "stocks", "view": "Watching"})
    return out


def _results_due(today: dt.date) -> list[tuple[dt.date, str]]:
    from results import engine
    from ui import results_tab
    try:
        cov = results_tab._coverage()
        status = engine.status(cov, today)
        done = set(status.loc[status["current"], "symbol"])
        up = results_tab._upcoming(tuple(cov), today)
    except Exception:          # noqa: BLE001 -- the calendar is a nicety
        return []
    return sorted((d, s) for s, d in up.items()
                  if s not in done and 0 <= (d - today).days <= 7)


def _row(r: dict, pages: dict, i: int) -> None:
    cols = st.columns([0.9, 6, 1.6], vertical_alignment="center")
    cols[0].markdown(c.badge(r["action"], r["urgent"]), unsafe_allow_html=True)
    cols[1].markdown(f"**{r['name']}** " + c.muted(f"· {r['what']} · {r['why']}"))
    label = {"stocks": "Stocks", "funds": "Mutual funds"}[r["page"]]
    if cols[2].button(f"{label} ›", key=f"today_go_{i}", type="tertiary"):
        if r.get("view"):
            st.session_state["stocks_view_pref"] = r["view"]
        st.switch_page(pages[r["page"]])


def render(pages: dict) -> None:
    today = dt.date.today()
    snap = state.snapshot()
    state.page_title("Today", f"Prices to {snap.as_of}")
    for w in snap.warnings:
        st.warning(w)

    rows = _stock_rows(snap) + _fund_rows() + _watch_rows(snap, today)
    act = [r for r in rows if r["action"] in ACT]
    review = [r for r in rows if r["action"] not in ACT]
    due = _results_due(today)

    m = st.columns(4)
    m[0].metric("Act now", f"{len(act)}", help="Exits, trims and stops reached")
    m[1].metric("Review", f"{len(review)}",
                help="Adds with room, funds slipping, targets reached")
    m[2].metric("Results due this week", f"{len(due)}")
    m[3].metric("Market regime", snap.regime)
    st.caption(snap.regime_detail)

    i = 0
    for title, group, empty in (("Act now", act, "Nothing needs selling or trimming."),
                                ("Review", review, "Nothing to review.")):
        with st.container(border=True):
            st.markdown(f"#### {title}")
            if not group:
                st.caption(empty)
            for r in group:
                _row(r, pages, i)
                i += 1

    with st.container(border=True):
        cols = st.columns([6, 1.6], vertical_alignment="center")
        if due:
            cols[0].markdown("**Results this week** " + c.muted(
                "· " + " · ".join(f"{s} {d:%a %d %b}" for d, s in due[:6])
                + (f" · {len(due) - 6} more" if len(due) > 6 else "")))
        else:
            cols[0].markdown("**Results this week** "
                             + c.muted("· none announced for what you follow"))
        if cols[1].button("Quarterly results ›", key="today_results", type="tertiary"):
            st.switch_page(pages["results"])
