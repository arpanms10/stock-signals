"""GARP / QARP tab: two fundamental shortlists from NSE filings.

Computed on demand from data/results/universe_quarters.csv and the local
price store, cached for the session. 'Refresh filings' runs
scripts/run_picker.py --refresh; the backtest section reads
config/picker_calibration.json.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd
import streamlit as st

from picker import engine as pe
from picker import score as ps
from results import store
from ui import components as c

CALIBRATION = Path(__file__).resolve().parents[1] / "config" / "picker_calibration.json"


@st.cache_data(show_spinner="Scoring the universe on its filings...", ttl=3600)
def _scored(as_of: dt.date, universe: str) -> pd.DataFrame:
    from data import store as price_store
    cfg = pe.load_config()
    q = store.quarters(pe.UNIVERSE)
    if q.empty:
        return pd.DataFrame()
    u = pe.universe(universe)
    q = q[q["symbol"].isin(set(u["symbol"]))]
    return pe.evaluate(q, as_of, pe.Prices(price_store.connect()),
                       dict(zip(u["symbol"], u["sector"])), cfg)


def render(as_of: dt.date, held: set[str], run_script=None) -> None:
    cfg = pe.load_config()
    st.subheader("GARP & QARP: fundamental shortlists")
    c.evidence("none", "On a survivorship-free universe GARP beat the market by +0.35% a "
                       "quarter (t 0.3) and QARP lagged by 1.9% (t −1.8). Shortlists to "
                       "research, not ranked picks.")
    st.caption(
        "Two classic ways to buy good businesses without overpaying, applied to "
        f"the {cfg['universe'].replace('nifty', 'Nifty ')} using only NSE's own "
        "filings and the price on the day. **GARP** wants profit growing fast and "
        "lastingly at a P/E not far above that growth. **QARP** wants a business "
        "that earns well, steadily and in cash on little debt, when it is not near "
        "its own most expensive. A shortlist to research, not orders.")

    top = st.columns([3, 1.2])
    if run_script and top[1].button("Refresh filings", width="stretch", key="pick_refresh",
                                    help="Runs scripts/run_picker.py --refresh: lists every "
                                         "company's filings and reads any new ones "
                                         "(a few minutes)."):
        _scored.clear()
        run_script("Refresh GARP / QARP filings", ["scripts/run_picker.py", "--refresh"])

    scored = _scored(as_of, cfg["universe"])
    if scored.empty:
        st.info("No filings on file for the universe yet. Press **Refresh filings** "
                "(the first run reads ~4,800 filings and takes over an hour; after "
                "that only new quarters are fetched).")
        _method(cfg)
        return
    top[0].caption(f"Prices and filings as of **{as_of:%d %b %Y}** · {len(scored)} "
                   f"stocks scored · {int(scored['garp_ok'].sum())} pass GARP, "
                   f"{int(scored['qarp_ok'].sum())} pass QARP")
    _verdict()

    n = cfg["top_n"]
    tab_g, tab_q, tab_all, tab_m = st.tabs([f"GARP top {n}", f"QARP top {n}",
                                            "All scored", "Method & backtest"])
    pct = st.column_config.NumberColumn(format="%+.0f%%")
    common = {"symbol": "Stock", "sector": "Sector", "pe": "P/E", "core_pe": "Core P/E",
              "mcap_cr": "Mkt cap ₹cr", "held": "Held"}
    with tab_g:
        g = ps.picks(scored, "garp", n).assign(held=lambda d: d["symbol"].isin(held))
        st.dataframe(
            g[["symbol", "garp_score", "growth", "core_growth_1y", "core_growth_2y",
               "core_pe", "peg", "topline_growth", "consistency", "interest_cover",
               "sector", "held"]].rename(columns={
                   **common, "garp_score": "Score", "growth": "Growth used",
                   "core_growth_1y": "1y", "core_growth_2y": "2y (a yr)",
                   "peg": "PEG", "topline_growth": "Top line", "consistency": "Consistency",
                   "interest_cover": "Int. cover"}),
            hide_index=True, width="stretch",
            column_config={
                "Score": st.column_config.ProgressColumn(min_value=0, max_value=100,
                                                         format="%.0f"),
                "Growth used": st.column_config.NumberColumn(
                    format="%+.0f%%", help="Lower of 1-year and 2-year (annualised) "
                                           "TTM core profit growth"),
                "1y": pct, "2y (a yr)": pct, "Top line": pct,
                "PEG": st.column_config.NumberColumn(format="%.2f",
                                                     help="Core P/E / growth used"),
                "Core P/E": st.column_config.NumberColumn(
                    format="%.1f", help="Market cap / TTM profit less other income "
                                        "and one-offs"),
                "Consistency": st.column_config.NumberColumn(
                    format="percent", help="Share of the last 8 quarters with YoY "
                                          "top-line growth"),
            })
    with tab_q:
        qd = ps.picks(scored, "qarp", n).assign(held=lambda d: d["symbol"].isin(held))
        st.dataframe(
            qd[["symbol", "qarp_score", "quality", "roe", "roce", "cfo_pat", "de",
                "margin", "pe", "pe_hist_pct", "pe_vs_sector", "fcf_yield", "sector",
                "held"]].rename(columns={
                    **common, "qarp_score": "Score", "quality": "Quality", "roe": "ROE %",
                    "roce": "ROCE %", "cfo_pat": "CFO/PAT", "de": "D/E",
                    "margin": "EBITDA margin %", "pe_hist_pct": "P/E in own range",
                    "pe_vs_sector": "P/E vs sector", "fcf_yield": "FCF yield %"}),
            hide_index=True, width="stretch",
            column_config={
                "Score": st.column_config.ProgressColumn(min_value=0, max_value=100,
                                                         format="%.0f"),
                "Quality": st.column_config.ProgressColumn(
                    min_value=0, max_value=100, format="%.0f",
                    help="Percentile among peers (lenders ranked only against lenders)"),
                "P/E in own range": st.column_config.NumberColumn(
                    format="percent", help="Share of its last three years' quarterly "
                                           "P/Es below today's: 0% = cheapest it has been"),
                "P/E vs sector": st.column_config.NumberColumn(format="%.2fx"),
            })
    with tab_all:
        st.dataframe(scored.drop(columns=["as_of"], errors="ignore")
                     .sort_values("qarp_score", ascending=False),
                     hide_index=True, width="stretch", height=600)
    with tab_m:
        _method(cfg)
        _backtest()


def _verdict() -> None:
    if not CALIBRATION.exists():
        st.warning("No backtest yet -- run `scripts/run_picker.py --backtest` before trusting "
                   "either list.")
        return
    cal = json.loads(CALIBRATION.read_text())
    parts = []
    for m in ("garp", "qarp"):
        s = cal.get(m)
        if s and s.get("excess_t") is not None:
            parts.append(f"**{m.upper()}** {s['excess_mean']:+.1f}% a period vs the "
                         f"equal-weight universe (t = {s['excess_t']})")
    strong = any(((cal.get(m) or {}).get("excess_t") or 0) >= 2 for m in ("garp", "qarp"))
    (st.info if strong else st.warning)(
        f"Backtest {cal['from']} to {cal['to']}: " + "; ".join(parts)
        + ". See *Method & backtest*.")


def _method(cfg: dict) -> None:
    g, q = cfg["garp"], cfg["qarp"]
    st.markdown(f"""
