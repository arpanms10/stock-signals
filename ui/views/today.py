"""Today: everything that wants a decision, from every page, in one list.

Nothing here is computed afresh: the rows are the verdicts the Stocks,
Mutual funds and Watching views already show, gathered so most visits need
only this page. Each row opens the page it came from.

The one addition is Expected ranges: NIFTY and the core holdings, spot +/-
one standard deviation for the next session and the next five, with how
often such a band has held (strategy/ranges.py, scripts/run_range_backtest.py). A
size of move, not a direction.
"""
from __future__ import annotations

import datetime as dt

import streamlit as st

from ui import components as c
from ui import state

ACT = {"EXIT", "TRIM", "STOP"}
RECENT_DAYS = 10
RANGE_INDEX = "NIFTY"


def _recent(iso, today: dt.date) -> bool:
    try:
        return (today - dt.date.fromisoformat(str(iso)[:10])).days <= RECENT_DAYS
    except ValueError:
        return False


def _stock_rows(snap) -> list[dict]:
    order = {"EXIT": 0, "TRIM": 1, "ADD": 2, "WATCH": 3}
    out = []
    for r in sorted(snap.holdings, key=lambda r: (order.get(r["action"], 9),
                                                  0 if r["urgency"] == "urgent" else 1,
                                                  -r["value"])):
        if r["action"] not in order:
            continue
        why = r["reasons"][0] if r["reasons"] else ""
        out.append({"action": r["action"], "urgent": r["urgency"] == "urgent",
                    "name": r["symbol"], "what": "stock", "why": why,
                    "page": "stocks", "view": "Holdings"})
    return out


def _fund_rows() -> list[dict]:
    from ui import mf_tab
    try:
        ev = mf_tab._evaluate((), True)
    except SystemExit:
        return []
    out = []
    for d in ev.decisions:
        if d.weight is None or d.action not in ("EXIT", "TRIM", "ADD", "WATCH"):
            continue
        size = (f"{d.weight:.0f}% → {d.target:.0f}% of book" if d.target is not None
                else f"{d.weight:.0f}% of book")
        why = d.reasons[-1] if d.action in ("TRIM", "ADD") and d.reasons else \
            (d.reasons[0] if d.reasons else "")
        out.append({"action": d.action, "urgent": False, "name": d.name, "what": "fund",
                    "why": f"{size} · {why}", "page": "funds"})
    return out


def _watch_rows(snap, today: dt.date) -> list[dict]:
    out = []
    for w in snap.watching:
        if w.get("stopped_on") and _recent(w["stopped_on"], today):
            out.append({"action": "STOP", "urgent": False, "name": w["symbol"],
                        "what": f"watch list ({w.get('source') or 'manual'})",
                        "why": f"stop reached {str(w['stopped_on'])[:10]}",
                        "page": "stocks", "view": "Watching"})
        elif w.get("t1_hit_on") and _recent(w["t1_hit_on"], today) \
                and w.get("outcome") == "t1":
            out.append({"action": "HOLD", "urgent": False, "name": w["symbol"],
                        "what": f"watch list ({w.get('source') or 'manual'})",
                        "why": f"first target reached {str(w['t1_hit_on'])[:10]}; the stop "
                               "moves up to the flagged price",
                        "page": "stocks", "view": "Watching"})
    return out


def _results_due(today: dt.date) -> list[tuple[dt.date, str]]:
    from results import engine
    from ui import results_tab
    try:
        cov = results_tab._coverage()
        status = engine.status(cov, today)
        done = set(status.loc[status["current"], "symbol"])
        up = results_tab._upcoming(tuple(cov), today)
    except Exception:          # noqa: BLE001 -- the calendar is a nicety
        return []
    return sorted((d, s) for s, d in up.items()
                  if s not in done and 0 <= (d - today).days <= 7)


