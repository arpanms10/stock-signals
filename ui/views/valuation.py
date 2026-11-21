"""Portfolio › Valuation: what each holding's price implies, and scenario values.

For every holding: the method that suits the business, today's multiple
against its own 5 years and its peers, the growth the price implies against
the growth delivered, and bear / base / bull values from the explicit
assumptions in config/valuation.yaml. Scenarios, not price targets.
"""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from book import valuations as bv
from strategy import valuation as val


@st.cache_data(ttl=300, show_spinner=False)
def _load() -> tuple[list[dict], list[str], dict]:
    import dataclasses
    vs, notes = bv.build()
    return [dataclasses.asdict(v) for v in vs], notes, val.load_config()


def _n(x, fmt="{:,.1f}") -> str:
    return "—" if x is None else fmt.format(x)


def _mult(v: dict) -> str:
    h = v["headline"]
    x = v["current"].get(h)
    if x is None:
        return "—"
    return f"{val.MULT_LABEL.get(h, h)} {x:.1f}" + ("%" if h == "yield" else "×")


def render() -> None:
    st.markdown("## Valuation")
    st.caption("What each price implies, against the stock's own history and its peers, "
               "and bear / base / bull values from the stated assumptions "
               "(config/valuation.yaml). **Scenarios, not price targets**: change an "
               "assumption and every value moves with it.")
    vs, notes, cfg = _load()
    for n in notes:
        st.caption(n)
    if not vs:
        st.info("No holdings to value yet (Portfolio › Holding data › Refresh data).")
        return
    _summary(vs)
    st.divider()
    pick = st.selectbox("Holding", [v["symbol"] for v in vs], key="val_pick")
    _detail(next(v for v in vs if v["symbol"] == pick), cfg)


def _summary(vs: list[dict]) -> None:
    rows = []
    for v in sorted(vs, key=lambda v: v["symbol"]):
        sc, b, p, r = v["scenarios"], v["band"], v["peers"], v["reverse"]
        rows.append({
            "Holding": v["symbol"], "Method": val.METHOD_LABEL.get(v["method"], v["method"]),
            "Price ₹": v["price"], "Multiple": _mult(v),
            "Own 5y percentile": None if b.get("percentile") is None else b["percentile"] * 100,
            "× peer median": p.get("relative"),
            "Price implies %": r.get("implied_pct"), "Delivered %": r.get("delivered_pct"),
            "Bear ₹": sc.get("bear", {}).get("fair_value"),
            "Base ₹": sc.get("base", {}).get("fair_value"),
            "Bull ₹": sc.get("bull", {}).get("fair_value"),
            "Margin of safety %": v["margin_of_safety_pct"]})
    st.markdown("**Your holdings**")
    st.caption("Own 5y percentile: where today's multiple sits in its own last five years "
               "(0 = cheapest month, 100 = dearest). Price implies: the growth (ROE for "
               "lenders, distribution growth for REITs) that today's price needs to earn the "
               "cost of equity. Margin of safety = (base − price) / base.")
    num = st.column_config.NumberColumn
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={
        "Price ₹": num(format="%.1f"), "Own 5y percentile": num(format="%.0f"),
        "× peer median": num(format="%.2f"), "Price implies %": num(format="%.1f"),
        "Delivered %": num(format="%.1f"), "Bear ₹": num(format="%.0f"),
        "Base ₹": num(format="%.0f"), "Bull ₹": num(format="%.0f"),
        "Margin of safety %": num(format="%.0f")})


