"""F&O tab: PCR, OI support/resistance and the expected range for one underlying.

Streamlit runs every tab's code on each rerun, so nothing here touches NSE
until Analyse is pressed; results are then cached for a few minutes.
"""
from __future__ import annotations

import datetime as dt

import altair as alt
import numpy as np
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


def _pct(p) -> str:
    return "--" if p is None else f"{100 * p:.0f}%"


def _reach_caption(x) -> str | None:
    if x is None or x.hist_touch is None:
        return None
    p = x.hist_touch_intraday if x.hist_touch_intraday is not None else x.hist_touch
    return f"reached {_pct(p)}"


def _vol_row(vc) -> None:
    st.markdown("**Volatility**")
    m = st.columns(5)
    m[0].metric("Straddle IV", f"{vc.iv:.1f}%" if vc.iv else "--",
                help="Annualised volatility implied by the ATM straddle -- the "
                     "same measure the history uses, so the percentile compares "
                     "like with like.")
    m[1].metric("IV percentile", f"{vc.iv_pct:.0f}" if vc.iv_pct is not None else "--",
                None if vc.iv_pct is not None else
                ("history not current" if vc.iv_hist_n == 0 else f"{vc.iv_hist_n} days of history"),
                delta_color="off", delta_arrow="off",
                help="Share of the past year's daily straddle IVs below today's. "
                     "Needs 120 days of F&O history (run_fno_backtest.py --ingest).")
    m[2].metric("Realised (20d)", f"{vc.rv20:.1f}%" if vc.rv20 else "--",
                f"IV/RV {vc.iv_rv:.2f}" if vc.iv_rv else None,
                delta_color="off", delta_arrow="off",
                help="How much it has actually moved, annualised, over the last 20 "
                     "sessions. Needs a history refreshed within the last week.")
    m[3].metric("Skew", f"{vc.skew:+.1f} pts" if vc.skew is not None else "--",
                help="IV of the put one straddle below spot minus the call one "
                     "straddle above. Positive: downside protection costs more.")
    m[4].metric("Next month", f"{vc.term:+.1f} pts" if vc.term is not None else "--",
                help="Next monthly's straddle IV minus this one. Negative "
                     "(inverted) usually means an event -- often results -- "
                     "before this expiry.")


def _payoff_chart(oc, v) -> alt.Chart:
    """P&L at expiry for the position, spot +/- two straddles."""
    span = 2 * (v.straddle or v.spot * 0.05)
    lo, hi = v.spot - span, v.spot + span
    xs = np.linspace(lo, hi, 121)
    unit = oc.units
    if oc.side == "CE":
        pl = (np.maximum(xs - oc.strike, 0) - oc.mid) * unit
    else:
        pl = (np.maximum(oc.strike - xs, 0) - oc.mid) * unit
    df = pd.DataFrame({"x": xs, "pl": pl})
    pad = 0.08 * (pl.max() - pl.min() or 1)
    xsc = alt.Scale(domain=[float(lo), float(hi)], nice=False)
    ysc = alt.Scale(domain=[float(pl.min() - pad), float(pl.max() + pad)], nice=False)
    color = CALL if oc.side == "CE" else PUT
    line = alt.Chart(df).mark_line(strokeWidth=2, color=color, clip=True).encode(
        x=alt.X("x:Q", scale=xsc, title="price at expiry", axis=alt.Axis(format=",.0f")),
        y=alt.Y("pl:Q", scale=ysc, title="P&L at expiry (₹)", axis=alt.Axis(format=",.0f")),
        tooltip=[alt.Tooltip("x:Q", title="price at expiry", format=",.0f"),
                 alt.Tooltip("pl:Q", title="P&L (₹)", format=",.0f")])
    zero = alt.Chart(pd.DataFrame({"y": [0.0]})).mark_rule(color=MUTED, strokeWidth=1).encode(
        y=alt.Y("y:Q", scale=ysc, title="P&L at expiry (₹)"))
    marks = pd.DataFrame({"x": [v.spot, oc.breakeven], "label": ["spot", "breakeven"]})
    rules = alt.Chart(marks).mark_rule(color=MUTED, strokeDash=[4, 3]).encode(
        x=alt.X("x:Q", scale=xsc, title="price at expiry"),
        tooltip=["label:N", alt.Tooltip("x:Q", title="level", format=",.2f")])
    return _style((line + zero + rules).properties(height=220))


