"""Results tab: this season's quarterly results for the stocks you follow.

Reads what run_results.py stored (data/results/); the Check button runs it.
Nothing here fetches on page load except NSE's results calendar, which is
cached for half a day and shared with the Swing tab.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from results import coverage, engine, reaction as rx, store
from results import scorecard as sc
from ui import components as c

VERDICT_TONE = {"strong": "good", "in line": "neutral", "weak": "bad"}
SOURCES = ["holding", "watch", "swing", "momentum"]


@st.cache_data(ttl=600, show_spinner=False)
def _coverage() -> dict[str, list[str]]:
    return coverage.symbols()


@st.cache_data(ttl=3600, show_spinner=False)
def _upcoming(symbols: tuple[str, ...], today: dt.date) -> dict:
    return engine.upcoming(list(symbols), today, log=lambda *_: None)


def render(run_script=None) -> None:
    """run_script(label, args) runs a script with live output and reloads."""
    today = dt.date.today()
    want = engine.expected_quarter(today)
    st.subheader("Quarterly results")
    st.caption(
        "Your holdings, the watch list and the latest swing and momentum picks, "
        "scored from NSE's own filings. Each verdict compares the company with "
        "**its own recent quarters**, not with analyst estimates, which no free "
        "source carries. The price reaction is how the market took it. "
        "Information only: the verdict feeds no ranking.")

    cov = _coverage()
    status = engine.status(cov, today)
    upcoming = _upcoming(tuple(cov), today)

    top = st.columns([1, 1, 1, 1, 1.3])
    cur = status[status["current"]]
    top[0].metric("Reported", f"{len(cur)}", f"of {len(status)} followed",
                  delta_color="off", delta_arrow="off",
                  help=f"Filed results for the quarter ended {want:%d %b %Y}")
    for i, v in enumerate(("strong", "in line", "weak"), start=1):
        top[i].metric(v.capitalize(), f"{int((cur.get('verdict') == v).sum())}"
                      if not cur.empty else "0")
    top[4].write("")
    if run_script and top[4].button("Check for new results", type="primary",
                                    width="stretch", key="results_run",
                                    help="Runs run_results.py: scores new filings "
                                         "and updates price reactions."):
        run_script("Check quarterly results", ["run_results.py"])

    f1, f2 = st.columns([3, 2])
    picked = f1.pills("Followed because", SOURCES, selection_mode="multi",
                      default=SOURCES, key="results_src")
    only_now = f2.toggle(f"Only this season ({want:%b %Y} quarter)", value=True,
                         key="results_now")
    view = status[status["sources"].apply(
        lambda s: any(x in s.split(", ") for x in (picked or SOURCES)))]
    if only_now:
        view = view[view["current"]]

    if view.empty or "verdict" not in view:
        st.info("Nothing reported yet for this selection. Press **Check for new "
                "results** after a company files; the daily job also runs it.")
    else:
        _table(view)
        _detail(view)

    _coming_up(status, upcoming, today)
    _backtest()


def _backtest() -> None:
    """What run_results_backtest.py found, from config/results_calibration.json."""
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "config" / "results_calibration.json"
    with st.expander("Has the verdict predicted anything? (backtest)"):
        if not path.exists():
            st.caption("No backtest yet. Run `run_results_backtest.py` (about an hour "
                       "the first time: it reads ~3,000 filings).")
            return
        cal = json.loads(path.read_text())
        q = cal["quarters"]
        st.caption(f"Tested {cal['tested_on']}: {cal['events']} filings from "
                   f"{cal['stocks']} Nifty 200 stocks, quarters {q[0]} to {q[-1]}. "
                   "Entry at the close of the reaction day; returns less the NIFTY 500.")
        (st.success if cal["verdict_predicts"] else st.warning)(
            "The verdict has predicted 60-session excess returns."
            if cal["verdict_predicts"] else
            "No reliable edge: a strong verdict has not gone on to beat a weak one "
            "once the reaction is known. Read it; don't trade on it alone.")
        for h, r in cal["horizons"].items():
            st.markdown(f"**{h} sessions on** -- strong minus weak, per quarter: "
                        f"{r['verdict_spread_mean']}% (t = {r['verdict_spread_t']}, "
                        f"better in {r['verdict_spread_positive_quarters']} of "
                        f"{r['verdict_spread_quarters']} quarters)")
            st.dataframe(pd.DataFrame(r["by_verdict"]).rename(columns={
                "verdict": "Verdict", "n": "Filings", "mean": "Mean excess %",
                "median": "Median %", "hit_pct": "Beat index %"}),
                hide_index=True, width="stretch")


def _num(r, k):
    v = r.get(k)
    return None if v is None or v != v else v


def _table(view: pd.DataFrame) -> None:
    v = view.sort_values("broadcast", ascending=False).copy()
    is_lender = v["kind"].isin(["bank", "nbfc"])
    # One "growth" set of columns for both: revenue for companies, NII for
    # lenders -- labelled per row in the Kind column, so the table stays narrow.
    v["Top line YoY"] = v["nii_yoy"].where(is_lender, v.get("revenue_yoy")) \
        if "nii_yoy" in v else v.get("revenue_yoy")
    v["Margin / GNPA"] = [
        (f"GNPA {r['gnpa']:.2f}%" if _num(r, "gnpa") is not None else "")
        if r["kind"] in ("bank", "nbfc") else
        (f"{r['margin']:.1f}% ({r['margin_yoy_bps']:+.0f}bps)"
         if _num(r, "margin") is not None and _num(r, "margin_yoy_bps") is not None
         else f"{r['margin']:.1f}%" if _num(r, "margin") is not None else "")
        for _, r in v.iterrows()]
    v["Reaction"] = [
        f"{r['day_pct']:+.1f}% ({r['day_atr']:+.1f} ATR)"
        if _num(r, "day_pct") is not None and _num(r, "day_atr") is not None
        else ("waiting for prices" if _num(r, "day_pct") is None else f"{r['day_pct']:+.1f}%")
        for _, r in v.iterrows()]
    cols = {"symbol": "Stock", "sources": "Followed", "quarter": "Quarter",
            "broadcast": "Filed", "verdict": "Verdict", "kind": "Kind",
            "Top line YoY": "Top line YoY", "pat_yoy": "PAT YoY",
            "Margin / GNPA": "Margin / GNPA", "Reaction": "Reaction",
            "d5_excess_pct": "5d vs index", "flags": "Flags"}
    for k in cols:
        if k not in v:
            v[k] = None
    st.dataframe(
        v[list(cols)].rename(columns=cols).fillna({"Flags": ""}),
        hide_index=True, width="stretch", height=min(38 + 35 * len(v), 720),
        column_config={
            "Filed": st.column_config.DatetimeColumn(format="DD MMM HH:mm"),
            "Top line YoY": st.column_config.NumberColumn(
                format="%+.1f%%", help="Revenue for companies, net interest "
                                       "income for banks and NBFCs"),
            "PAT YoY": st.column_config.NumberColumn(
                format="%+.1f%%", help="Blank when either year was a loss"),
            "Reaction": st.column_config.TextColumn(
                help="Reaction-day close vs the close before it, and in multiples "
                     "of the stock's daily ATR. After-hours filings react next "
                     "session."),
            "5d vs index": st.column_config.NumberColumn(
                format="%+.1f%%", help="Five sessions on, less the NIFTY 500"),
        })


def _detail(view: pd.DataFrame) -> None:
    st.markdown("**One stock in detail**")
    pick = st.selectbox("Stock", view.sort_values("broadcast", ascending=False)
                        ["symbol"].tolist(), key="results_pick")
    r = view[view["symbol"] == pick].iloc[0].to_dict()
    st.markdown(f"**{pick}** &nbsp; {r['quarter']} &nbsp; "
                f"{c.pill(r['verdict'], VERDICT_TONE.get(r['verdict'], 'neutral'))}"
                f" &nbsp; <span style='opacity:.75'>{r.get('points_detail') or ''}"
                f" · {r.get('basis', '')}</span>", unsafe_allow_html=True)
    st.write(sc.headline(r))
    if isinstance(r.get("flags"), str) and r["flags"]:
        for f in r["flags"].split("; "):
            st.markdown(f"- ⚠️ {f}")
    if (read := rx.read(r["verdict"], r.get("reaction"))):
        st.info(read)

    q = store.quarters()
    q = q[q["symbol"] == pick].sort_values("to_date")
    if q.empty:
        return
    lines = pd.DataFrame([
        {"Quarter": row["quarter"], **(sc.lender_lines(row) if r["kind"] in ("bank", "nbfc")
                                       else sc.company_lines(row))}
        for row in (engine._clean(x) for x in q.to_dict("records"))])
    top = "nii" if r["kind"] in ("bank", "nbfc") else "revenue"
    label = "Net interest income" if top == "nii" else "Revenue"
    left, right = st.columns([2, 3])
    with left:
        st.caption(f"{label}, ₹ crore, by quarter")
        st.bar_chart(lines.set_index("Quarter")[[top]].rename(columns={top: label}),
                     height=240)
    with right:
        keep = (["Quarter", "nii", "ppop", "provisions", "pat", "credit_cost", "gnpa"]
                if top == "nii" else
                ["Quarter", "revenue", "ebitda", "margin", "core_pbt", "pat", "eps"])
        names = {"nii": "NII", "ppop": "Pre-prov. profit", "provisions": "Provisions",
                 "pat": "PAT", "credit_cost": "Credit cost %", "gnpa": "GNPA %",
                 "revenue": "Revenue", "ebitda": "EBITDA", "margin": "EBITDA margin %",
                 "core_pbt": "Core PBT", "eps": "EPS"}
        st.caption("₹ crore unless marked; newest first")
        st.dataframe(lines[keep].iloc[::-1].rename(columns=names), hide_index=True,
                     width="stretch",
                     column_config={names[k]: st.column_config.NumberColumn(format="%.1f")
                                    for k in keep[1:]})


def _coming_up(status: pd.DataFrame, upcoming: dict, today: dt.date) -> None:
    done = set(status.loc[status["current"], "symbol"])
    soon = sorted((d, s) for s, d in upcoming.items() if s not in done)
    st.markdown("**Coming up**")
    if not soon:
        st.caption("No board meetings for results announced in the next three weeks.")
        return
    src = dict(zip(status["symbol"], status["sources"]))
    st.dataframe(pd.DataFrame([{"Date": d, "Stock": s, "Followed": src.get(s, ""),
                                "In": f"{(d - today).days} days"} for d, s in soon]),
                 hide_index=True, width="stretch",
                 column_config={"Date": st.column_config.DateColumn(format="ddd DD MMM")})
