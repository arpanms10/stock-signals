"""Portfolio › Overview: what you own, in what proportions, against the plan.

Value and P/L, the sleeves (operating equity, REITs/InvITs as listed real
assets, ETFs, bonds), Core vs Satellite against 70:30, market-cap and sector
split, and each position against its size guideline. Every guideline crossed
is a review note, not an action. Snapshots save the book as valued today so
later reviews can compare.
"""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from book import book_view as bv
from book import marks as mk
from book import research_db as rdb
from book import securities as sec
from ui import components as c
from ui import state

HUE = "#2563eb"
GUIDE = "#6b7280"


@st.cache_data(ttl=600, show_spinner="Valuing the book…")
def _load():
    return bv.load()


def _f(v):
    return None if v is None else float(v)


def _bars(rows: list[dict], label: str, guide: float | None = None,
          guide_label: str = "") -> alt.Chart:
    d = pd.DataFrame([{"name": r.get("label", r["key"]), "pct": _f(r["pct"]) or 0.0,
                       "value": _f(r["value"]) or 0.0} for r in rows])
    base = alt.Chart(d).encode(
        y=alt.Y("name:N", sort="-x", title=None, axis=alt.Axis(labelLimit=220)),
        x=alt.X("pct:Q", title=f"% of {label}",
                scale=alt.Scale(domain=[0, max(100 if guide is None else
                                               max(d["pct"].max(), guide) * 1.15,
                                               d["pct"].max() * 1.1)])))
    bars = base.mark_bar(color=HUE, cornerRadiusEnd=4, height={"band": 0.7}).encode(
        tooltip=[alt.Tooltip("name:N", title=label.capitalize()),
                 alt.Tooltip("pct:Q", title="%", format=".1f"),
                 alt.Tooltip("value:Q", title="₹", format=",.0f")])
    text = base.mark_text(align="left", dx=4, color="#8b8b8b").encode(
        text=alt.Text("pct:Q", format=".1f"))
    layers = [bars, text]
    if guide is not None:
        layers.append(alt.Chart(pd.DataFrame({"g": [guide], "t": [guide_label]}))
                      .mark_rule(color=GUIDE, strokeDash=[4, 3])
                      .encode(x="g:Q", tooltip=alt.Tooltip("t:N", title="Guideline")))
    return alt.layer(*layers).properties(height=alt.Step(30))


def _core_sat(cs: dict) -> alt.Chart:
    d = pd.DataFrame([
        {"role": "Core", "actual": _f(cs["core_pct"]) or 0.0,
         "target": _f(cs["target_core_pct"])},
        {"role": "Satellite", "actual": _f(cs["satellite_pct"]) or 0.0,
         "target": _f(cs["target_satellite_pct"])}])
    base = alt.Chart(d).encode(y=alt.Y("role:N", title=None, sort=["Core", "Satellite"]))
    bars = base.mark_bar(color=HUE, cornerRadiusEnd=4, height={"band": 0.6}).encode(
        x=alt.X("actual:Q", title="% of operating equity", scale=alt.Scale(domain=[0, 100])),
        tooltip=[alt.Tooltip("role:N"), alt.Tooltip("actual:Q", title="Actual %",
                                                    format=".1f"),
                 alt.Tooltip("target:Q", title="Target %")])
    tick = base.mark_tick(color="#f59e0b", thickness=3, size=30).encode(
        x="target:Q", tooltip=[alt.Tooltip("target:Q", title="Target %")])
    text = base.mark_text(align="left", dx=4, color="#8b8b8b").encode(
        x="actual:Q", text=alt.Text("actual:Q", format=".1f"))
    return alt.layer(bars, tick, text).properties(height=alt.Step(40))


def _positions_table(o) -> pd.DataFrame:
    rows = []
    for p in sorted(o.positions, key=lambda p: -(p.market_value or 0)):
        rows.append({
            "Symbol": p.symbol, "Name": p.name, "Type": sec.KIND_LABEL[p.kind],
            "Role": p.role or "—",
            "Bucket set": "yes" if p.bucket else ("—" if p.sleeve != sec.OPERATING
                                                  else "no (satellite by default)"),
            "Sector": p.sector or ("—" if p.sleeve != sec.OPERATING else "Unclassified"),
            "Cap": p.cap_band or "—", "Qty": _f(p.quantity),
            "Avg cost": _f(p.avg_cost), "Price": _f(p.price),
            "Price date": p.price_date or "—", "Value": _f(p.market_value),
            "Weight %": _f(p.weight_pct), "Guideline %": _f(p.limit_pct),
            "Over guideline": "over" if p.over_limit else "",
            "P/L": _f(p.pnl), "P/L %": _f(p.pnl_pct),
            "Price source": p.price_source})
    return pd.DataFrame(rows)


