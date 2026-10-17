"""Analyze tab: one stock, by the Fundamental Analyst, the Technical Analyst, or both.

Type a ticker, a company name or an ISIN and pick the analysis. Nothing runs
until Analyze is pressed; results are cached, so switching between the two
analysts or coming back later is instant.

A name that matches more than one company asks which one rather than
guessing (analysis/lookup.py).
"""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from analysis import fundamental as fa
from analysis import lookup as lk
from analysis import technical as ta
from ui import components as c

MODES = ["Both", "Fundamental", "Technical"]
STANCE_TONE = {"Strong": "good", "Sound": "good", "Mixed": "warn", "Weak": "bad",
               "Uptrend": "good", "Sideways": "warn", "Downtrend": "bad",
               "Not enough data": "neutral"}
TONE_MARK = {"pos": "🟢", "neg": "🔴", "neutral": "⚪"}


@st.cache_data(ttl=24 * 3600, show_spinner="Loading the NSE stock list...")
def _directory() -> pd.DataFrame:
    return lk.load_directory()


@st.cache_data(ttl=3600, show_spinner=False)
def _fundamental(symbol: str, sector: str, refresh: bool) -> fa.FundamentalReport:
    return fa.analyse_symbol(symbol, sector, refresh=refresh)


@st.cache_data(ttl=3600, show_spinner=False)
def _technical(symbol: str) -> ta.TechnicalReport:
    return ta.analyse_symbol(symbol)


def _table(rows: list[dict], cols: dict[str, tuple[str, str]]) -> None:
    """Plain table (st.table): st.dataframe renders zero-width in a tab that
    is not active at render time. `cols`: key -> (header, format)."""
    out = pd.DataFrame([{h: (f.format(r.get(k)) if r.get(k) is not None else "—")
                         for k, (h, f) in cols.items()} for r in rows])
    first = out.columns[0]
    st.table(out.set_index(first))


def _score_line(label: str, score, parts: dict) -> str:
    """'Quality 72/100 — growth 79 · governance 92', each part coloured."""
    if score is None:
        return f"{label} —"
    bits = [c.pill(f"{k} {v:.0f}", c.quality_tone(v)) for k, v in parts.items()
            if v is not None]
    return f"{label} **{score:.0f}/100** &nbsp; " + " ".join(bits)


# ------------------------------------------------------------- fundamental

def _render_fundamental(r: fa.FundamentalReport) -> None:
    st.markdown(f"#### 📊 Fundamental Analyst &nbsp; {c.pill(r.stance, STANCE_TONE.get(r.stance, 'neutral'))}",
                unsafe_allow_html=True)
    st.markdown(r.summary)
    if r.score is not None:
        st.markdown(_score_line("Quality", r.score, r.components), unsafe_allow_html=True)

    for f in r.red_flags:
        st.error(f"🚩 {f}")
    left, right = st.columns(2)
    with left:
        st.markdown("**Strengths**")
        st.markdown("\n".join(f"- {s}" for s in r.strengths) or "_None stand out._")
    with right:
        st.markdown("**Concerns**")
        st.markdown("\n".join(f"- {s}" for s in r.concerns) or "_None stand out._")

    shown = {k: v for k, v in r.metrics.items() if v != "—"}
    if shown:
        st.markdown("**Key figures** &nbsp; " + " &nbsp;·&nbsp; ".join(
            f"{k} **{v}**" for k, v in shown.items()))

    vals = {k: v for k, v in r.valuation.items() if v is not None}
    if vals:
        st.markdown("**Valuation** &nbsp; " + " &nbsp;·&nbsp; ".join(
            f"{k} **{v:,.0f}**" if "cap" in k else f"{k} **{v:g}**"
            for k, v in vals.items()))
        st.caption(r.valuation_note + " No free sector-P/E data exists, so this "
                   "is not a comparison with peers.")

    if r.quarters:
        st.markdown("**Quarterly results** (₹ crore, newest first)")
        _table(r.quarters, {"quarter": ("Quarter", "{}"),
                            "revenue_cr": ("Revenue", "{:,.0f}"),
                            "revenue_yoy_pct": ("Revenue YoY", "{:+.1f}%"),
                            "pat_cr": ("Profit", "{:,.0f}"),
                            "pat_yoy_pct": ("Profit YoY", "{:+.1f}%"),
                            "margin_pct": ("Net margin", "{:.1f}%")})

    if len(r.promoter_trend) > 1:
        st.markdown("**Promoter holding**")
        _table(r.promoter_trend, {"date": ("Quarter end", "{}"),
                                  "promoter_pct": ("Promoter %", "{:.2f}%")})

    for n in r.notes:
        st.caption(f"ℹ️ {n}")
    st.caption(f"Latest results: {r.as_of or '—'} · sources: {r.sources or '—'} · "
               f"fetched {r.fetched_at.replace('T', ' ') or '—'} (kept for a week)")