@st.cache_data(ttl=900, show_spinner=False)
def _range_rows(symbols: tuple[str, ...]) -> list[dict]:
    from records import ranges_store as rs
    from data import fo_bhavcopy as fb
    from data import store
    from scripts.record_ranges import load_calibration
    try:
        from data.sources.nse_derivatives import trading_holidays
        y = dt.date.today().year
        hol = trading_holidays(y) + trading_holidays(y + 1)
    except Exception:                 # noqa: BLE001 -- weekdays only, then
        hol = []
    pcon = store.connect()

    def closes(sym):
        df = store.load_prices(pcon, sym)
        return None if df.empty else df.set_index("date")["close"]

    con = fb.connect()
    try:
        return rs.today_rows(con, list(symbols), closes, load_calibration(), hol)
    finally:
        con.close()
        pcon.close()


@st.cache_data(ttl=900, show_spinner=False)
def _range_log() -> dict:
    """Forward-log hit rates: {(group, horizon): (hit %, n, first day)}."""
    from records import ranges_store as rs
    from data import fo_bhavcopy as fb
    con = fb.connect()
    try:
        sc = rs.score(rs.log(), con)
    finally:
        con.close()
    if sc.empty:
        return {}
    sc = sc[sc["method"] == "implied"]
    out = {}
    for name, g in (("NIFTY", sc[sc["symbol"] == RANGE_INDEX]), ("stocks", sc[sc["kind"] == "equity"])):
        for h, gh in g.groupby("horizon"):
            out[(name, int(h))] = (100 * gh["inside"].mean(), len(gh), gh["price_date"].min())
    return out


def _fmt(x) -> str:
    return "--" if x is None else f"{x:,.0f}" if abs(x) >= 100 else f"{x:,.2f}"


def _day(iso: str) -> str:
    return f"{dt.date.fromisoformat(iso):%a %d %b}"


def _ranges(snap, pages: dict, today: dt.date) -> None:
    core = [h for h in sorted(snap.holdings, key=lambda h: -h["value"])
            if h.get("bucket") == "core"]
    rows = _range_rows(tuple([RANGE_INDEX] + [h["symbol"] for h in core]))
    with st.container(border=True):
        st.markdown("#### Expected ranges")
        st.markdown(c.muted(
            "How far each could move, not which way: the close ± one standard "
            "deviation, from the options market's implied volatility. *Held before* "
            "is how often such a band held in the F&O history on file."))
        if not rows:
            st.caption("No F&O history on file yet -- run *Record Today ranges* on "
                       "Data and jobs.")
            return
        first = rows[0]
        if first["end_1"] < today.isoformat():
            st.warning(f"These ranges are from the close of {_day(first['price_date'])} "
                       f"and covered {_day(first['end_1'])}. Run *Record Today ranges* "
                       "(Data and jobs) for the latest close.")
        table = []
        for r in rows:
            note = []
            if r["method"] == "realised":
                note.append("no options: 20-day realised vol")
            elif r["iv_from"] == "next":
                note.append("expiry week: next month's IV")
            if r["results_on"] and (r["results_1"] or r["results_5"]):
                note.append(f"results {_day(r['results_on'])}")
            if r["end_1"] != first["end_1"]:
                note.append(f"prices to {_day(r['price_date'])}")
            hits = [f"{r[f'hit_{h}']:.0f}%" if r[f"hit_{h}"] is not None else "--"
                    for h in (1, 5)]
            table.append({
                "Name": "Nifty 50" if r["symbol"] == RANGE_INDEX else r["symbol"],
                "Close": _fmt(r["spot"]),
                "Next session": f"{_fmt(r['low_1'])} – {_fmt(r['high_1'])}",
                "±1": r["move_pct_1"],
                "Next 5 sessions": f"{_fmt(r['low_5'])} – {_fmt(r['high_5'])}",
                "±5": r["move_pct_5"],
                "Held before": f"{hits[0]} · {hits[1]}",
                "Note": "; ".join(note),
            })
        st.dataframe(
            table, hide_index=True, width="stretch", height=35 * (len(table) + 1) + 3,
            column_config={
                "Close": st.column_config.TextColumn(
                    f"Close {_day(first['price_date'])}"),
                "Next session": st.column_config.TextColumn(
                    f"{_day(first['end_1'])}",
                    help="The close ± one standard deviation of a day's move. A normal "
                         "distribution would put the next close inside about 68% of "
                         "the time."),
                "±1": st.column_config.NumberColumn("±", format="%.1f%%"),
                "Next 5 sessions": st.column_config.TextColumn(
                    f"By {_day(first['end_5'])}",
                    help="The same for the close five sessions out. Wider by √5."),
                "±5": st.column_config.NumberColumn("±", format="%.1f%%"),
                "Held before": st.column_config.TextColumn(
                    "Held before",
                    help="Share of past bands like this one (same source, index or "
                         "stock, results in the window or not) whose close landed "
                         "inside: next session · five sessions. From "
                         "run_range_backtest.py on NSE F&O history. A normal "
                         "distribution would give 68%."),
                "Note": st.column_config.TextColumn(
                    "Note", help="Results inside the window: the Held before figure "
                                 "is taken from past bands with results inside "
                                 "too. Expiry week: the near contract's IV is "
                                 "distorted, so next month's is used."),
            })
        log = _range_log()
        if log:
            parts = [f"{name} next session {log[(name, 1)][0]:.0f}% of {log[(name, 1)][1]:,}"
                     for name in ("NIFTY", "stocks") if (name, 1) in log]
            since = min(v[2] for v in log.values())
            st.caption(f"Forward log since {_day(since)} (bands written down before the "
                       "close): " + " · ".join(parts) + ".")
        else:
            st.caption("Forward log: every F&O underlying's bands are recorded each "
                       "weekday evening (history/ranges/) and scored once the close is "
                       "in; nothing scored yet.")


