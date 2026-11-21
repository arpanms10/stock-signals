"""Portfolio › Reviews: monitoring events and what changed since the last review.

Events come from NSE announcements, shareholding filings and results
(strategy/monitor.py). Each is a prompt to re-read the thesis, never a trade
signal. Marking one reviewed stores your note in research.db.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from book import monitoring as bm
from strategy import monitor as mon


def render() -> None:
    st.markdown("## Reviews")
    st.caption("Thesis checks are events that can break the reason you hold a company: an "
               "auditor resigning, a pledge, a sharp fall in profit, a top-management exit. "
               "Watch items are worth knowing. Neither is a buy or sell signal. Rules and "
               "thresholds: config/monitoring.yaml.")
    cfg = mon.load_config()
    con = bm.connect()
    try:
        _run_bar(con)
        st.divider()
        _events(con, cfg)
        st.divider()
        _changes(con, cfg)
        st.divider()
        _reports()
    finally:
        con.close()


def _run_bar(con) -> None:
    dates = bm.snapshots(con)
    last = con.execute("SELECT MAX(at) FROM announcement_fetch").fetchone()[0]
    c = st.columns([3, 1, 1])
    c[0].caption(f"Last review {dates[-1] if dates else 'never'}; announcements fetched "
                 f"{(last or 'never').replace('T', ' ')}. Run scripts/refresh_holding_data.py "
                 "first so shareholding and results are current.")
    offline = c[1].toggle("Offline", help="Skip fetching NSE announcements")
    if c[2].button("Run review", width="stretch", type="primary"):
        with st.spinner("Fetching announcements, scoring and comparing…"):
            r = bm.run(fetch=not offline)
        st.success(f"Review {r['as_of']}: {r['new_announcements']} new announcements, "
                   f"{r['new_events']} new events, {len(r['changes'])} changes. "
                   f"Reports: data/reviews/{r['report'].name} and "
                   f"{r['holdings_report'].name}")
        for p in r["problems"]:
            st.caption("⚠ " + p)


def _events(con, cfg) -> None:
    st.markdown("**Events**")
    since = (dt.date.today() - dt.timedelta(days=cfg["lookback_days"])).isoformat()
    f = st.columns([2, 2, 3])
    sev = f[0].multiselect("Severity", list(mon.SEVERITIES),
                           default=[mon.THESIS, mon.WATCH],
                           format_func=lambda s: mon.LABEL[s], key="rv_sev")
    status = f[1].radio("Status", ["open", "reviewed", "all"], horizontal=True,
                        key="rv_status")
    rows = bm.events(con, status=None if status == "all" else status,
                     severities=tuple(sev) or mon.SEVERITIES, since=since)
    syms = sorted({r["symbol"] for r in rows})
    pick = f[2].multiselect("Holdings", syms, key="rv_syms")
    if pick:
        rows = [r for r in rows if r["symbol"] in pick]
    rows.sort(key=lambda r: (mon.SEVERITIES.index(r["severity"]), r["status"] != "open",
                             -dt.date.fromisoformat(r["date"]).toordinal()))
    n = mon.summarise([mon.Event(**{k: r[k] for k in ("key", "symbol", "date", "kind",
                                                       "severity", "title", "detail",
                                                       "source", "link")}) for r in rows])
    m = st.columns(3)
    for col, s in zip(m, mon.SEVERITIES):
        col.metric(mon.LABEL[s], n.get(s, 0))
    if not rows:
        st.caption(f"No events in the last {cfg['lookback_days']} days with these filters.")
        return
    df = pd.DataFrame([{"Date": r["date"], "Holding": r["symbol"],
                        "Severity": mon.LABEL[r["severity"]], "Event": r["title"],
                        "Detail": r["detail"], "Source": r["link"] or None,
                        "Status": r["status"], "Your note": r["note"]} for r in rows])
    st.dataframe(df, hide_index=True, width="stretch",
                 column_config={"Source": st.column_config.LinkColumn("Source",
                                                                      display_text="filing"),
                                "Detail": st.column_config.TextColumn(width="large")})
    with st.expander("Mark an event reviewed"):
        labels = {r["key"]: f"{r['date']}  {r['symbol']}  {r['title']}" for r in rows}
        key = st.selectbox("Event", list(labels), format_func=labels.get, key="rv_key")
        cur = next(r for r in rows if r["key"] == key)
        note = st.text_area("What you read and decided", value=cur["note"], key="rv_note",
                            placeholder="e.g. Read the filing: one-off tax charge; thesis intact.")
        b = st.columns(2)
        if b[0].button("Mark reviewed", disabled=cur["status"] == "reviewed"):
            bm.set_status(con, key, "reviewed", note)
            st.rerun()
        if b[1].button("Re-open", disabled=cur["status"] == "open"):
            bm.set_status(con, key, "open", note)
            st.rerun()


def _changes(con, cfg) -> None:
    st.markdown("**What changed between reviews**")
    dates = bm.snapshots(con)
    if len(dates) < 2:
        st.caption("Needs two reviews. " + (f"The first was saved on {dates[0]}; the next "
                                             "one will list changes in scores, tiers, roles, "
                                             "weights, flags, shareholding and plan actions."
                                             if dates else "None saved yet."))
        return
    c = st.columns(2)
    b = c[1].selectbox("Review", dates[::-1], key="rv_b")
    older = [d for d in dates if d < b]
    if not older:
        st.caption("No earlier review to compare with.")
        return
    a = c[0].selectbox("Compared with", older[::-1], key="rv_a")
    ch = mon.diff(bm.load_snapshot(con, a), bm.load_snapshot(con, b), cfg)
    if not ch:
        st.caption("Nothing above the thresholds in config/monitoring.yaml.")
        return
    st.dataframe(pd.DataFrame([{"Holding": x["symbol"], "What": x["what"],
                                "Before": "—" if x["before"] is None else str(x["before"]),
                                "After": "—" if x["after"] is None else str(x["after"]),
                                "Note": x["note"]} for x in ch]),
                 hide_index=True, width="stretch")


def _reports() -> None:
    st.markdown("**Reports**")
    st.caption("Holdings report: every current company holding (position, scorecard, "
               "valuation, latest quarter, shareholding, risk, plan, unrealised gains, open "
               "checks) plus the book and this year's tax, rendered from the research pack "
               "with no model in the loop. Review report: events and what changed. Both are "
               "written by Run review; the button here rebuilds only the holdings report.")
    if st.button("Build holdings report now"):
        with st.spinner("Rebuilding the research pack and the report…"):
            p = bm.holdings_report()
        st.success(f"Wrote data/reviews/{p.name}")
    files = sorted(bm.REPORTS.glob("*.md"), key=lambda p: (p.stem.split("-", 1)[1], p.stem),
                   reverse=True) if bm.REPORTS.exists() else []
    if not files:
        st.caption("None yet.")
        return
    f = st.selectbox("Report", files, key="rv_report",
                     format_func=lambda p: ("Holdings " if p.stem.startswith("holdings")
                                           else "Review ") + p.stem.split("-", 1)[1])
    text = f.read_text()
    st.download_button("Download", text, file_name=f.name, mime="text/markdown")
    with st.expander("Show report"):
        st.markdown(text)