# --------------------------------------------------------------- technical

def _chart(r: ta.TechnicalReport) -> alt.Chart:
    d = r.chart.melt("date", ["close", "sma50", "sma200"], "series", "price").dropna()
    d["series"] = d["series"].map({"close": "Close", "sma50": "50 DMA", "sma200": "200 DMA"})
    lines = alt.Chart(d).mark_line().encode(
        x=alt.X("date:T", title=None),
        y=alt.Y("price:Q", title="₹", scale=alt.Scale(zero=False)),
        color=alt.Color("series:N", title=None,
                        scale=alt.Scale(domain=["Close", "50 DMA", "200 DMA"],
                                        range=["#2563eb", "#d97706", "#6b7280"])),
        strokeWidth=alt.condition(alt.datum.series == "Close", alt.value(2), alt.value(1.2)),
        tooltip=["date:T", "series:N", alt.Tooltip("price:Q", format=",.2f")])
    layers = [lines]
    # Fixed colours per layer, not a colour encoding: a second colour scale
    # would merge with the price lines' and recolour the levels.
    for lvls, colour in ((r.supports, "#16a34a"), (r.resistances, "#dc2626")):
        if lvls:
            lv = pd.DataFrame([{"price": l.price, "label": f"{l.kind}: {l.source}"}
                               for l in lvls])
            layers.append(alt.Chart(lv).mark_rule(strokeDash=[4, 3], color=colour).encode(
                y="price:Q", tooltip=["label:N", alt.Tooltip("price:Q", format=",.2f")]))
    return alt.layer(*layers).properties(height=320).configure_legend(orient="bottom")


def _render_technical(r: ta.TechnicalReport) -> None:
    head = r.stance + (f" · {r.qualifier}" if r.qualifier else "")
    st.markdown(f"#### 📈 Technical Analyst &nbsp; {c.pill(head, STANCE_TONE.get(r.stance, 'neutral'))}",
                unsafe_allow_html=True)
    st.markdown(r.summary)
    if r.chart is None:
        return

    st.markdown(f"Close **₹{r.close:,.2f}** &nbsp;·&nbsp; "
                + _score_line("Timing score", r.score, r.buckets), unsafe_allow_html=True)

    st.altair_chart(_chart(r), width="stretch")

    left, right = st.columns(2)
    with left:
        st.markdown("**Support** (nearest first)")
        st.markdown("\n".join(f"- ₹{l.price:,.2f} ({l.pct_away:+.1f}%) — {l.source}"
                              for l in r.supports) or "_None below in the past year._")
    with right:
        st.markdown("**Resistance** (nearest first)")
        st.markdown("\n".join(f"- ₹{l.price:,.2f} ({l.pct_away:+.1f}%) — {l.source}"
                              for l in r.resistances) or "_None above in the past year._")

    st.markdown("**Readings**")
    st.markdown("\n".join(f"- {TONE_MARK[t]} **{a}:** {txt}" for a, txt, t in r.signals))

    st.markdown("**Returns against the NIFTY 500**")
    _table(r.returns, {"period": ("Period", "{}"), "stock_pct": ("Stock", "{:+.1f}%"),
                       "benchmark_pct": ("NIFTY 500", "{:+.1f}%"),
                       "excess_pct": ("Difference", "{:+.1f}")})

    if r.setup:
        s = r.setup
        tone = "good" if s["eligible"] else "neutral"
        st.markdown(f"**Swing setup today** &nbsp; {c.pill(s['setup'], tone)}",
                    unsafe_allow_html=True)
        st.markdown(c.trade_plan(s["entry"], s["stop"], s["t1"], s["t2"], label="entry"),
                    unsafe_allow_html=True)
        st.caption(f"Stop {s['stop_pct']:.1f}% below entry. {s['note'].capitalize()}. "
                   "Rules and backtest on the Swing tab.")
    else:
        st.caption("No swing setup (pullback or breakout) today.")
    st.caption(f"Prices through {r.as_of}.")


