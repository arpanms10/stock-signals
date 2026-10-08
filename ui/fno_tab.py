"""F&O tab: PCR, OI support/resistance and the expected range for one underlying.

Streamlit runs every tab's code on each rerun, so nothing here touches NSE
until Analyse is pressed; results are then cached for a few minutes.
"""
from __future__ import annotations

import datetime as dt

import altair as alt
import pandas as pd
import streamlit as st

import fno_report
from data.sources import nse_derivatives as nsed
from ui import components as c

# Reference palette, dark steps (the dashboard theme is dark). Two series only:
# puts are the support side, calls the resistance side.
PUT, CALL = "#3987e5", "#d95926"
INK, MUTED, GRID = "#ffffff", "#c3c2b7", "#3a3a37"
BIAS_TONE = {"bullish": "good", "bearish": "bad", "stretched": "warn",
             "neutral": "neutral", "n/a": "neutral"}


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def _underlyings() -> list[str]:
    u = nsed.fno_underlyings()
    return list(u[u.kind == "index"].symbol) + sorted(u[u.kind == "equity"].symbol)


@st.cache_data(ttl=3600, show_spinner=False)
def _expiries(symbol: str) -> list[dt.date]:
    return nsed.expiries(symbol)


@st.cache_data(ttl=180, show_spinner="Fetching the option chain from NSE...")
def _analyse(symbol: str, expiry: dt.date | None):
    return fno_report.load(symbol, expiry)


def _style(chart: alt.Chart) -> alt.Chart:
    return (chart.configure(background="transparent")
            .configure_view(strokeWidth=0)
            .configure_axis(labelColor=MUTED, titleColor=MUTED, gridColor=GRID,
                            domainColor=GRID, tickColor=GRID, labelFontSize=11)
            .configure_legend(labelColor=INK, titleColor=MUTED, orient="top"))


def _bars(t: pd.DataFrame, spot: float, field: str, title: str) -> alt.Chart:
    """Calls and puts side by side per strike, a rule at spot."""
    long = pd.concat([
        pd.DataFrame({"strike": t.strike, "side": "Puts", "value": t[f"pe_{field}"],
                      "role": t.role}),
        pd.DataFrame({"strike": t.strike, "side": "Calls", "value": t[f"ce_{field}"],
                      "role": t.role}),
    ])
    color = alt.Color("side:N", title=None,
                      scale=alt.Scale(domain=["Puts", "Calls"], range=[PUT, CALL]))
    bars = alt.Chart(long).mark_bar(cornerRadiusEnd=2).encode(
        x=alt.X("strike:O", title="strike", axis=alt.Axis(labelAngle=-90)),
        xOffset=alt.XOffset("side:N", sort=["Puts", "Calls"]),
        y=alt.Y("value:Q", title=title, axis=alt.Axis(format="~s")),
        color=color,
        tooltip=[alt.Tooltip("strike:Q", format=","), "side:N",
                 alt.Tooltip("value:Q", title=title, format=","),
                 alt.Tooltip("role:N", title="role")],
    )
    # Spot sits between strikes; mark it on the nearest one.
    atm = float(t.strike.iloc[(t.strike - spot).abs().argmin()])
    rule = alt.Chart(pd.DataFrame({"strike": [atm], "label": [f"spot {spot:,.2f}"]})) \
        .mark_rule(color=MUTED, strokeDash=[4, 3], strokeWidth=1.5) \
        .encode(x="strike:O", tooltip=["label:N"])
    return _style((bars + rule).properties(height=300))


def _pcr_line(h: pd.DataFrame) -> alt.Chart:
    base = alt.Chart(h).encode(
        x=alt.X("ts:T", title=None, axis=alt.Axis(format="%H:%M", tickCount=6)))
    line = base.mark_line(strokeWidth=2, color=PUT).encode(
        y=alt.Y("pcr_oi:Q", title="PCR (OI)", scale=alt.Scale(zero=False),
                axis=alt.Axis(format=".2f", tickCount=4)))
    pts = base.mark_circle(size=64, color=PUT).encode(
        y="pcr_oi:Q",
        tooltip=[alt.Tooltip("ts:T", format="%d-%b %H:%M"),
                 alt.Tooltip("pcr_oi:Q", format=".2f", title="PCR"),
                 alt.Tooltip("spot:Q", format=","),
                 alt.Tooltip("support:Q", format=","),
                 alt.Tooltip("resistance:Q", format=",")])
    return _style((line + pts).properties(height=220))


def _px(x) -> str:
    if x is None or pd.isna(x):
        return "--"
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:,.2f}"


