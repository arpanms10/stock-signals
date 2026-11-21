"""Portfolio › Risk & plan: how risky today's mix has been, what it is
exposed to, and a Core–Satellite plan for new money.

Historical figures hold your current weights constant over the past: what
this mix would have done, not a forecast. The plan is a simulation you read;
nothing here edits your holdings file or places an order.
"""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from book import risk_view as rv
from strategy import portfolio_risk as pr

PORTFOLIO_BLUE = "#4e79a7"
BENCH_GREY = "#8a8f98"


@st.cache_data(ttl=300, show_spinner="Reading the book…")
def _gather():
    g = rv.gather()
    if "error" in g:
        return g, None
    return g, rv.risk(g)


def _p(x, fmt="{:.1f}%") -> str:
    return "—" if x is None else fmt.format(x)


def render() -> None:
    st.markdown("## Risk & plan")
    st.caption("Historical figures hold your **current weights** constant over the past "
               "three years: what this mix would have done, not what it will do. The plan "
               "below is a simulation: nothing is written and no order is placed.")
    g, r = _gather()
    if "error" in g:
        st.info(g["error"])
        return
    _history(r)
    st.divider()
    _exposures(r)
    st.divider()
    _plan(g)


# ------------------------------------------------------------------ history

def _history(r: pr.RiskReport) -> None:
    h, b = r.history, r.benchmark
    st.markdown("**Historical risk** (backward-looking)")
    if not h.get("days"):
        st.caption("Not enough price history.")
        return
    m = st.columns(5)
    m[0].metric("Volatility, a year", _p(h["volatility_pct"]),
                delta=f"benchmark {_p(b.get('volatility_pct'))}", delta_color="off")
    m[1].metric("Beta", _p(h.get("beta"), "{:.2f}"), help="vs NIFTY 500, daily returns")
    m[2].metric("Maximum drawdown", _p(h["max_drawdown_pct"]),
                delta=f"benchmark {_p(b.get('max_drawdown_pct'))}", delta_color="off",
                help=f"peak {h['drawdown_peak']}, trough {h['drawdown_trough']}")
    m[3].metric("Worst month", _p(h["worst_month_pct"]),
                delta=f"benchmark {_p(b.get('worst_month_pct'))}", delta_color="off")
    m[4].metric("1-day loss, 95%", _p(h["var95_1d_pct"]),
                help="On 1 day in 20 the mix lost at least this much (historical)")
    st.caption(f"{h['from']} to {h['to']}, {h['days']} sessions. Return, a year: "
               f"{_p(h.get('return_ann_pct'))} (benchmark {_p(b.get('return_ann_pct'))}). "
               "Price returns: dividends are left out, and a demerger (ITC into ITC "
               "Hotels) shows as a fall in the parent. " + " ".join(r.notes))
    if r.curve:
        df = pd.DataFrame(r.curve).melt("date", var_name="series", value_name="value")
        df["date"] = pd.to_datetime(df["date"])
        df["series"] = df["series"].map({"portfolio": "Your current mix",
                                         "benchmark": "NIFTY 500"})
        chart = alt.Chart(df).mark_line(strokeWidth=2).encode(
            x=alt.X("date:T", title=None),
            y=alt.Y("value:Q", title="Growth of ₹100", scale=alt.Scale(zero=False)),
            color=alt.Color("series:N", title=None, scale=alt.Scale(
                domain=["Your current mix", "NIFTY 500"], range=[PORTFOLIO_BLUE, BENCH_GREY]),
                legend=alt.Legend(orient="top")),
            tooltip=[alt.Tooltip("date:T"), "series:N", alt.Tooltip("value:Q", format=".1f")])
        st.altair_chart(chart.properties(height=240), use_container_width=True)


# ------------------------------------------------------------------ exposures