# ------------------------------------------------------------------ render

def render() -> None:
    st.markdown("Analyse any NSE stock. Enter a **ticker**, a **company name** or "
                "an **ISIN**, then pick the analyst.")
    with st.form("analyze_form", border=False):
        q, mode_col, go = st.columns([3, 2, 1], vertical_alignment="bottom")
        query = q.text_input("Stock", placeholder="e.g. INFY, Infosys, INE009A01021",
                             key="an_query")
        mode = mode_col.radio("Analysis", MODES, horizontal=True, key="an_mode")
        submitted = go.form_submit_button("Analyze", type="primary", width="stretch")
        refresh = st.checkbox("Refetch fundamentals (ignore the week-old copy)",
                              key="an_refresh")
    if submitted:
        st.session_state["an_run"] = {"query": query, "mode": mode, "refresh": refresh}
        st.session_state.pop("an_pick", None)
    run = st.session_state.get("an_run")
    if not run or not run["query"].strip():
        st.caption("**Fundamental Analyst** — business quality from NSE filings "
                   "and Yahoo: growth, returns, balance sheet, cash conversion, "
                   "promoter holding and pledge, valuation, red flags.  \n"
                   "**Technical Analyst** — the chart: trend, momentum, volume and "
                   "delivery, support and resistance, relative strength, today's "
                   "swing setup.  \n"
                   "Both describe; neither forecasts. Decision support, not advice.")
        return

    d = _directory()
    res = lk.resolve(run["query"], d)
    listing = res.match
    if listing is None:
        if not res.candidates:
            st.warning(f"No NSE stock matches “{run['query']}”. Try the ticker "
                       "(e.g. TCS) or the ISIN.")
            return
        labels = [x.label() for x in res.candidates]
        pick = st.selectbox(f"More than one stock matches “{run['query']}” — which one?",
                            labels, index=None, placeholder="Choose a stock",
                            key="an_pick")
        if pick is None:
            return
        listing = res.candidates[labels.index(pick)]

    st.subheader(f"{listing.symbol} · {listing.name or 'name not on file'}")
    meta = [x for x in (listing.isin, listing.sector,
                        res.how if res.how.startswith("symbol (renamed") else "") if x]
    if meta:
        st.caption(" · ".join(meta))

    want_f = run["mode"] in ("Both", "Fundamental")
    want_t = run["mode"] in ("Both", "Technical")
    # Both: the two verdicts side by side up top, the full reports stacked
    # below. Side-by-side reports were too narrow to read on a laptop.
    verdicts = st.container() if want_f and want_t else None
    fund = tech = None
    if want_f:
        with st.container():
            with st.spinner("Reading filings (NSE, then Yahoo) — up to a minute "
                            "the first time, cached for a week after..."):
                try:
                    fund = _fundamental(listing.symbol, listing.sector, run["refresh"])
                except Exception as exc:
                    st.error(f"Fundamental analysis failed: {type(exc).__name__}: {exc}")
            if fund:
                _render_fundamental(fund)
    if want_t:
        if want_f:
            st.divider()
        with st.container():
            with st.spinner("Loading prices (fetching from NSE if this stock is new "
                            "here)..."):
                try:
                    tech = _technical(listing.symbol)
                except Exception as exc:
                    st.error(f"Technical analysis failed: {type(exc).__name__}: {exc}")
            if tech:
                _render_technical(tech)
    if verdicts is not None and fund and tech:
        with verdicts:
            a, b = st.columns(2)
            a.markdown(f"**Fundamental** &nbsp; {c.pill(fund.stance, STANCE_TONE.get(fund.stance, 'neutral'))}"
                       f"<br><span style='font-size:13px'>{fund.summary}</span>",
                       unsafe_allow_html=True)
            th = tech.stance + (f" · {tech.qualifier}" if tech.qualifier else "")
            b.markdown(f"**Technical** &nbsp; {c.pill(th, STANCE_TONE.get(tech.stance, 'neutral'))}"
                       f"<br><span style='font-size:13px'>{tech.summary}</span>",
                       unsafe_allow_html=True)
            st.divider()
    st.caption("_Rule-based description from free data. Neither analyst has "
               "been shown to predict returns. Decision support, not advice._")