def _option_panel(v, res) -> None:
    """Facts about one contract. Deliberately no buy/sell suggestion: the
    validation found nothing in this data that predicts direction."""
    from strategy import fno
    st.markdown("**Check an option** &nbsp; " + c.pill("facts, not a recommendation", "neutral"),
                unsafe_allow_html=True)
    t = res.chain.strikes
    near = t[(t["strike"] - v.spot).abs() <= v.spot * 0.08]["strike"].tolist()
    if not near:
        return
    a, b, d = st.columns([1, 1.4, 1])
    side = a.radio("Type", ["Put (PE)", "Call (CE)"], horizontal=True, key=f"oc_side_{v.symbol}_{v.expiry}")
    side = "PE" if side.startswith("Put") else "CE"
    default = min(near, key=lambda k: abs(k - v.spot))
    strike = b.selectbox("Strike", near, index=near.index(default),
                         format_func=lambda k: f"{k:,.0f}" if k >= 100 else f"{k:,.2f}",
                         key=f"oc_strike_{v.symbol}_{v.expiry}")
    lots = d.number_input("Lots", min_value=1, value=1, step=1, key=f"oc_lots_{v.symbol}_{v.expiry}")
    oc = fno.option_check(v, t, strike, side, int(lots), fno_report.reach_curve(),
                          fno.settings(fno_report._cfg())["risk_free_pct"])
    if oc is None or oc.mid is None:
        st.info("No live quote or trade for this strike.")
        return
    u = oc.units
    m = st.columns(5)
    m[0].metric("Premium (mid)", _px(oc.mid), f"bid {_px(oc.bid)} / ask {_px(oc.ask)}",
                delta_color="off", delta_arrow="off")
    m[1].metric("Cost", f"₹{oc.cost:,.0f}", f"{oc.lots} × {oc.lot or '?'}",
                delta_color="off", delta_arrow="off",
                help="Premium × lot size × lots. For a buyer, also the maximum loss.")
    m[2].metric("Breakeven at expiry", _px(oc.breakeven),
                f"{oc.breakeven_pct:+.1f}% · {oc.breakeven_moves:.1f} moves",
                delta_color="off", delta_arrow="off")
    best = "model" if v.kind == "index" else "historical"
    m[3].metric("Beyond breakeven at expiry",
                _pct(oc.p_profit_model if best == "model" else oc.p_profit_hist),
                f"{'historical' if best == 'model' else 'model'} "
                f"{_pct(oc.p_profit_hist if best == 'model' else oc.p_profit_model)}",
                delta_color="off", delta_arrow="off",
                help=f"How often a move this size finished beyond the breakeven. The "
                     f"{best} estimate is shown first: it has been the better-calibrated "
                     f"one for {'indices' if v.kind == 'index' else 'stocks'}. A move of "
                     "the same size the other way gets the same number -- there is no "
                     "direction in it.")
    m[4].metric("In the money at expiry",
                _pct(oc.p_itm_model if best == "model" else oc.p_itm_hist),
                f"traded through BE: {_pct(oc.p_touch_be_intraday)}",
                delta_color="off", delta_arrow="off",
                help="Finished beyond the strike (some value left), and how often price "
                     "traded through the breakeven at some point before expiry.")
    m = st.columns(5)
    m[0].metric("Delta", f"{oc.delta:+.2f}" if oc.delta is not None else "--",
                f"{oc.delta * u:+,.0f} units" if oc.delta is not None else None,
                delta_color="off", delta_arrow="off",
                help="Premium change per 1-point move in the underlying.")
    m[1].metric("Time decay / day",
                f"₹{oc.theta_day * u:,.0f}" if oc.theta_day is not None else "--",
                help="What the position loses per day from time alone, all else equal.")
    m[2].metric("Per vol point", f"₹{oc.vega * u:,.0f}" if oc.vega is not None else "--",
                help="Change in value if implied volatility moves 1 point.")
    m[3].metric("IV (this strike)", f"{oc.iv:.1f}%" if oc.iv else "--")
    m[4].metric("Spread", f"{oc.spread_pct:.1f}%" if oc.spread_pct is not None else "--",
                f"OI {oc.oi:,.0f} · vol {oc.volume:,.0f}", delta_color="off", delta_arrow="off")
    st.altair_chart(_payoff_chart(oc, v), width="stretch")
    st.dataframe(pd.DataFrame([{"if expiry close is": lbl, "level": round(lvl, 2),
                                "P&L (₹)": round(pl * oc.lots)} for lbl, lvl, pl in oc.payoff]),
                 hide_index=True, width="content")
    for n in oc.notes:
        st.caption("• " + n)
    st.caption("Decision support, not investment advice. Whether to trade, and which "
               "way, is yours to decide.")


