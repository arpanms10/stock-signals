"""Mutual Funds tab: your MF book's verdicts, and any fund by name.

Reads config/mf_holdings.csv or .xlsx (percent of the MF book, no amounts) and the
NAV history in data/mf/mf.db (run_mf.py --refresh). Funds typed in the box
are judged alongside, as not held. Same engine as run_mf.py.
"""
from __future__ import annotations

import streamlit as st

from mf import engine
from ui import components as c

TONE = {"EXIT": "bad", "AVOID": "bad", "TRIM": "warn", "WATCH": "warn",
        "ADD": "good", "HOLD": "neutral", "NEUTRAL": "neutral", "NOT JUDGED": "neutral"}
STATE_TONE = {"STRONG": "good", "OK": "neutral", "SLIPPING": "warn", "WEAK": "bad",
              "YOUNG": "info", "-": "neutral"}


@st.cache_data(ttl=3600, show_spinner="Ranking funds against their categories...")
def _evaluate(queries: tuple[str, ...]) -> engine.Evaluation:
    return engine.evaluate(list(queries))


def _calibration_line() -> str | None:
    from mf.backtest import calibration_line
    return calibration_line()


def _cat(k: str | None) -> str:
    # "equity:small cap" would be read by Streamlit's markdown as a :small[]
    # text directive and lose a word.
    return k.replace(":", " / ") if k else "?"


def render() -> None:
    st.caption("Each fund against its SEBI category on month-end Direct Growth NAVs. "
               "Your book comes from config/mf_holdings.csv or .xlsx (% of the MF portfolio). "
               "Decision support, not advice.")
    q = st.text_input("Evaluate funds (comma-separated names or AMFI codes)",
                      placeholder="parag parikh flexi cap, hdfc flexi cap")
    queries = tuple(x.strip() for x in q.split(",") if x.strip())
    try:
        ev = _evaluate(queries)
    except SystemExit as e:
        st.info(str(e))
        return
    line = _calibration_line()
    if line:
        st.caption(line)
    else:
        st.warning("No backtest yet -- run `run_mf.py --backtest` to see what these "
                   "states were worth historically.")
    for query, cands in ev.unresolved:
        opts = "; ".join(f"{name} ({code})" for code, name, _ in cands[:4])
        st.warning(f"'{query}' matches more than one fund: {opts}. Use more of the name or the code.")
    if ev.book is not None:
        for w in ev.book.warnings:
            st.caption(f"Holdings: {w}")

    st.markdown(f"**As of {ev.as_of}**")
    for d in ev.decisions:
        held = f"{d.weight:.0f}% of book" if d.weight is not None else "not held"
        target = (f" → {d.target:.0f}%" if d.target is not None and d.weight is not None else "")
        head = (f"{c.pill(d.action, TONE.get(d.action, 'neutral'))} &nbsp; **{d.name}** &nbsp; "
                f"{c.pill(d.state, STATE_TONE.get(d.state, 'neutral'))} &nbsp; "
                f"<span style='color:#6b7280'>{_cat(d.category)} · {held}{target}</span>")
        st.markdown(head, unsafe_allow_html=True)
        bullets = [f"- {r}" for r in d.reasons] + [f"- ⚠️ {x}" for x in d.cautions]
        if d.alternatives:
            bullets.append(f"- Same category: {'; '.join(d.alternatives)}")
        st.markdown("\n".join(bullets))

    codes = [d.code for d in ev.decisions if d.code is not None]
    if codes:
        with st.expander("Compare side by side"):
            t = engine.compare_table(ev, codes)
            t.loc["Category"] = [_cat(k) for k in t.loc["Category"]]
            st.table(t.apply(lambda col: [_fmt(v, k) for k, v in col.items()]))


def _fmt(v, key: str) -> str:
    from run_mf import _fmt as fmt
    return fmt(v, key)