def render() -> None:
    st.subheader("F&O: where option writers have put their money")
    st.caption(
        "Support is the strike below spot with the most put open interest; "
        "resistance, the strike above with the most call OI. The range takes "
        "the tighter of each wall and spot ± the ATM straddle. This describes "
        "positioning, not a forecast. How often the range held is measured in "
        "`docs/f_o/validation.md`.")

    try:
        symbols = _underlyings()
    except RuntimeError as exc:
        st.error(f"Could not reach NSE for the F&O list: {exc}")
        return

    a, b, go = st.columns([2, 2, 1])
    sym = a.selectbox("Underlying", symbols, index=0, key="fno_sym")
    try:
        exps = _expiries(sym)
    except RuntimeError as exc:
        st.error(f"Could not load expiries for {sym}: {exc}")
        return
    monthly = set(nsed.monthly_expiries(exps))
    default = nsed.nearest_monthly(exps, dt.date.today())
    upcoming = [e for e in exps if e >= dt.date.today()]
    exp = b.selectbox("Expiry", upcoming, index=upcoming.index(default),
                      format_func=lambda e: e.strftime("%d-%b-%Y")
                      + ("  · monthly" if e in monthly else "  · weekly"),
                      key="fno_exp")
    go.write("")
    if go.button("Analyse", type="primary", width="stretch"):
        st.session_state["fno_req"] = (sym, exp)
    req = st.session_state.get("fno_req")
    if not req:
        st.info("Pick an underlying and press **Analyse**. Nothing is fetched "
                "from NSE until then.")
        return
    if st.button("Refresh from NSE", key="fno_refresh"):
        _analyse.clear()

    try:
        res = _analyse(*req)
    except (nsed.NotFnO, ValueError, RuntimeError) as exc:
        st.error(str(exc))
        return
    v = res.view

    st.markdown(
        f"### {v.symbol} &nbsp; {c.pill(v.bias.upper(), BIAS_TONE[v.bias])}",
        unsafe_allow_html=True)
    st.caption(f"Expiry {v.expiry:%d-%b-%Y}, {v.sessions} sessions left · "
               f"NSE data as of {v.timestamp:%d-%b %H:%M}"
               + (f" · lot {v.lot}" if v.lot else "") + " · OI in contracts")
    for w in v.warnings:
        st.warning(w)

    m = st.columns(6)
    m[0].metric("Spot", _px(v.spot))
    m[1].metric("Futures", _px(v.futures.price) if v.futures else "--",
                f"{v.futures.basis:+,.2f} basis" if v.futures else None,
                delta_color="off", delta_arrow="off")
    m[2].metric("PCR (OI)", f"{v.pcr['oi']:.2f}" if v.pcr["oi"] else "--",
                v.pcr_bias, delta_color="off", delta_arrow="off")
    m[3].metric("PCR (today's ΔOI)",
                f"{v.pcr['chg_oi']:.2f}" if v.pcr["chg_oi"] else "--",
                v.chg_pcr_bias, delta_color="off", delta_arrow="off",
                help="Put/call ratio of OI added today. Blank when a side is "
                     "unwinding -- a ratio of a gain and a loss means nothing.")
    m[4].metric("Max pain", _px(v.max_pain))
    m[5].metric("ATM IV", f"{v.atm_iv:.1f}%" if v.atm_iv else "--")

    m = st.columns(5)
    m[0].metric("Strongest support", _px(v.support.strike) if v.support else "--",
                f"next {_px(v.supports[1].strike)}" if len(v.supports) > 1 else None,
                delta_color="off", delta_arrow="off")
    m[1].metric("Strongest resistance",
                _px(v.resistance.strike) if v.resistance else "--",
                f"next {_px(v.resistances[1].strike)}" if len(v.resistances) > 1 else None,
                delta_color="off", delta_arrow="off")
    m[2].metric("Expected move", f"±{_px(v.straddle)}" if v.straddle else "--",
                f"±{_px(v.iv_move)} by IV" if v.iv_move else None, delta_color="off", delta_arrow="off",
                help="ATM straddle: what the market charges for a move either way.")
    m[3].metric("Range low", _px(v.range_low), f"from {v.low_from}" if v.low_from else None,
                delta_color="off", delta_arrow="off", help="The tighter of the support wall and "
                "spot minus the expected move.")
    m[4].metric("Range high", _px(v.range_high), f"from {v.high_from}" if v.high_from else None,
                delta_color="off", delta_arrow="off", help="The tighter of the resistance wall and "
                "spot plus the expected move.")

    with st.expander("Why these numbers", expanded=False):
        for r in v.reasons:
            st.markdown(f"- {r}")

    t = fno_report.chain_table(res)
    st.markdown("**Open interest by strike**")
    st.altair_chart(_bars(t, v.spot, "oi", "open interest"), width="stretch")
    st.markdown("**Change in open interest today**")
    st.altair_chart(_bars(t, v.spot, "chg_oi", "change in OI"), width="stretch")

    h = fno_report.history(v.symbol, v.expiry,
                           since=dt.datetime.combine(v.timestamp.date(), dt.time()))
    st.markdown("**PCR through the day**")
    if len(h) >= 2:
        st.altair_chart(_pcr_line(h), width="stretch")
        st.caption(f"From {len(h)} stored snapshots today. Each Analyse or "
                   "`run_fno.py` run adds one.")
    else:
        st.caption("One snapshot so far today. Each refresh or `run_fno.py` "
                   "run stores another, and this becomes a line.")

    with st.expander("Option chain table"):
        st.dataframe(t, hide_index=True, width="stretch")

    path = fno_report.write_excel([res])
    st.download_button("Download Excel report", path.read_bytes(),
                       file_name=path.name,
                       mime="application/vnd.openxmlformats-officedocument."
                            "spreadsheetml.sheet")