def _detail(v: dict, cfg: dict) -> None:
    st.markdown(f"### {v['symbol']} · {val.METHOD_LABEL.get(v['method'], v['method'])}")
    st.caption(v["method_why"])
    m = st.columns(4)
    m[0].metric("Price", _n(v["price"], "₹{:,.1f}"), help=f"close on {v['price_date']}")
    m[1].metric("Multiple", _mult(v))
    b = v["band"]
    m[2].metric("Own 5-year percentile", _n(None if b.get("percentile") is None
                                            else b["percentile"] * 100, "{:.0f}"),
                help=b.get("note") or f"{b.get('months')} months, {b.get('from')} to {b.get('to')}")
    m[3].metric("Margin of safety", _n(v["margin_of_safety_pct"], "{:.0f}%"),
                help="(base scenario − price) / base scenario")
    for n in v["notes"]:
        st.caption(n)

    if b.get("base") is not None and v["history"]:
        _band_chart(v, b)

    sc = v["scenarios"]
    if sc:
        st.markdown("**Scenarios** (not price targets)")
        keys = sorted({k for s in sc.values() for k in s["assumptions"]})
        tbl = [{"": "Value per share ₹", **{k.title(): _n(sc[k]["fair_value"], "{:,.0f}")
                                           for k in val.SCEN if k in sc}},
               {"": "vs price", **{k.title(): _n(sc[k]["upside_pct"], "{:+.0f}%")
                                   for k in val.SCEN if k in sc}}]
        for a in keys:
            tbl.append({"": _label(a), **{
                k.title(): _cell(sc[k]["assumptions"].get(a, "")) for k in val.SCEN if k in sc}})
        st.dataframe(pd.DataFrame(tbl), hide_index=True, width="stretch")
    if v["cross_check"]:
        cc = v["cross_check"]
        st.caption("Cross-check, earnings × own P/E band: " + " / ".join(
            f"{k} ₹{cc[k]['fair_value']:,.0f}" for k in val.SCEN if k in cc))

    r = v["reverse"]
    if r:
        st.markdown("**What the price implies**")
        line = (f"{_n(r.get('implied_pct'), '{:.1f}%')} — {r.get('basis', '')}")
        if r.get("delivered_pct") is not None:
            line += (f". Delivered: {r['delivered_pct']:.1f}% ({r.get('delivered_basis', '')}); "
                     f"gap {r['gap_pts']:+.1f} pts" if r.get("gap_pts") is not None else "")
        if r.get("note"):
            line += f". {r['note']}"
        st.write(line)

    p = v["peers"]
    if p:
        st.markdown("**Peers**")
        if p.get("median") is not None:
            st.write(f"{val.MULT_LABEL[p['metric']]} median {p['median']:.1f}× across "
                     f"{p['n']} {p.get('industry', '')} peers (range {p['low']:.1f}–"
                     f"{p['high']:.1f}); this holding is "
                     + (f"{p['relative']:.2f}× the median." if p.get("relative") else "not comparable."))
            st.caption("Peers: " + ", ".join(p["names"]))
        else:
            st.caption(p.get("note", "no peers"))

    with st.expander("Assumptions in use (config/valuation.yaml)"):
        st.json({k: v["assumptions"].get(k) for k in v["assumptions"]} or
                {"discount": cfg["discount"], "terminal_growth_pct": cfg["terminal_growth_pct"]})


def _label(key: str) -> str:
    out = key.replace("_pct", " (%)").replace("_cr", " (₹ cr)").replace("_y1_5", ", years 1–5")
    return out.replace("_", " ").capitalize()


def _cell(x) -> str:
    if isinstance(x, (int, float)):
        return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:g}"
    return str(x)


def _band_chart(v: dict, b: dict) -> None:
    h = v["headline"]
    df = pd.DataFrame([{"date": x["date"], "value": x[h]} for x in v["history"] if x.get(h)])
    df["date"] = pd.to_datetime(df["date"])
    label = val.MULT_LABEL[h]
    rules = pd.DataFrame([{"level": b[k], "which": f"{k} ({p}th pct)"} for k, p in
                          (("bear", 20), ("base", 50), ("bull", 80))])
    line = alt.Chart(df).mark_line(strokeWidth=2).encode(
        x=alt.X("date:T", title=None),
        y=alt.Y("value:Q", title=label, scale=alt.Scale(zero=False)),
        tooltip=[alt.Tooltip("date:T"), alt.Tooltip("value:Q", title=label, format=".1f")])
    ref = alt.Chart(rules).mark_rule(strokeDash=[4, 4], color="#8a8f98").encode(
        y="level:Q", tooltip=["which:N", alt.Tooltip("level:Q", format=".1f")])
    txt = alt.Chart(rules).mark_text(align="left", dx=4, dy=-6, color="#8a8f98").encode(
        y="level:Q", x=alt.value(0), text="which:N")
    st.markdown(f"**{label}, last 5 years** (month-end, each month using only results "
                "filed by then)")
    st.altair_chart((line + ref + txt).properties(height=260), use_container_width=True)
