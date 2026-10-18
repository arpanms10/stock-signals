"""Data and jobs: every refresh and backtest, how old its data is, and the
button that runs it. Replaces the column of buttons that used to sit in the
sidebar of every page.

Jobs still run the way they always have: in this tab, with live output,
and the page reloads when one finishes.
"""
from __future__ import annotations

import datetime as dt

import streamlit as st

from ui import components as c
from ui import service, state

TONE = {"fresh": "green", "ageing": "orange", "stale": "red"}


def _updated(label: str, kind: str | None, fresh: dict) -> tuple[str, str]:
    """(text, colour) for the Updated column."""
    if kind and kind in fresh:
        f = fresh[kind]
        when = f"{f['latest']:%d %b %Y}" if f.get("latest") else "never"
        return (f"{when} · {f['label']}", TONE.get(f["label"], "gray"))
    ran = st.session_state.get("last_run", {}).get(label)
    if ran:
        return (f"ran {ran:%d %b %H:%M}", "green")
    p = service.JOB_OUTPUTS.get(label)
    if p is not None and p.exists():
        return (f"{dt.datetime.fromtimestamp(p.stat().st_mtime):%d %b %Y}", "gray")
    return ("—", "gray")


def run_queue(labels: list[str], where) -> None:
    """Run several jobs one after another, stopping at the first failure."""
    for label in labels:
        where.markdown(f"**{label}**")
        box = where.empty()
        lines: list[str] = []
        for line in service.run_script(service.SCRIPTS[label]):
            lines.append(line)
            box.code("\n".join(lines[-14:]), language=None)
        code = service.exit_code(lines)
        if code != 0:
            where.error(f"{label} failed (exit code {code}); the jobs after it were "
                        "not run.")
            return
        st.session_state.setdefault("last_run", {})[label] = dt.datetime.now()
    state.clear()
    st.session_state["refreshed"] = ", ".join(labels)
    st.rerun()


def render() -> None:
    fresh = {f["kind"]: f for f in state.freshness()}
    stale = [service.FIXES[k][1] for k, f in fresh.items()
             if f["label"] == "stale" and k in service.FIXES]
    top = st.columns([6, 2], vertical_alignment="bottom")
    with top[0]:
        state.page_title("Data and jobs", "Weekday job 19:30 · weekly job Saturday · "
                                          "keep this tab open while a job runs")
    out = st.container()
    if top[1].button("Refresh everything stale", type="primary", width="stretch",
                     disabled=not stale, key="jobs_stale",
                     help="Runs, in order: " + ", ".join(stale) if stale
                     else "Nothing is stale."):
        run_queue(stale, out)
    state.refreshed_notice()
    if job := st.session_state.pop("run_job", None):
        state.run_and_reload(job, service.SCRIPTS[job], out)

    for group, jobs in service.JOB_GROUPS:
        with st.container(border=True):
            st.markdown(f"**{group}**")
            head = st.columns([3, 3, 2.2, 1.8, 1.2])
            for col, h in zip(head, ("Data", "Used by", "Updated", "Takes", "")):
                col.caption(h)
            for label, used_by, kind, takes in jobs:
                cols = st.columns([3, 3, 2.2, 1.8, 1.2], vertical_alignment="center")
                cols[0].markdown(label)
                cols[1].markdown(c.muted(used_by))
                text, colour = _updated(label, kind, fresh)
                cols[2].markdown(f":{colour}[{text}]")
                cols[3].markdown(c.muted(takes))
                verb = "Rerun" if label.startswith("Rerun") else "Refresh"
                if cols[4].button(verb, key=f"job_{label}", width="stretch"):
                    state.run_and_reload(label, service.SCRIPTS[label], out)
    st.caption("The data chip at the top right shows the same freshness on every "
               "page. Suggest buckets lives on the Stocks page.")
