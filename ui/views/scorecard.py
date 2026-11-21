"""Portfolio › Scorecard: the 100-point fundamental scorecard for each holding.

Every company share you hold, scored from its filed record (Holding data)
and your research inputs, with each point explained: the value, the rule,
the source. Two axes: the company's fundamental quality tier, and the role
it suggests in your book. Descriptive: not backtested, ranks nothing.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from book import research_db as rdb
from book import scorecards as bs
from book import userfiles as uf
from strategy import scorecard as sc

ROLE = {"core": "Core", "satellite": "Satellite", "review": "Review", "n/a": "—"}
STATUS = {sc.SCORED: "", sc.NOT_ASSESSED: "not assessed", sc.NA: "n/a",
          sc.PENDING: "pending"}


@st.cache_data(ttl=300, show_spinner=False)
def _cards() -> tuple[list[dict], list[str]]:
    cards, notes = bs.build(save=False)
    return [bs.card_dict(c) for c in cards], notes



def _v(x) -> str:
    if x is None:
        return "—"
    if x in (float("inf"), float("-inf")):
        return "∞" if x > 0 else "−∞"
    return f"{x:,.2f}" if abs(x) < 1000 else f"{x:,.0f}"


def render() -> None:
    st.markdown("## Scorecard")
    st.caption("A 100-point summary of each company's filed record against stated "
               "conventions (config/scorecard.yaml). **Descriptive, not backtested**: it "
               "explains, it does not rank or predict. Valuation's 15 points come from "
               "Portfolio › Valuation.")
    cards, notes = _cards()
    if not cards:
        st.info("No company shares in your holdings file, or no holding data yet "
                "(Portfolio › Holding data › Refresh data).")
        return

    a, b, c = st.columns([2, 2, 3])
    if a.button("Save today's scorecards", width="stretch",
                help="Stores a dated copy of every card in research.db, so later "
                     "reviews can see what changed."):
        objs, _ = bs.build(save=True)
        st.success(f"Saved {len(objs)} scorecards.")
    if b.button("Create / update fill-in files", width="stretch",
                help="Adds a row per holding to config/research_inputs.xlsx and per "
                     "sector to config/sector_outlook.xlsx. Existing entries are kept."):
        objs, _ = bs.build(save=False)
        for line in bs.fill_in_files(objs) or ["Both files already list every holding."]:
            st.success(line)
        _cards.clear()
    have = [s.stem for s in (uf.RESEARCH, uf.SECTOR_OUTLOOK) if uf.find(s.stem)]
    c.caption("Judgment items (competitive advantage 8, governance 6, industry 10 "
              "points) come from " + (" and ".join(f"config/{h}" for h in have)
                                      if have else "two fill-in files, not created yet")
              + ". Points without evidence are ignored.")
    for n in notes:
        st.caption(n)

    _summary(cards)
    st.divider()
    names = [c["symbol"] for c in cards]
    pick = st.selectbox("Company", names, key="sc_pick")
    _detail(next(c for c in cards if c["symbol"] == pick))


def _summary(cards: list[dict]) -> None:
    rows = []
    # Alphabetical, not by score: the score is descriptive and unvalidated, so
    # the page does not rank holdings by it.
    for c in sorted(cards, key=lambda c: c["symbol"]):
        rows.append({
            "Holding": c["symbol"], "Model": c["model"],
            "Score": c["score"],
            "Points": f"{c['earned']:.1f} / {c['available']:.0f}",
            "Assessed %": c["coverage_pct"],
            "Quality tier": c["tier_label"],
            "Flags": "; ".join(f["text"] for f in c["flags"]),
            "Suggested role": ROLE.get(c["role"], c["role"]),
            "Category": c["satellite_category"].replace("_", " "),
            "Your bucket": c["bucket_note"]})
    st.markdown("**Your holdings**")
    st.caption("Score = points earned / points assessed × 100. Assessed % = share of the "
               "points that apply to the business that have data or your evidence; "
               "below 60% no tier is given. Not-assessed points are never counted as zero.")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={
        "Score": st.column_config.NumberColumn(format="%.0f"),
        "Assessed %": st.column_config.NumberColumn(format="%.0f%%")})


def _detail(c: dict) -> None:
    st.markdown(f"### {c['symbol']} · {c['model']} model")
    m = st.columns([1, 1, 2, 1])
    m[0].metric("Score", "—" if c["score"] is None else f"{c['score']:.0f}",
                help="Points earned / points assessed × 100")
    m[1].metric("Assessed", f"{c['coverage_pct']:.0f}%",
                help=f"{c['available']:.0f} of {c['applicable']:.0f} applicable points")
    m[2].metric("Quality tier", c["tier_label"].split(" (")[0])
    m[3].metric("Suggested role", ROLE.get(c["role"], c["role"]))
    if c["raw_tier"] and c["raw_tier"] != c["tier"]:
        st.warning(f"Governance override: {c['tier_label']}.")
    for f in c["flags"]:
        (st.error if f["severity"] == sc.SEVERE else st.warning)(
            f"{f['severity'].title()} flag: {f['text']} ({f['source']})")
    st.caption("Role: " + "; ".join(c["role_reasons"])
               + (f" · {c['bucket_note']}" if c["bucket_note"] else ""))

    st.markdown("**Components**")
    st.dataframe(pd.DataFrame([{
        "Component": comp["label"], "Weight": comp["weight"],
        "Earned": round(comp["earned"], 1) if comp["available"] else None,
        "Assessed": comp["available"],
        "Note": comp["note"] or ("" if comp["available"] else
                                 STATUS.get(comp["criteria"][0]["status"], ""))}
        for comp in c["components"]]), hide_index=True, width="stretch")

    st.markdown("**Every criterion**")
    st.dataframe(pd.DataFrame([{
        "Component": comp["label"], "Criterion": cr["label"], "Value": _v(cr["value"]),
        "Points": "" if cr["points"] is None else cr["points"],
        "Max": cr["max_points"], "Status": STATUS.get(cr["status"], cr["status"]),
        "Rule": cr["rule"], "Source": cr["source"], "Detail": cr["note"]}
        for comp in c["components"] for cr in comp["criteria"]]),
        hide_index=True, width="stretch")
    if c["manual_todo"]:
        with st.expander("Evidence worth collecting for this sector"):
            st.markdown("\n".join(f"- {x}" for x in c["manual_todo"]))
            st.caption("Not in any free structured source. Summarise what you find in "
                       "moat_evidence / governance_evidence in the research inputs file.")
    con = rdb.connect()
    try:
        hist = rdb.scorecard_history(con, c["symbol"])
    finally:
        con.close()
    if hist:
        with st.expander(f"Saved scorecards ({len(hist)})"):
            st.dataframe(pd.DataFrame([{
                "As of": h["as_of"], "Score": h["score"], "Assessed %": h["coverage_pct"],
                "Tier": h["tier"] or "—", "Role": h["role"], "Config": h["config_hash"]}
                for h in hist]), hide_index=True, width="stretch")
