"""Research › Research assistant: the pack the assistant reads, how to ask,
and every saved answer with its check.

The assistant is Claude Code (the /research skill) reading
data/research/pack.json: no API key, nothing sent anywhere by this app.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from book import research_pack as rp
from strategy import answer_check as ac


def render() -> None:
    st.markdown("## Research assistant")
    st.caption("Ask in Claude Code with /research. It answers only from the research pack "
               "below: every figure there comes from the scorecard, valuation, risk, plan "
               "and tax engines, and every figure in an answer must cite it. The checker "
               "rejects invented or mismatched figures, stale data passed off as current, "
               "missing governance flags, certainty about returns, and buy/sell orders.")
    pack = _pack_section()
    st.divider()
    _ask()
    st.divider()
    _answers(pack)


def _pack_section() -> dict | None:
    st.markdown("**Research pack**")
    pack = rp.load()
    c = st.columns([3, 1, 1])
    if pack:
        c[0].caption(f"Built {pack['generated_at'].replace('T', ' ')}; prices as of "
                     f"{pack['as_of']['prices']}; {len(pack['holdings'])} holdings "
                     "(operating equity); plan with "
                     f"{'₹{:,.0f} a month'.format(pack['plan']['monthly']) if pack['plan']['monthly'] else 'no new money assumed'}. "
                     "data/research/pack.json stays on your computer.")
    else:
        c[0].caption("No pack yet.")
    monthly = c[1].number_input("New money a month, ₹ (optional)", min_value=0, value=0,
                                step=5000, key="rp_monthly", label_visibility="collapsed",
                                help="Only if you want the plan in the pack to assume it")
    if c[2].button("Rebuild pack", width="stretch"):
        with st.spinner("Building the pack…"):
            pack = rp.write(rp.build(float(monthly))) and rp.load()
        st.success("Pack rebuilt. Answers from the old pack are marked as such.")
    return pack


def _ask() -> None:
    st.markdown("**How to ask**")
    st.caption("In Claude Code in this project, type /research followed by a question. "
               "These are the questions the pack is laid out for:")
    pack = rp.load() or {"questions": rp.QUESTIONS}
    st.code("\n".join(f"/research {q.replace('this stock', 'TCS')}"
                      for q in pack["questions"]), language="text")


def _answers(pack: dict | None) -> None:
    st.markdown("**Saved answers**")
    saved = rp.answers()
    if not saved:
        st.caption("None yet. Answers that pass the check are filed here by "
                   "`scripts/research.py check ANSWER.json --save`.")
        return
    labels = [f"{'✓' if (a.get('check') or {}).get('ok') else '✗'}  "
              f"{f.name[:15].replace('-', ' ', 1)}  {a.get('question', f.name)}"
              for f, a in saved]
    i = st.selectbox("Answer", range(len(saved)), format_func=lambda k: labels[k],
                     key="rp_answer")
    f, a = saved[i]
    if "_error" in a:
        st.error(f"{f.name}: {a['_error']}")
        return
    now = ac.check({k: v for k, v in a.items() if k != "check"}, pack) if pack else None
    st.markdown(f"#### {a['question']}")
    if a["status"] == "INSUFFICIENT DATA":
        st.warning("INSUFFICIENT DATA: " + "; ".join(a.get("missing", [])))
    st.markdown(a["summary"])
    if now is not None:
        if now["ok"]:
            st.success(f"Check passes against the current pack ({now['checked']} evidence "
                       "items verified).")
        elif pack and a.get("pack_generated_at") != pack.get("generated_at"):
            st.info(f"Answered from the pack of {a['pack_generated_at'].replace('T', ' ')}; "
                    "the pack has been rebuilt since, so figures may have moved. Ask again "
                    "for a current answer.")
        else:
            st.error("Check fails: " + "; ".join(now["errors"]))
        for w in now["warnings"]:
            st.caption("⚠ " + w)
    rows = []
    for n, fd in enumerate(a["findings"], 1):
        ev = []
        for e in fd["evidence"]:
            if "path" in e:
                ev.append(f"{e['path']} = {e['value']}")
            else:
                ev.append(f"{e['derived']['op']}({', '.join(e['derived']['of'])}) = {e['value']}")
        rows.append({"#": n, "Claim": fd["claim"], "Kind": fd["kind"],
                     "Evidence (pack path = value)": "\n".join(ev) or "—"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if a.get("governance_flags"):
        st.warning("Governance flags: " + "; ".join(f"{g['symbol']}: {g['flag']}"
                                                    for g in a["governance_flags"]))
    if a.get("stale_data_used"):
        st.caption("Stale figures used: " + ", ".join(a["stale_data_used"]))
    for c in a.get("caveats", []):
        st.caption("• " + c)
    st.caption(f"Not advice. File: data/research/answers/{f.name}")