**GARP** (Lynch) -- passes when the lower of 1-year and 2-year core profit growth is
at least **{g['min_growth']:.0f}%**, PEG (core P/E ÷ that growth) is at most
**{g['max_peg']}**, the top line is growing, interest cover is at least
**{g['min_interest_cover']:.0f}x** and debt/equity at most **{g['max_de']}**.
Ranked {g['w_growth']:.0%} on growth, {1 - g['w_growth']:.0%} on a low PEG.

**QARP** (Buffett / Munger, AQR's quality factor) -- passes when quality is in the
top **{100 - 100 * q['min_quality']:.0f}%**, profit is still growing, and the P/E is
not above **{q['max_pe_hist_pct']:.0%}** of its own last three years. Ranked
{q['w_quality']:.0%} on quality, {1 - q['w_quality']:.0%} on price (P/E against its
own history and its sector, FCF yield).

**Quality** for a company: ROE, ROCE, CFO/PAT, interest cover, debt/equity, EBITDA
margin and its stability, other income as a share of profit, top-line consistency.
For a bank or NBFC: ROE, ROA, gross NPA, credit cost, consistency -- ranked only
against other lenders. A missing measure is left out, never counted as average.

**Core profit** is PAT less other income, one-offs and discontinued operations,
after tax. Growth is in total, not per share, so bonus issues and splits cannot
fake a collapse.

**Point in time**: every number is what NSE had published on the date. The
balance sheet (ROE, ROCE, debt, cash flow) is only in filings from 2025, so
before that quality rests on what the P&L shows.
""")


def _backtest() -> None:
    if not CALIBRATION.exists():
        return
    cal = json.loads(CALIBRATION.read_text())
    st.markdown(f"**Backtest** {cal['from']} to {cal['to']}: {cal['periods']} "
                f"rebalances after each results season, top {cal['top_n']}, equal "
                f"weight, costs included. Tested {cal['tested_on']}.")
    rows = []
    for m, label in (("garp", "GARP"), ("qarp", "QARP"), ("universe", "Equal-weight universe"),
                     ("index", "NIFTY 500")):
        s = cal.get(m)
        if s:
            rows.append({"Strategy": label, "CAGR %": s["cagr"], "Total %": s["total"],
                         "Worst period %": s["worst_period"],
                         "vs universe / period %": s.get("excess_mean"),
                         "t": s.get("excess_t"), "Beat universe %": s.get("beat_universe_pct")})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption(f"Caveats: {cal.get('note', '')}. Results season returns cluster, so "
               "the t-statistic is across rebalance periods, not stocks.")