def _px(x) -> str:
    if x is None or pd.isna(x):
        return "--"
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:,.2f}"


def render() -> None:
    st.subheader("F&O: where option writers have put their money")
    st.caption(
        "The range is spot ± the ATM straddle -- the move the market is pricing -- "
        "with how often the expiry close actually landed inside it (stocks since "
        "2024, indices since 2019). "
        "Walls, PCR and max pain show where writers are positioned; tested on "
        "~14,000 expiries, none of them predicted where price ended up "
        "(`docs/f_o/validation.md`).")

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
        f"### {v.symbol} &nbsp; {c.pill(v.bias.upper() + ' positioning', BIAS_TONE[v.bias])}",
        unsafe_allow_html=True)
    st.caption(f"Expiry {v.expiry:%d-%b-%Y}, {v.sessions} sessions left · "
               f"NSE data as of {v.timestamp:%d-%b %H:%M}"
               + (f" · lot {v.lot}" if v.lot else "") + " · OI in contracts")
    for w in v.warnings:
        st.warning(w)

    norms = "stock" if v.kind == "equity" else "index"
    st.markdown("**Range to expiry**")
    m = st.columns(5)
    m[0].metric("Spot", _px(v.spot))
    m[1].metric("Range low", _px(v.range_low),
                f"spot − {v.range_from}" if v.range_from else None,
                delta_color="off", delta_arrow="off")
    m[2].metric("Range high", _px(v.range_high),
                f"spot + {v.range_from}" if v.range_from else None,
                delta_color="off", delta_arrow="off")
    m[3].metric("Held since " + ("2024" if v.kind == "equity" else "2019"),
                f"~{v.range_hit_pct:.0f}%" if v.range_hit_pct is not None else "--",
                f"{'stocks' if v.kind == 'equity' else 'indices'}, this far out",
                delta_color="off", delta_arrow="off",
                help="Share of expiry closes inside spot ± ATM straddle, measured "
                     "5/10/20 sessions before monthly expiries, Jan 2024 - Sep 2026.")
    m[4].metric("Expected move", f"±{_px(v.straddle)}" if v.straddle else "--",
                f"±{_px(v.iv_move)} by IV ({v.atm_iv:.1f}%)" if v.iv_move else None,
                delta_color="off", delta_arrow="off",
                help="ATM call + put premium (bid/ask mid): what the market charges "
                     "for a move either way.")

    if v.conditional:
        st.caption(v.conditional)
    if v.vol:
        _vol_row(v.vol)

    st.markdown("**Positioning** &nbsp; " + c.pill("no measured edge", "neutral"),
                unsafe_allow_html=True)
    m = st.columns(6)
    m[0].metric("Put wall", _px(v.support.strike) if v.support else "--",
                _reach_caption(v.support_reach),
                delta_color="off", delta_arrow="off",
                help="Largest put OI below spot, read as support. Held no more often "
                     "than any level the same distance away. 'reached' = how often a "
                     "move this size closed at or below it before expiry.")
    m[1].metric("Call wall", _px(v.resistance.strike) if v.resistance else "--",
                _reach_caption(v.resistance_reach),
                delta_color="off", delta_arrow="off",
                help="Largest call OI above spot, read as resistance. Same caveat. "
                     "'reached' = how often a move this size closed at or above it "
                     "before expiry.")
    m[2].metric("PCR (OI)", f"{v.pcr['oi']:.2f}" if v.pcr["oi"] else "--",
                f"{v.pcr_bias} ({norms})", delta_color="off", delta_arrow="off",
                help="Stocks run far lower PCR than indices, so each is read against "
                     "its own norms. Did not predict direction.")
    m[3].metric("PCR (today's ΔOI)",
                f"{v.pcr['chg_oi']:.2f}" if v.pcr["chg_oi"] else "--",
                v.chg_pcr_bias, delta_color="off", delta_arrow="off",
                help="Put/call ratio of OI added today. Blank when a side is "
                     "unwinding -- a ratio of a gain and a loss means nothing. "
                     "Untested: the history is end-of-day only.")
    m[4].metric("Max pain", _px(v.max_pain),
                help="Historically a worse guess for the expiry close than today's price.")
    m[5].metric("Futures", _px(v.futures.price) if v.futures else "--",
                (v.futures.buildup or f"{v.futures.basis:+,.2f} basis") if v.futures else None,
                delta_color="off", delta_arrow="off")

    st.markdown("**Reach a level** &nbsp; " + c.pill("not a forecast of direction", "neutral"),
                unsafe_allow_html=True)
    lc, r0, r1, r2, r3 = st.columns([1.2, 1, 1, 1, 1])
    default = v.resistance.strike if v.resistance else round(v.spot * 1.05, 2)
    level = lc.number_input("Price (target or stop)", min_value=0.0, value=float(default),
                            step=float(max(round(v.spot * 0.005, 0), 0.5)),
                            key=f"fno_level_{v.symbol}_{v.expiry}")
    x = fno_report.reaches(v, [level])
    x = x[0] if x else None
    if x is None:
        lc.caption("Needs an ATM straddle and a price away from spot.")
    else:
        r0.metric("Traded there", _pct(x.hist_touch_intraday),
                  f"{x.distance_pct:+.1f}% · {x.moves:.1f} moves", delta_color="off",
                  delta_arrow="off",
                  help="Share of past cases where a move this size (in ATM straddles) "
                       "traded at or beyond the level on some day before expiry -- "
                       "the day's high/low (estimated from the near future for "
                       "stocks). What a stop or limit order would have met. Up and "
                       "down pooled.")
        r1.metric("Closed there", _pct(x.hist_touch),
                  help="Same, but only counting daily closes at or beyond it.")
        r2.metric("Beyond it at expiry", _pct(x.hist_expiry),
                  help="Share of past cases where the expiry close was at or beyond it.")
        r3.metric("Model (IV)", _pct(x.model_expiry), "beyond at expiry",
                  delta_color="off", delta_arrow="off",
                  help="Same question from the straddle-implied normal distribution. "
                       "Out of sample it ran 2-4 points high for stocks.")
        if x.n:
            lc.caption(f"From {x.n:,} past {'stock' if v.kind == 'equity' else 'index'} "
                       "observations, 5-10 sessions before monthly expiries."
                       + (" For indices the model column has been better calibrated: "
                          "the historical ones ran 4-8 points high out of sample."
                          if v.kind == "index" else ""))

    _option_panel(v, res)

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
