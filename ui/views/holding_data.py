"""Portfolio › Holding data: the figures behind each holding, with provenance.

Coverage first (what is on file for every holding, how fresh, what failed),
then one holding in detail: key figures, the fiscal-year table, recent
quarters, and every value's source, period, method and confidence. Missing
and not-applicable values are shown as such, never as zero.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from collections import defaultdict
from pathlib import Path

import pandas as pd
import streamlit as st

from book import marks as mk
from book import portfolio as pf
from book import research_db as rdb
from book import securities as sec
from data import financials as fin
from ui import service, state

ROOT = Path(__file__).resolve().parents[2]
REFRESH = "Refresh holding data (filings, statements, market stats)"

KEY = [("market_cap", "Market cap", "₹ cr"), ("close_adjusted", "Close", "₹"),
       ("high_52w", "52-week high", "₹"), ("low_52w", "52-week low", "₹"),
       ("beta_1y", "Beta (1y)", ""), ("volatility_1y_pct", "Volatility (1y)", "%"),
       ("avg_volume_20d", "Avg volume (20d)", ""), ("return_1y_pct", "Return 1y", "%"),
       ("return_3y_cagr_pct", "Return 3y CAGR", "%"),
       ("return_5y_cagr_pct", "Return 5y CAGR", "%"),
       ("dividend_yield_pct", "Dividend yield", "%"),
       ("distribution_yield_pct", "Distribution yield", "%"),
       ("dps_ttm", "Dividend / distribution, 12m", "₹"),
       ("promoter_pct", "Promoter holding", "%"),
       ("non_promoter_pct", "Non-promoter (≈ free float)", "%"),
       ("promoter_change_pct", "Promoter change", "pts")]
TTM = [("revenue", "Revenue (TTM)"), ("pat", "PAT (TTM)"), ("eps", "EPS (TTM)")]
ANNUAL = ["revenue", "ebitda", "ebit", "pat", "eps", "equity", "debt", "net_debt", "cash",
          "working_capital", "cfo", "capex", "fcf", "cfi", "cff",
          "revenue_growth_pct", "pat_growth_pct", "eps_growth_pct",
          "ebitda_margin_pct", "net_margin_pct", "roe_pct", "roce_pct", "debt_equity",
          "interest_cover", "current_ratio", "cash_conversion"]
CAGR = ["revenue_cagr_3y_pct", "revenue_cagr_5y_pct", "pat_cagr_3y_pct",
        "pat_cagr_5y_pct", "eps_cagr_3y_pct", "eps_cagr_5y_pct"]
QUARTER = ["revenue", "ebitda", "pat", "eps", "ebitda_margin_pct", "net_margin_pct",
           "gnpa_pct", "nnpa_pct"]
SOURCE_RANK = {fin.NSE: 0, fin.CALC: 1, fin.YAHOO: 2}
MARK = {"fresh": "", "stale": " ⏳", "missing": "—", "n/a": "n/a"}


def _latest_session() -> str | None:
    db = ROOT / "data" / "market.db"
    if not db.exists():
        return None
    con = sqlite3.connect(db)
    try:
        s = mk.sessions(con, 1)
        return s[0] if s else None
    finally:
        con.close()


@st.cache_data(ttl=300, show_spinner=False)
def _load() -> tuple[list[dict], dict, dict, str | None]:
    holdings = pf.read_csv_holdings()
    con = rdb.connect()
    try:
        rows = {h["tradingsymbol"]: rdb.metrics(con, _key(h)) for h in holdings}
        logs = defaultdict(list)
        for r in rdb.last_retrievals(con):
            logs[r["symbol"]].append(r)
    finally:
        con.close()
    return holdings, rows, dict(logs), _latest_session()


def _key(h: dict) -> str:
    s = sec.classify(h["tradingsymbol"], h.get("isin") or "")
    return s.base if s.kind == sec.EQUITY else s.symbol


def _fmt(v, unit: str = "") -> str:
    if v is None:
        return "—"
    f = float(v)
    if unit in ("%", "pts"):
        return f"{f:+.1f}%" if unit == "pts" else f"{f:.1f}%"
    if abs(f) >= 1000:
        return f"{f:,.0f}"
    return f"{f:,.2f}"


def _best(rows: list[dict], metric: str, period: str | None = None) -> dict | None:
    cands = [r for r in rows if r["metric"] == metric
             and (period is None or r["period"] == period)]
    if not cands:
        return None
    return max(cands, key=lambda r: (r["period_end"], -SOURCE_RANK.get(r["source"], 9)))


def render() -> None:
    top = st.columns([6, 2])
    with top[0]:
        state.page_title("Holding data", "Financials and market figures for each "
                         "holding, with where every number came from")
    if top[1].button("Refresh data", width="stretch", key="hd_refresh",
                     help="Runs scripts/refresh_holding_data.py: NSE filings, Yahoo "
                          "statements, market stats. A few minutes for a full book."):
        state.run_and_reload(REFRESH, service.SCRIPTS[REFRESH])
    holdings, rows, logs, session = _load()
    if not holdings:
        st.info("No holdings yet: see Portfolio › Import holdings.")
        return
    today = dt.date.today()
    if not any(rows.values()):
        st.info("No holding data yet. Press **Refresh data** (or run "
                "`scripts/refresh_holding_data.py`).")
        return

    _coverage(holdings, rows, logs, session, today)
    st.divider()
    names = [h["tradingsymbol"] for h in holdings]
    pick = st.selectbox("Holding", names, key="hd_pick")
    h = next(x for x in holdings if x["tradingsymbol"] == pick)
    _detail(h, rows.get(pick, []), logs.get(_key(h), []), session, today)


def _coverage(holdings, rows, logs, session, today) -> None:
    out = []
    for h in holdings:
        rs = rows.get(h["tradingsymbol"], [])
        s = sec.classify(h["tradingsymbol"], h.get("isin") or "")
        q = sorted({r["period_end"] for r in rs if r["period"] == "quarter"
                    and r["source"] == fin.NSE})
        fys = sorted({r["period_end"] for r in rs if r["period"] == "fy"
                      and r["status"] == fin.OK})
        pt = max((r["period_end"] for r in rs if r["period"] == "point"
                  and r["status"] == fin.OK), default=None)
        states = [fin.freshness(r, today, session)[0] for r in fin.latest_rows(rs)]
        lg = logs.get(_key(h), [])
        failed = [f"{r['dataset']} ({r['source']})" for r in lg if r["status"] == "failed"]
        mism = [r for r in lg if r["status"] == "mismatch"]
        out.append({
            "Holding": h["tradingsymbol"], "Type": s.label,
            "Quarters on file": len(q), "Latest quarter": q[-1] if q else "—",
            "Fiscal years": f"{fys[0][:4]}–{fys[-1][:4]}" if fys else "—",
            "Market stats as of": pt or "—",
            "Fresh": states.count("fresh"), "Stale": states.count("stale"),
            "Missing": states.count("missing"),
            "Retrieved": max((r["at"][:16] for r in lg), default="never"),
            "Failed": ", ".join(failed), "NSE vs Yahoo": f"{len(mism)} differ" if mism else ""})
    st.markdown("**Coverage**")
    st.caption("Fresh / stale / missing count the latest value of each series "
               "(older periods are history). Stale: a newer period should exist, or "
               "it hasn't been re-checked for two weeks. Missing: looked for, not "
               "found -- never shown as zero.")
    st.dataframe(pd.DataFrame(out), hide_index=True, width="stretch")


def _detail(h, rs, lg, session, today) -> None:
    if not rs:
        st.info("Nothing on file for this holding yet.")
        return
    s = sec.classify(h["tradingsymbol"], h.get("isin") or "")
    st.markdown(f"### {h['tradingsymbol']} · {s.label}")

    key = []
    for metric, label, unit in KEY + [(m, l, "₹ cr" if m != "eps" else "₹") for m, l in TTM]:
        r = _best(rs, metric, "ttm" if (metric, label) in TTM else None)
        if r is None:
            continue
        fr, why = fin.freshness(r, today, session)
        key.append({"Figure": label, "Value": _fmt(r["value"], unit) + MARK.get(fr, ""),
                    "Unit": r["unit"], "Period": f"{r['period']} {r['period_end']}",
                    "Source": r["source"], "Method": r["method"],
                    "Confidence": r["confidence"], "Freshness": f"{fr}: {why}",
                    "Note": r["note"]})
    if key:
        st.markdown("**Key figures**")
        st.dataframe(pd.DataFrame(key), hide_index=True, width="stretch")

    if s.kind in (sec.REIT, sec.INVIT):
        pend = [r["metric"] for r in rs if r["source"] == "manual input"]
        if pend:
            st.caption("Pending manual input (the trust's own reports): "
                       + ", ".join(pend) + ".")

    annual = defaultdict(dict)
    for r in rs:
        if r["metric"] in ANNUAL and r["period"] in ("fy", "instant") \
                and r["period_end"][5:] == "03-31":
            cur = annual[r["metric"]].get(r["period_end"])
            if cur is None or SOURCE_RANK.get(r["source"], 9) < SOURCE_RANK.get(cur["source"], 9):
                annual[r["metric"]][r["period_end"]] = r
    if annual:
        years = sorted({e for m in annual.values() for e in m})[-6:]
        tbl = []
        for m in ANNUAL:
            if m not in annual:
                continue
            line = {"Metric": m}
            for y in years:
                r = annual[m].get(y)
                tag = "" if r is None else {fin.NSE: "", fin.CALC: "",
                                            fin.YAHOO: " ʸ"}.get(r["source"], "")
                line[f"FY{y[:4]}"] = ("n/a" if r and r["status"] == fin.NA else
                                      (_fmt(r["value"], r["unit"]) + tag) if r else "")
            tbl.append(line)
        st.markdown("**Fiscal years** (₹ cr unless marked; ʸ = Yahoo, otherwise NSE "
                    "filings or calculated from them)")
        st.dataframe(pd.DataFrame(tbl), hide_index=True, width="stretch")
    cg = [r for r in rs if r["metric"] in CAGR]
    if cg:
        st.markdown("**Growth over 3 and 5 years**")
        st.dataframe(pd.DataFrame([{
            "Metric": r["metric"],
            "Value": "n/a" if r["status"] == fin.NA else _fmt(r["value"], "%"),
            "Confidence": r["confidence"], "Note": r["note"]}
            for r in sorted(cg, key=lambda r: r["metric"])]),
            hide_index=True, width="stretch")

    quarters = sorted({r["period_end"] for r in rs if r["period"] == "quarter"})[-8:]
    if quarters:
        tbl = []
        for m in QUARTER:
            got = {r["period_end"]: r for r in rs if r["metric"] == m
                   and r["period"] == "quarter"}
            if not got:
                continue
            tbl.append({"Metric": m, **{q: _fmt(got[q]["value"], got[q]["unit"])
                                        if q in got else "" for q in quarters}})
        st.markdown("**Recent quarters** (NSE filings)")
        st.dataframe(pd.DataFrame(tbl), hide_index=True, width="stretch")

    latest = {id(r) for r in fin.latest_rows(rs)}
    with st.expander(f"Every value with its provenance ({len(rs)})"):
        st.dataframe(pd.DataFrame([{
            "Metric": r["metric"], "Period": r["period"], "Ends": r["period_end"],
            "Value": None if r["value"] is None else float(r["value"]),
            "Unit": r["unit"], "Status": r["status"], "Source": r["source"],
            "Basis": r["basis"], "Method": r["method"], "Confidence": r["confidence"],
            "Freshness": fin.freshness(r, today, session)[0] if id(r) in latest
            else "history",
            "Retrieved": r["retrieved_at"], "Note": r["note"], "Reference": r["ref"]}
            for r in rs]), hide_index=True, width="stretch",
            column_config={"Reference": st.column_config.LinkColumn()})
    if lg:
        with st.expander("Last retrieval per source"):
            st.dataframe(pd.DataFrame([{"When": r["at"], "Source": r["source"],
                                        "Dataset": r["dataset"], "Status": r["status"],
                                        "Rows": r["rows"], "Message": r["message"]}
                                       for r in lg]), hide_index=True, width="stretch")