def render() -> None:
    o, why = _load()
    top = st.columns([6, 1.5])
    with top[0]:
        state.page_title(
            "Overview",
            f"Valued at the {o.price_date} close (NSE bhavcopy)" if o else "")
    if top[1].button("Revalue", width="stretch", key="ov_reload",
                     help="Re-read the holdings file and prices on disk."):
        _load.clear()
        st.rerun()
    if o is None:
        for w in why:
            st.info(w)
        st.caption("Portfolio › Import holdings reads a broker download.")
        return

    cols = st.columns(5)
    cols[0].metric("Market value", c.money(_f(o.total_value)))
    cols[1].metric("Invested", c.money(_f(o.invested)),
                   help="Quantity × average cost, every holding with a cost.")
    cols[2].metric("Unrealised P/L", c.money(_f(o.unrealised)),
                   c.pct(_f(o.return_pct), signed=True),
                   help="On the holdings that have both a price and a cost.")
    cols[3].metric("Day change", c.money(_f(o.day_change)),
                   help=f"{o.price_date} close against the session before.")
    reit = next((s for s in o.sleeves if s["key"] == sec.REAL_ASSETS), None)
    cols[4].metric("REITs / InvITs", c.pct(_f(reit["pct"])) if reit else "0%",
                   help="Alternative income / listed real assets: a separate "
                        "sleeve, outside the 70:30 split.")

    if o.reviews:
        with st.container(border=True):
            st.markdown("**Review notes** — guidelines crossed. Each asks you to "
                        "look; none is an instruction to trade.")
            st.markdown("\n".join(f"- {r}" for r in o.reviews))

    cs = o.core_satellite
    left, right = st.columns(2)
    with left:
        st.markdown("**Core vs Satellite** (operating equity; amber tick = target)")
        st.altair_chart(_core_sat(cs), width="stretch")
        st.caption(f"Core {c.money(_f(cs['core_value']))} · Satellite "
                   f"{c.money(_f(cs['satellite_value']))}"
                   + (f" (of which trade {c.money(_f(cs['trade_value']))})"
                      if cs["trade_value"] else "")
                   + ". Role is your bucket column: core = core; satellite, trade "
                   "and blank = satellite.")
        if cs["unset"]:
            st.caption(f"{len(cs['unset'])} holding(s) have no bucket. The Stocks "
                       "page's *Suggest buckets* pre-fills core or satellite for "
                       "you to confirm in the holdings file.")
    with right:
        st.markdown("**Sleeves** (whole book)")
        st.altair_chart(_bars(o.sleeves, "the book"), width="stretch")

    left, right = st.columns(2)
    with left:
        st.markdown("**Market-cap band** (operating equity)")
        st.altair_chart(_bars(o.caps, "operating equity"), width="stretch")
        st.caption(f"Band source: {mk.CAP_SOURCE}.")
    with right:
        st.markdown(f"**Top holdings**: top 5 = {c.pct(_f(o.top5_pct))}, "
                    f"top 10 = {c.pct(_f(o.top10_pct))} of the book")
        smax = bv.ov.load_config()["sector_max_pct"]
        st.markdown(f"**Sectors** (operating equity; dashed = {smax}% guideline)")
        st.altair_chart(_bars(o.sectors, "operating equity", smax,
                              f"{smax}% of operating equity"), width="stretch")

    st.markdown("**Positions**")
    st.dataframe(
        _positions_table(o), hide_index=True, width="stretch",
        column_config={
            "Qty": st.column_config.NumberColumn(format="%.0f"),
            "Avg cost": st.column_config.NumberColumn(format="₹%.2f"),
            "Price": st.column_config.NumberColumn(format="₹%.2f"),
            "Value": st.column_config.NumberColumn(format="₹%.0f"),
            "Weight %": st.column_config.NumberColumn(format="%.2f"),
            "Guideline %": st.column_config.NumberColumn(format="%.0f"),
            "P/L": st.column_config.NumberColumn(format="₹%.0f"),
            "P/L %": st.column_config.NumberColumn(format="%+.1f"),
        })

    if o.disclosures:
        with st.expander(f"Missing or qualified data ({len(o.disclosures)})"):
            st.markdown("\n".join(f"- {d}" for d in o.disclosures))

    _snapshots(o)


def _snapshots(o) -> None:
    st.markdown("**Snapshots**")
    with st.form("ov_snapshot", border=False):
        cols = st.columns([4, 1.5], vertical_alignment="bottom")
        label = cols[0].text_input("Label", placeholder="e.g. Q3 review",
                                   label_visibility="collapsed")
        if cols[1].form_submit_button("Save snapshot", width="stretch"):
            sid = bv.snapshot(o, label.strip())
            st.success(f"Saved snapshot #{sid} at the {o.price_date} close.")
    con = rdb.connect()
    try:
        snaps = rdb.snapshots(con)
        trail = rdb.audit_trail(con, 30)
    finally:
        con.close()
    if snaps:
        st.dataframe(pd.DataFrame([{
            "#": s["id"], "Taken": s["taken_at"], "Prices of": s["price_date"],
            "Label": s["label"], "Value": float(s["total_value"]),
            "Invested": float(s["invested"]),
            "Core %": s["summary"]["core_satellite"].get("core_pct")}
            for s in snaps]), hide_index=True, width="stretch",
            column_config={"Value": st.column_config.NumberColumn(format="₹%.0f"),
                           "Invested": st.column_config.NumberColumn(format="₹%.0f")})
    else:
        st.caption("No snapshots yet.")
    with st.expander(f"Audit log ({len(trail)} most recent)"):
        if trail:
            st.dataframe(pd.DataFrame([{"When": t["at"], "Who": t["actor"],
                                        "What": t["action"], "On": t["target"],
                                        "Detail": str(t["detail"])} for t in trail]),
                         hide_index=True, width="stretch")
        else:
            st.caption("Nothing recorded yet.")