def _row(r: dict, pages: dict, i: int) -> None:
    cols = st.columns([0.9, 6, 1.6], vertical_alignment="center")
    cols[0].markdown(c.badge(r["action"], r["urgent"]), unsafe_allow_html=True)
    cols[1].markdown(f"**{r['name']}** " + c.muted(f"· {r['what']} · {r['why']}"))
    label = {"stocks": "Stocks", "funds": "Mutual funds"}[r["page"]]
    if cols[2].button(f"{label} ›", key=f"today_go_{i}", type="tertiary"):
        if r.get("view"):
            st.session_state["stocks_view_pref"] = r["view"]
        st.switch_page(pages[r["page"]])


def render(pages: dict) -> None:
    today = dt.date.today()
    snap = state.snapshot()
    state.page_title("Today", f"Prices to {snap.as_of}")
    for w in snap.warnings:
        st.warning(w)

    rows = _stock_rows(snap) + _fund_rows() + _watch_rows(snap, today)
    act = [r for r in rows if r["action"] in ACT]
    review = [r for r in rows if r["action"] not in ACT]
    due = _results_due(today)

    m = st.columns(4)
    m[0].metric("Act now", f"{len(act)}", help="Exits, trims and stops reached")
    m[1].metric("Review", f"{len(review)}",
                help="Adds with room, funds slipping, targets reached")
    m[2].metric("Results due this week", f"{len(due)}")
    m[3].metric("Market regime", snap.regime)
    st.caption(snap.regime_detail)

    i = 0
    for title, group, empty in (("Act now", act, "Nothing needs selling or trimming."),
                                ("Review", review, "Nothing to review.")):
        with st.container(border=True):
            st.markdown(f"#### {title}")
            if not group:
                st.caption(empty)
            for r in group:
                _row(r, pages, i)
                i += 1

    _ranges(snap, pages, today)

    with st.container(border=True):
        cols = st.columns([6, 1.6], vertical_alignment="center")
        if due:
            cols[0].markdown("**Results this week** " + c.muted(
                "· " + " · ".join(f"{s} {d:%a %d %b}" for d, s in due[:6])
                + (f" · {len(due) - 6} more" if len(due) > 6 else "")))
        else:
            cols[0].markdown("**Results this week** "
                             + c.muted("· none announced for what you follow"))
        if cols[1].button("Quarterly results ›", key="today_results", type="tertiary"):
            st.switch_page(pages["results"])