def _exposures(r: pr.RiskReport) -> None:
    st.markdown("**What the book is exposed to**")
    c = st.columns([1, 1])
    with c[0]:
        st.caption("Themes across sectors (% of operating equity). The sector groupings "
                   "are conventions: a bank is rate-sensitive, a metal maker commodity-exposed.")
        st.dataframe(pd.DataFrame([{
            "Theme": t["label"], "%": round(t["pct"], 1), "Review above %": t["warn_pct"],
            "Flag": "review" if t["flag"] else "", "Holdings": ", ".join(t["holdings"])}
            for t in r.themes]), hide_index=True, width="stretch")
    with c[1]:
        st.caption("Risk flags (% of operating equity in holdings that carry each).")
        st.dataframe(pd.DataFrame([{
            "Flag": f["label"], "%": round(f["pct"], 1), "Holdings": ", ".join(f["holdings"])}
            for f in r.flags]), hide_index=True, width="stretch")
        st.caption(f"Effective number of holdings: {_p(r.effective_names, '{:.1f}')} "
                   "(1 / sum of squared weights; equal weights in that many names would "
                   "be as concentrated).")
    c = st.columns([1, 1])
    with c[0]:
        st.caption("Same business, several tickers (sector groups held more than once).")
        if r.overlaps:
            st.dataframe(pd.DataFrame([{"Group": o["group"], "%": round(o["pct"], 1),
                                        "Holdings": ", ".join(o["holdings"])}
                                       for o in r.overlaps]), hide_index=True, width="stretch")
        pairs = r.correlation.get("pairs", [])
        st.caption("Largest holdings moving together over a year (correlation ≥ "
                   f"{pr.load_config()['correlation_flag']}): "
                   + (", ".join(f"{p['a']}–{p['b']} {p['corr']:.2f}" for p in pairs)
                      if pairs else "none."))
    with c[1]:
        st.caption("Satellite categories, % of operating equity, against the guideline "
                   "(config/core_satellite.yaml). Categories come from the scorecard.")
        st.dataframe(pd.DataFrame([{
            "Category": s["category"].replace("_", " "), "%": round(s["pct"], 1),
            "Guideline %": s["target_pct"]} for s in r.satellite_mix]),
            hide_index=True, width="stretch")
        known = [x for x in r.liquidity if x["days"] is not None]
        part = pr.load_config()["liquidity"]["participation_pct"]
        if known and known[0]["days"] < 1:
            st.caption(f"Liquidity: every holding with volume on file could be sold within "
                       f"a day at {part}% of its average daily traded value.")
        elif known:
            st.caption("Slowest to sell: " + ", ".join(
                f"{x['symbol']} {x['days']:.1f} days" for x in known[:3] if x["days"] >= 1)
                + f" (at {part}% of average daily traded value).")
        nov = [x["symbol"] for x in r.liquidity if x["days"] is None]
        if nov:
            st.caption("No volume on file: " + ", ".join(nov) + ".")


# ------------------------------------------------------------------ plan

def _plan(g: dict) -> None:
    st.markdown("**Core–Satellite plan for new money**")
    blank = sum(1 for i in g["items"] if not i["position"].bucket)
    c = st.columns([1, 1, 2])
    monthly = c[0].number_input("New money a month, ₹", min_value=0, value=25000, step=5000,
                                key="plan_monthly")
    months = c[1].slider("Over months", 1, 36, 12, key="plan_months")
    opts = ["Your bucket labels", "Fill blank buckets with the scorecard's suggestion"]
    pick = c[2].radio("Roles", opts, index=1 if blank > len(g["items"]) / 2 else 0,
                      key="plan_roles", help="A what-if: your holdings file is not changed. "
                      "Set bucket = core / satellite there to make a role yours.")
    if blank:
        c[2].caption(f"{blank} of {len(g['items'])} holdings have no bucket set.")
    p = rv.make_plan(g, float(monthly), int(months), pick == opts[1])
    for w in p.warnings:
        st.warning(w)

    b = {x["role"]: x for x in p.buckets}
    m = st.columns(4)
    m[0].metric("Core now → after", f"{b['core']['before_pct']:.0f}% → {b['core']['after_pct']:.0f}%",
                help=f"target {b['core']['target_pct']:.0f}%")
    m[1].metric("Satellite now → after",
                f"{b['satellite']['before_pct']:.0f}% → {b['satellite']['after_pct']:.0f}%",
                help=f"target {b['satellite']['target_pct']:.0f}%")
    m[2].metric("Months to 70% core, new money only",
                "—" if p.months_to_target is None else f"{p.months_to_target:.0f}")
    m[3].metric("Estimated tax on trims", f"₹{sum(r.trim_tax for r in p.rows):,.0f}")

    rows = []
    for r in p.rows:
        rows.append({"Holding": r.symbol, "Role": r.role,
                     "Now ₹": round(r.current), "Now %": r.current / p.book_after * 100,
                     "Target %": r.target / p.book_after * 100,
                     "Buy ₹": round(r.buy) or None, "a month ₹": round(r.buy / p.months) or None,
                     "Trim ₹": round(r.trim) or None, "Est. tax ₹": round(r.trim_tax) or None,
                     "After %": r.after / p.book_after * 100, "Cap %": r.cap_pct,
                     "Why": "; ".join(r.notes)})
    st.caption(f"₹{p.contribution:,.0f} over {p.months} months. % of the whole book after "
               f"new money (₹{p.book_after:,.0f}). Unallocated: ₹{p.unallocated:,.0f}.")
    num = st.column_config.NumberColumn
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={
        "Now ₹": num(format="%d"), "Now %": num(format="%.1f"), "Target %": num(format="%.1f"),
        "Buy ₹": num(format="%d"), "a month ₹": num(format="%d"), "Trim ₹": num(format="%d"),
        "Est. tax ₹": num(format="%d"), "After %": num(format="%.1f"),
        "Cap %": num(format="%g")})
    with st.expander("Sectors before and after"):
        st.dataframe(pd.DataFrame([{"Sector": s["sector"], "Now %": round(s["before_pct"], 1),
                                    "After %": round(s["after_pct"], 1)} for s in p.sectors]),
                     hide_index=True, width="stretch")
    with st.expander("Every constraint, and how the plan is built"):
        st.markdown("\n".join(f"- {c}" for c in p.constraints))
        st.caption("Targets come from rules, not forecasts: the scorecard is used only to "
                   "stop new money going into a flagged holding, never to tilt weights, "
                   "because it has not been backtested. Tax is an estimate from your average "
                   "cost and long/short split (config/scoring.yaml); Phase 6 adds lot-level "
                   "detail.")
