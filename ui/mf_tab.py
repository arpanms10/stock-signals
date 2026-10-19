"""Mutual funds: your MF book's verdicts (Portfolio › Mutual funds), and any
funds side by side (Research › Compare funds).

Reads config/mf_holdings.csv or .xlsx (percent of the MF book, no amounts) and
the NAV history in data/mf/mf.db (scripts/run_mf.py --refresh). Same engine as
scripts/run_mf.py.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st

from mf import engine, verdict
from ui import components as c

STATE_TONE = {"STRONG": "good", "OK": "neutral", "SLIPPING": "warn", "WEAK": "bad",
              "YOUNG": "info", "-": "neutral"}
EXAMPLE = Path(__file__).resolve().parents[1] / "config" / "mf_holdings.example.csv"


@st.cache_data(ttl=3600, show_spinner="Ranking funds against their categories...")
def _evaluate_cached(queries: tuple[str, ...], use_holdings: bool,
                     book_stamp: float | None) -> engine.Evaluation:
    return engine.evaluate(list(queries), use_holdings=use_holdings)


def _book_stamp() -> float | None:
    """When the holdings file last changed, so an edit shows on the next
    load instead of after the cache expires."""
    from mf import holdings
    try:
        p = holdings.find_file()
    except SystemExit:
        return -1.0
    return p.stat().st_mtime if p is not None else None


def _evaluate(queries: tuple[str, ...], use_holdings: bool = True) -> engine.Evaluation:
    return _evaluate_cached(queries, use_holdings, _book_stamp() if use_holdings else None)


def _calibration_line() -> str | None:
    from mf.backtest import calibration_line
    return calibration_line()


def _cat(k: str | None) -> str:
    # "equity:small cap" would be read by Streamlit's markdown as a :small[]
    # text directive and lose a word.
    return k.replace(":", " / ") if k else "?"


def _fmt(v, key: str) -> str:
    from scripts.run_mf import _fmt as fmt
    return fmt(v, key)


def _evidence() -> None:
    line = _calibration_line()
    if line:
        c.evidence("weak", line)
    else:
        st.warning("No backtest yet -- run it from Data and jobs (or `scripts/run_mf.py "
                   "--backtest`) to see what these states were worth historically.")


def _unresolved(ev: engine.Evaluation) -> None:
    for query, cands in ev.unresolved:
        opts = "; ".join(f"{name} ({code})" for code, name, _ in cands[:4])
        st.warning(f"'{query}' matches more than one fund: {opts}. Use more of the "
                   "name or the code.")


def _decisions(ev: engine.Evaluation) -> None:
    for d in ev.decisions:
        held = f"{d.weight:.0f}% of book" if d.weight is not None else "not held"
        target = (f" → {d.target:.0f}%" if d.target is not None and d.weight is not None else "")
        head = (f"{c.badge(d.action)} &nbsp; **{d.name}** &nbsp; "
                f"{c.pill(d.state, STATE_TONE.get(d.state, 'neutral'))} &nbsp; "
                f"{c.muted(f'{_cat(d.category)} · {held}{target}')}")
        st.markdown(head, unsafe_allow_html=True)
        bullets = [f"- {r}" for r in d.reasons] + [f"- ⚠️ {x}" for x in d.cautions]
        if d.alternatives:
            bullets.append(f"- Same category: {'; '.join(d.alternatives)}")
        st.markdown("\n".join(bullets))


def _compare(ev: engine.Evaluation) -> None:
    codes = [d.code for d in ev.decisions if d.code is not None]
    t = engine.compare_table(ev, codes)
    t.loc["Category"] = [_cat(k) for k in t.loc["Category"]]
    st.table(t.apply(lambda col: [_fmt(v, k) for k, v in col.items()]))


def _summary(ev: engine.Evaluation) -> None:
    """Category mix, the largest fund against its cap, and near-twin pairs."""
    book = ev.book.table
    rules = verdict.load_rules()
    by_code = {d.code: d for d in ev.decisions}
    mix: dict[str, float] = {}
    for r in book.itertuples():
        k = (by_code[r.code].category if r.code in by_code else None) or "other"
        group = k.split(":", 1)[0]
        group = group if group in ("equity", "hybrid", "debt") else "other"
        mix[group] = mix.get(group, 0.0) + float(r.weight)
    cap = rules["allocation"]["max_fund_pct"]
    big = book.loc[book["weight"].idxmax()]
    twins = 0
    if ev.corr is not None:
        cs = list(ev.corr.index)
        thr = rules["allocation"]["near_duplicate_corr"]
        twins = sum(1 for i, x in enumerate(cs) for y in cs[i + 1:]
                    if ev.corr.at[x, y] == ev.corr.at[x, y] and ev.corr.at[x, y] >= thr)
    m = st.columns(3)
    parts = sorted(mix.items(), key=lambda kv: -kv[1])
    m[0].metric("Mix", f"{parts[0][1]:.0f}% {parts[0][0]}",
                " · ".join(f"{k} {v:.0f}%" for k, v in parts[1:]) or None,
                delta_color="off", delta_arrow="off")
    over = big["weight"] > cap
    m[1].metric("Largest fund", f"{big['weight']:.0f}%",
                f"above the {cap}% cap" if over else f"within the {cap}% cap",
                delta_color="inverse" if over else "off", delta_arrow="off",
                help=str(big["name"]))
    m[2].metric("Funds that move as one", f"{twins}" if twins else "None",
                help="Pairs of your funds whose monthly returns correlate above "
                     f"{rules['allocation']['near_duplicate_corr']}")


def _workbook(ev: engine.Evaluation) -> bytes | None:
    from mf import evaluation_report
    try:
        with tempfile.TemporaryDirectory() as d:
            p = evaluation_report.build(Path(d) / "MF_Evaluation.xlsx", ev)
            return p.read_bytes()
    except Exception:          # noqa: BLE001 -- the page works without it
        return None


def _refresh_button(where, run_script) -> None:
    label = "Refresh fund NAVs"
    if run_script and where.button("Refresh NAVs", width="stretch", key="mf_refresh",
                                   help="Runs scripts/run_mf.py --refresh: downloads any month "
                                        "of AMFI NAVs not on file, then reloads."):
        from ui import service
        run_script(label, service.SCRIPTS[label])


def render_book(run_script=None) -> None:
    """Portfolio › Mutual funds."""
    from ui import state
    top = st.columns([6, 1.5, 1.5], vertical_alignment="bottom")
    try:
        ev = _evaluate((), True)
    except SystemExit as e:
        with top[0]:
            state.page_title("Mutual funds")
        _refresh_button(top[1], run_script)
        st.info(str(e))
        return
    newest = max(ev.nav_date.values()) if ev.nav_date else "?"
    with top[0]:
        state.page_title("Mutual funds", f"Month-end NAVs to {ev.as_of} · newest NAV {newest}")
    _refresh_button(top[1], run_script)
    if ev.book is not None and ev.decisions:
        data = _workbook(ev)
        if data:
            top[2].download_button("Workbook", data, "MF_Evaluation.xlsx",
                                   icon=":material/download:", width="stretch",
                                   key="mf_xlsx", help="The same workbook as "
                                   "run_mf.py --excel")
    state.refreshed_notice()
    _evidence()

    if ev.book is None:
        _no_book()
        return
    for w in ev.book.warnings:
        st.caption(f"Holdings: {w}")
    _unresolved(ev)
    if ev.book.table.empty:
        st.info("None of the funds in your holdings file could be matched.")
        return
    _summary(ev)
    _decisions(ev)
    with st.expander("Compare your funds side by side"):
        _compare(ev)


def _no_book() -> None:
    with st.container(border=True):
        st.markdown("#### Add your mutual fund book")
        st.markdown("Put `mf_holdings.xlsx` or `mf_holdings.csv` in the `config` folder. "
                    "Percentages only, no amounts.")
        st.table({"Fund": ["Parag Parikh Flexi Cap", "Kotak Emerging Equity"],
                  "Pct": [30, 20], "Held over 1y": ["yes", "no"]})
        st.markdown("- **Pct** is each fund's share of your whole MF portfolio; together "
                    "they make 100\n"
                    "- Names as you'd say them, old names too, or AMFI codes\n"
                    "- **Held over 1y** is optional; it only changes the exit-load warning")
        cols = st.columns([1, 1, 3])
        if cols[0].button("I've added it, reload", type="primary", key="mf_reload"):
            st.rerun()
        if EXAMPLE.exists():
            cols[1].download_button("Example file", EXAMPLE.read_bytes(),
                                    "mf_holdings.csv", key="mf_example")
        st.caption("No book yet? You can still compare any funds under Research › "
                   "Compare funds.")


def render_compare() -> None:
    """Research › Compare funds: any funds, judged as not held."""
    from ui import state
    state.page_title("Compare funds", "Direct Growth · month-end NAVs · each fund against "
                                      "its own category")
    if pref := st.session_state.pop("mf_compare_pref", None):
        st.session_state["mf_compare_q"] = pref
    q = st.text_input("Funds to compare (comma-separated names or AMFI codes)",
                      placeholder="parag parikh flexi cap, hdfc flexi cap", key="mf_compare_q")
    _evidence()
    queries = tuple(x.strip() for x in q.split(",") if x.strip())
    if not queries:
        st.caption("Type one or more fund names. Old names work too: “Axis Bluechip” "
                   "finds Axis Large Cap.")
        return
    try:
        ev = _evaluate(queries, False)
    except SystemExit as e:
        st.info(str(e))
        return
    _unresolved(ev)
    if not ev.decisions:
        return
    st.markdown(f"**As of {ev.as_of}**")
    _compare(ev)
    with st.expander("Verdicts and reasons", expanded=True):
        _decisions(ev)
