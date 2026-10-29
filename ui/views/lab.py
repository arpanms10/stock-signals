"""Research › Strategy lab: every registered strategy, its tests and its
forward record, side by side.

Reads what research/run_lab.py and scripts/record_lab.py wrote:
config/lab_results.json (numbers), research/lab_ledger.json (what has
been run and which windows are spent) and history/lab/ (forward picks).
Nothing is computed here except the forward record's returns.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from ui import state


def _fmt(v, suffix=""):
    return "" if v is None or v != v else f"{v:g}{suffix}"


@st.cache_data(ttl=600, show_spinner=False)
def _forward_table(names: tuple[str, ...]) -> dict[str, list[dict]]:
    from backtest import pit_engine as pit
    from data import bhavcopy as bc
    from data import store
    from lab import forward
    from scoring import timing
    recs = {n: forward.read(n) for n in names}
    if not any(len(r) for r in recs.values()):
        return {}
    con = bc.connect(forward.ROOT / "data" / "market.db")
    first = min(pd.Timestamp(r["price_date"].min()) for r in recs.values() if len(r))
    closes = pit.load_wide(con, first.date(), dt.date.today(), pit.symbol_aliases(con))
    base = timing.load_config()
    idx = store.load_index(store.connect(), base["signals"]["regime_index"])
    bench = idx.set_index(pd.to_datetime(idx["date"]))["close"]
    return {n: forward.score(n, closes, bench).to_dict("records") for n in names
            if len(recs[n])}


def _summary(strategies, data, res) -> pd.DataFrame:
    from lab import ledger
    rows = []
    for s in strategies:
        r = res.get(s.name, {})
        tr, ho = r.get("train", {}), r.get("holdout", {})
        tm, hm = tr.get("metrics", {}), ho.get("metrics", {})
        mm = r.get("measure", {}).get("metrics", {})
        rows.append({
            "Strategy": s.name, "Where it stands": ledger.status(data, s),
            "Forward": "yes" if s.status == "forward" else "",
            "Train CAGR %": tm.get("cagr_pct"),
            "Train after tax %": tm.get("after_tax_liquidation_cagr_pct"),
            "Train vs NIFTY 500": tm.get("excess_cagr_pct"),
            "Holdout vs NIFTY 500": hm.get("excess_cagr_pct"),
            "2018-now CAGR %": mm.get("cagr_pct"),
            "2018-now after tax %": mm.get("after_tax_liquidation_cagr_pct"),
            "Deflated Sharpe": mm.get("deflated_sharpe", tm.get("deflated_sharpe")),
        })
    df = pd.DataFrame(rows)
    # Text, so a strategy not yet run shows a blank cell rather than "None".
    num = [c for c in df.columns if c not in ("Strategy", "Where it stands", "Forward")]
    df[num] = df[num].map(_fmt)
    return df


def _detail(name: str, run: dict) -> None:
    m, b = run.get("metrics", {}), run.get("benchmark", {})
    st.caption(f"{run.get('start')} .. {run.get('end')}, {run.get('universe')}, "
               f"run {run.get('run_on', '')}")
    c = st.columns(5)
    c[0].metric("CAGR", _fmt(m.get("cagr_pct"), "%"),
                f"{_fmt(m.get('excess_cagr_pct'))} pts vs NIFTY 500")
    c[1].metric("After tax", _fmt(m.get("after_tax_liquidation_cagr_pct"), "%"),
                help="Every year's capital-gains tax paid from the book, and "
                     "everything sold and taxed at the end.")
    c[2].metric("Worst fall", _fmt(m.get("max_drawdown_pct"), "%"))
    c[3].metric("vs random picks", _fmt(m.get("edge_vs_controls_pct"), " pts"),
                help=f"{_fmt(m.get('edge_vs_controls_sd'))} standard deviations of "
                     "the random-pick controls")
    c[4].metric("Deflated Sharpe", _fmt(m.get("deflated_sharpe")),
                help=f"After {run.get('trials', '?')} trials in the family: the "
                     "probability the edge over the index is real. Above 0.95 is "
                     "convincing.")
    st.caption(f"NIFTY 500 over the same window: {_fmt(b.get('cagr_pct'), '%')} a year, "
               f"{_fmt(b.get('after_tax_cagr_pct'), '%')} after tax held and sold once, "
               f"worst fall {_fmt(b.get('max_drawdown_pct'), '%')}. Random picks: "
               f"{_fmt(run.get('controls', {}).get('mean'), '%')}. "
               f"{run.get('trials', '?')} trial(s) in the family.")
    verdict = run.get("verdict")
    if verdict:
        lines = [f"{'✅' if ok else '❌'} `{chk}`" for chk, ok in verdict["checks"].items()]
        st.markdown(("**Passed** its rule: " if verdict["passed"] else
                     "**Failed** its rule: ") + " · ".join(lines))
    lo, hi = m.get("excess_range_low"), m.get("excess_range_high")
    if lo is not None:
        st.caption(f"Yearly return over the index, 90% range: {lo} to {hi} points. "
                   f"Halves: {m.get('first_half_excess_pct')} / "
                   f"{m.get('second_half_excess_pct')} points over the index.")
    curves = run.get("curves", {})
    if curves.get("strategy"):
        label = {"strategy": name, "after_tax": f"{name}, after tax",
                 "benchmark": "NIFTY 500", "universe_ew": "universe, equal weight"}
        df = pd.DataFrame({label[k]: pd.Series(v) for k, v in curves.items() if k in label})
        df.index = pd.to_datetime(df.index)
        df = df / df.iloc[0] * 100
        st.line_chart(df, y_label="growth of 100")
    if run.get("by_year"):
        st.dataframe(pd.DataFrame(run["by_year"]).set_index("year"), width="stretch")


def render() -> None:
    from lab import compare, ledger, registry, results
    from lab.rules import rule_for
    state.page_title("Strategy lab", "Every strategy tested the same way: the 200 "
                     "most liquid NSE names on each date, costs and tax, random "
                     "picks, a train window and one holdout")
    strategies = registry.all_strategies()
    data, res = ledger.load(), results.load()

    st.dataframe(_summary(strategies, data, res), hide_index=True, width="stretch")
    st.caption("Run tests with `research/run_lab.py` (see docs/lab.md). A holdout "
               "runs once, and only after train passes under a rule written "
               "beforehand. Once a family's holdout is spent, its variants can "
               "only be tracked forward.")

    name = st.selectbox("Strategy", [s.name for s in strategies], key="lab_pick")
    s = registry.get(name)
    rule = rule_for(name)
    st.markdown(f"**{name}** · family *{s.family}* · {s.summary}")
    if rule:
        st.caption(f"Rule fixed {rule.fixed_on}: {rule.note}. Train: "
                   + "; ".join(map(str, rule.train)) + ". Holdout: "
                   + "; ".join(map(str, rule.holdout)) + ".")
    runs = res.get(name, {})
    tabs = st.tabs(["Train", "Holdout", "2018 to now", "Ranks 201-500", "Forward"])
    for tab, split in zip(tabs[:4], ("train", "holdout", "measure", "robustness")):
        with tab:
            if split in runs:
                _detail(name, runs[split])
            else:
                st.info(f"No {split} run on this computer yet.")
    with tabs[4]:
        if s.status != "forward":
            st.info("Not tracked forward. Set status='forward' in lab/strategies.py.")
        else:
            fw = _forward_table((name,)).get(name)
            if not fw:
                st.info("No forward record yet: the Saturday job starts it.")
            else:
                t = pd.DataFrame(fw)
                st.dataframe(t, hide_index=True, width="stretch")
                done = t[~t["open"]]
                if len(done):
                    st.caption(f"{len(done)} closed months: on average "
                               f"{done['excess_pct'].mean():.2f} points a month over "
                               f"NIFTY 500, ahead in {(done['excess_pct'] > 0).sum()}.")

    with st.expander("Families and spent windows"):
        st.dataframe(pd.DataFrame([
            {"Family": f, "Holdout": (f"spent {v['holdout']['on']}: {v['holdout']['by']}"
                                      if v.get("holdout") else "unseen"),
             "Trials": v.get("trials", 0), "Also seen": "; ".join(v.get("seen", []))}
            for f, v in data["families"].items()]), hide_index=True, width="stretch")

    with st.expander("Compare and blend"):
        split = st.segmented_control("Window", ["train", "holdout", "measure"], default="train",
                                     key="lab_split") or "train"
        corr = compare.correlation(split, res)
        if corr.empty:
            st.info(f"No {split} results yet.")
        else:
            st.caption("Correlation of monthly returns. Lower means the two "
                       "smooth each other out when held together.")
            st.dataframe(corr, width="stretch")
            names = [c for c in corr.columns if c != "NIFTY 500"]
            if len(names) >= 2:
                c1, c2, c3 = st.columns([2, 2, 3])
                a = c1.selectbox("Blend", names, key="lab_a")
                b = c2.selectbox("with", [n for n in names if n != a], key="lab_b")
                w = c3.slider(f"Weight in {a}", 0.0, 1.0, 0.5, 0.1, key="lab_w")
                out = compare.blend(a, b, w, split, res)
                if out:
                    out.pop("correlation")
                    st.dataframe(pd.DataFrame(out).T, width="stretch")
                    st.caption("Rebalanced to the weights every month, before costs.")
