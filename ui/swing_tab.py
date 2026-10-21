"""Swing tab: setups in the Nifty 200 / 500, scored, with a trade plan each.

Streamlit runs every tab's code on each rerun, and the scan takes ~30s for
the Nifty 200 (longer for the 500), so nothing is computed until Scan is
pressed; results are then cached for the day.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from records import swing_report as sr
from scoring import timing
from ui import components as c

LABELS = {"nifty200": "Nifty 200", "nifty500": "Nifty 500"}
REGIME_TONE = {"favourable": "good", "mixed": "warn", "unfavourable": "bad",
               "unknown": "neutral"}


@st.cache_data(ttl=6 * 3600, show_spinner="Scanning for swing setups...")
def _scan(universe: str) -> sr.SwingView:
    return sr.load(universe)


def _cfg() -> dict:
    return timing.load_config()


def _methodology(cfg: dict) -> None:
    s = cfg["swing"]
    pb, bo = s["pullback"], s["breakout"]
    w = s["weights"]
    st.markdown(f"""
A **swing trade** holds a stock for days to a few weeks to catch one leg of a
move, with the stop, targets and time limit fixed before the trade starts.
Delivery (CNC) only, so overnight gaps are part of the risk.

**1. Eligible: liquid stocks in an uptrend.** Every check uses only data up to
the scan date.
- Close above the 200 DMA, 50 DMA above the 200 DMA, and the 200 DMA rising
- Price at least ₹{s['min_price']:.0f}, and average traded value over 20 days of at least ₹{s['min_turnover_cr']:.0f} cr
- Daily ATR between {s['atr_pct'][0]:.0f}% and {s['atr_pct'][1]:.0f}% of price. Below that the stock is too quiet to swing; above it the stop is too wide to size.

**2. Setup: something to act on today.** A stock without a setup is never suggested, whatever its score.
- **Pullback:** the low came within {pb['touch_atr']} ATR of the {pb['ma']}-day EMA in the last {pb['touch_bars']} sessions, RSI(14) is between {pb['rsi'][0]:.0f} and {pb['rsi'][1]:.0f}, and today closed up and above the EMA. The up close is the turn; buying the touch itself means catching a falling knife.
- **Breakout:** the close is above the prior {bo['lookback']}-session high, on at least {bo['volume_mult']}× average volume, out of a base no wider than {bo['max_base_pct']:.0f}%, and within {bo['near_high_pct']:.0f}% of the 52-week high.
- Stocks reporting quarterly results within {s['results_within_days']} days are held back (NSE board-meeting calendar).

**3. Score, 0–100: orders the setups.**

| Component | Weight | What it measures |
|---|---|---|
| Trend | {w['trend']:.0%} | 50 DMA above the 200 DMA, slope of the 200 DMA, ADX |
| Relative strength | {w['relative_strength']:.0%} | 3- and 6-month return minus NIFTY 500's, as a percentile of today's eligible names |
| Setup quality | {w['setup']:.0%} | Pullback: depth to the EMA, how far RSI has reset, close near the day's high. Breakout: volume multiple, base tightness, distance from the 52-week high |
| Volume | {w['volume']:.0%} | Up-day volume against down-day volume, 20 days |
| Risk | {w['risk']:.0%} | A tighter stop (in % of price) scores higher |

**4. Trade plan: mechanical, so it can be tested exactly as shown.**
- **Entry:** buy-stop {s['entry_buffer_pct']}% above the signal day's high, valid for {s['entry_valid_sessions']} sessions. If it doesn't trigger, there's no trade.
- **Stop:** lowest low of the last {s['stop_lookback']} sessions minus {s['stop_buffer_atr']} ATR, kept between {s['stop_atr'][0]:.0f} and {s['stop_atr'][1]:.0f} ATR below the entry.
- **T1** at {s['t1_r']}R: book {s['t1_book_pct']:.0f}% and move the stop to entry. **T2** at {s['t2_r']}R for the rest, or exit at the close after {s['max_hold_sessions']} sessions. (1R is entry minus stop.)
- **Size:** {s['risk_per_trade_pct']}% of capital lost if the stop is hit, and at most {s['max_position_pct']:.0f}% of capital in one name.
- **Costs:** about {s['round_trip_cost_pct']}% per round trip (STT 0.1% on each side, stamp duty, exchange fees, DP charge, slippage). Short-term gains are taxed at 20% on top.

**Market backdrop.** NIFTY 500 against its 200 DMA, plus breadth (the share of the universe above its own 50 DMA, with {s['breadth_min_pct']:.0f}% needed). It's shown for context only and doesn't filter suggestions (`regime_gate: {str(s['regime_gate']).lower()}`).

Every number above is in `config/scoring.yaml` under `swing:`.
""")


def _static(df: pd.DataFrame, fmt: dict[str, str]) -> None:
    """A plain table. Not st.dataframe: Streamlit's grid computes a width of
    zero when it renders inside a tab that is not the active one, and these
    tables live in the Backtest sub-tab (see components.watching_table)."""
    out = df.copy()
    for col, f in fmt.items():
        if col in out:
            out[col] = [f.format(v) if v is not None and v == v else "--" for v in out[col]]
    # A string index: st.table drops an integer one (the Year and Band columns).
    key = out.columns[0]
    out[key] = out[key].astype(str)
    st.table(out.set_index(key))


def _forward() -> None:
    """The live record: what was actually suggested, and how it has gone."""
    from scripts import record_swing
    st.markdown("**Forward record** (picks actually suggested, from "
                "history/swing_picks.xlsx)")
    if not record_swing.HISTORY.exists():
        st.caption("Nothing recorded yet. Use **Save top 10 to Excel** after a "
                   "scan, or install the daily job "
                   "(`scripts/install_schedule.py --install daily`).")
        return
    try:
        p = pd.read_excel(record_swing.HISTORY, sheet_name="Picks")
        s = pd.read_excel(record_swing.HISTORY, sheet_name="Summary")
    except (OSError, ValueError):
        st.caption("history/swing_picks.xlsx could not be read.")
        return
    first, last = pd.to_datetime(p["price_date"]).min(), pd.to_datetime(p["price_date"]).max()
    st.caption(f"{p['price_date'].nunique()} scan dates, {first:%d-%b-%Y} to "
               f"{last:%d-%b-%Y}. Outcomes are replayed with each scan's own plan. "
               "Only closed trades count as results.")
    _static(s.rename(columns={"group": "", "trades": "Trades", "win_pct": "Win %",
                              "avg_r": "Avg R", "avg_ret_pct": "Avg %"}),
            {"Trades": "{:,.0f}", "Win %": "{:.1f}%", "Avg R": "{:+.3f}",
             "Avg %": "{:+.2f}%"})
    st.divider()


def _backtest(cal: dict | None) -> None:
    _forward()
    if not cal:
        st.info("No backtest on file yet. Run `scripts/run_swing.py --backtest`, or use "
                "**Rerun swing backtest** in the sidebar.")
        return
    g = cal["groups"]
    top_key = f"top {cal['top_n']}"
    st.caption(f"{LABELS.get(cal['universe'], cal['universe'])}, {cal['from']} to "
               f"{cal['to']}. Every signal is played out with the plan above, after "
               f"costs. Run on {cal['run_on']}.")
    rows = []
    for name, why in (("all eligible", "every uptrend stock, every day, no setup"),
                      ("any setup", "pullback or breakout, unscored"),
                      (top_key, "the day's best-scored setups: what this tab suggests"),
                      ("pullback", ""), ("breakout", "")):
        x = g.get(name) or {}
        rows.append({"Group": name, "What": why, "Trades": x.get("trades"),
                     "Win %": x.get("win_pct"), "Avg R": x.get("avg_r"),
                     "Avg %": x.get("avg_ret_pct"), "Stopped %": x.get("stopped_pct"),
                     "Hit T2 %": x.get("t2_pct"), "Sessions": x.get("avg_sessions")})
    _static(pd.DataFrame(rows), {"Trades": "{:,.0f}", "Win %": "{:.1f}%",
                                 "Avg R": "{:+.3f}", "Avg %": "{:+.2f}%",
                                 "Stopped %": "{:.1f}%", "Hit T2 %": "{:.1f}%",
                                 "Sessions": "{:.1f}"})
    st.caption("Avg R: average result per trade in multiples of the risk taken "
               "(entry minus stop), after costs.")

    bands = pd.DataFrame(cal.get("score_bands") or [])
    if not bands.empty:
        st.markdown("**By score quintile** (setup trades). A score that works "
                    "should rise from band 1 to band 5.")
        _static(bands[["band", "score", "trades", "win_pct", "avg_r", "avg_ret_pct"]]
                .rename(columns={"band": "Band", "score": "Score range",
                                 "trades": "Trades", "win_pct": "Win %",
                                 "avg_r": "Avg R", "avg_ret_pct": "Avg %"}),
                {"Trades": "{:,.0f}", "Win %": "{:.1f}%", "Avg R": "{:+.3f}",
                 "Avg %": "{:+.2f}%"})

    left, right = st.columns(2)
    with left:
        st.markdown("**By market backdrop** (avg R)")
        _static(pd.DataFrame([
            {"Backdrop": k, "Setups": v["any setup"].get("avg_r"),
             "Top": v[top_key].get("avg_r"), "Setup trades": v["any setup"].get("trades")}
            for k, v in cal["regimes"].items()]),
            {"Setups": "{:+.3f}", "Top": "{:+.3f}", "Setup trades": "{:,.0f}"})
        r = cal.get("results") or {}
        if r:
            st.markdown(f"**Results within {r['days']} days** (setups, since {r['from']})")
            _static(pd.DataFrame([
                {"": k, "Trades": r[k].get("trades"), "Avg R": r[k].get("avg_r")}
                for k in ("results soon", "no results")]),
                {"Trades": "{:,.0f}", "Avg R": "{:+.3f}"})
    with right:
        st.markdown("**By year** (avg R)")
        _static(pd.DataFrame(cal["years"]).rename(columns={
            "year": "Year", "eligible_r": "Eligible", "setup_r": "Setups",
            "top_r": "Top", "top_trades": "Top trades"}),
            {"Eligible": "{:+.3f}", "Setups": "{:+.3f}", "Top": "{:+.3f}",
             "Top trades": "{:,.0f}"})
    p = cal.get("portfolio") or {}
    if p:
        st.markdown(
            f"**As a portfolio:** the day's top {cal['top_n']} taken into "
            f"{p['max_positions']} slots, sized as above, made **{p['cagr_pct']:.1f}% a year** "
            f"over {p['years']} years ({p['trades_taken']:,} trades). Over the same span "
            f"NIFTY 500 made **{cal['benchmark_cagr_pct']:.1f}%**. The worst fall in "
            f"closed-trade equity was {p['closed_trade_max_dd_pct']:.0f}%.")
    st.caption("The universe is today's index list applied backwards, which "
               "flatters every absolute number here (today's members are the ones "
               "that did well). The rows share that bias, so the comparisons "
               "between them are the honest part.")


def _verdict(cal: dict | None) -> str | None:
    """One line on whether the score has earned its ordering."""
    if not cal:
        return None
    g = cal["groups"]
    top = g.get(f"top {cal['top_n']}", {}).get("avg_r")
    anys = g.get("any setup", {}).get("avg_r")
    elig = g.get("all eligible", {}).get("avg_r")
    bands = cal.get("score_bands") or []
    if None in (top, anys, elig) or not bands:
        return None
    rising = bands[-1]["avg_r"] > bands[0]["avg_r"]
    if top > anys and rising:
        return None
    p = cal.get("portfolio") or {}
    return (f"**Backtest: the score has not been shown to pick better trades.** "
            f"From {cal['from'][:4]} to {cal['to'][:4]}, the top {cal['top_n']} by score "
            f"averaged {top:+.2f}R per trade after costs, against {anys:+.2f}R for every "
            f"setup and {elig:+.2f}R for uptrend stocks with no setup at all. The "
            f"{'highest' if rising else 'lowest'} score band did best. As a portfolio "
            f"that was {p.get('cagr_pct', 0):.1f}% a year, against "
            f"{cal.get('benchmark_cagr_pct') or 0:.1f}% for NIFTY 500. Treat this list "
            f"as setups to review, not a ranking to trust. Details are under "
            f"**Backtest** below.")


def render(on_watch=None) -> None:
    """on_watch runs after a pick is added to the watch list, so the page can
    drop its cached copy of that list."""
    cfg = _cfg()
    s = cfg["swing"]
    st.subheader("Swing: setups to hold for days to a few weeks")
    st.caption("Liquid stocks in an uptrend that show a pullback or a breakout "
               "today, scored, each with an entry, a stop and targets. How it "
               "works and how it has done are below the list.")

    a, b, d, go, save = st.columns([1.4, 1.6, 1.2, 1, 1.2])
    universes = list(sr.UNIVERSES)
    universe = a.selectbox("Universe", universes, index=universes.index(s["universe"]),
                           format_func=LABELS.get, key="swing_universe")
    capital = b.number_input("Capital for sizing (₹)", min_value=0, value=1_000_000,
                             step=100_000, key="swing_capital",
                             help=f"Shares are sized so a stop-out loses "
                                  f"{s['risk_per_trade_pct']}% of this.")
    show_all = d.toggle("Show full list", value=False, key="swing_all",
                        help="Every eligible stock, not only the top suggestions. "
                             "Names without a setup are at the bottom.")
    go.write("")
    if go.button("Scan", type="primary", width="stretch", key="swing_go"):
        st.session_state["swing_req"] = universe
    req = st.session_state.get("swing_req")

    # The scan runs before the Save button is drawn, so the button can be
    # enabled on the same run that produced the picks, not one click later.
    v = _scan(req) if req else None
    n_sug = 0
    if v is not None and not v.table.empty:
        n_sug = int(v.table["suggest"].sum())
        if s.get("regime_gate") and v.regime and v.regime.state != "favourable":
            n_sug = 0
    n_save = min(s["top_n"], n_sug)
    save.write("")
    if save.button(f"Save top {n_save or s['top_n']} to Excel", width="stretch",
                   key="swing_save", disabled=not n_save,
                   help="Records this scan -- every eligible stock, not only the "
                        "top -- in history/swing/ (once per price date), then "
                        "rebuilds history/swing_picks.xlsx: the top picks of every "
                        "recorded scan and how each has played out. Available "
                        "after a scan with at least one suggestion."):
        from scripts import record_swing
        n, df = record_swing.record(v, cfg,
                                    bench_close=record_swing.benchmark_close(cfg, v.as_of))
        closed = int((df["outcome"] == "closed").sum())
        where = (f"history/swing_picks.xlsx now holds {len(df)} picks across "
                 f"{df['price_date'].nunique()} scan dates, {closed} closed.")
        if n:
            st.success(f"Recorded the {LABELS[v.universe]} scan of {v.as_of:%d-%b-%Y} "
                       f"({n} eligible stocks, {n_sug} with a setup). {where}")
        else:
            st.info(f"The {LABELS[v.universe]} scan of {v.as_of:%d-%b-%Y} was already "
                    f"recorded and is kept as first saved. Outcomes were refreshed: "
                    f"{where}")

    cal = sr.calibration()
    tab_list, tab_method, tab_bt = st.tabs(["Suggestions", "Methodology", "Backtest"])
    with tab_method:
        _methodology(cfg)
    with tab_bt:
        _backtest(cal)

    with tab_list:
        if v is None:
            st.info(f"Press **Scan** to rank the {LABELS[universe]}. It takes about "
                    "half a minute (longer for the Nifty 500) and is cached for the day.")
            return
        if req != universe:
            st.caption(f"Showing the {LABELS[req]}. Press Scan to switch to the "
                       f"{LABELS[universe]}.")
        if st.button("Rescan", key="swing_rescan",
                     help="Recompute after a data refresh"):
            _scan.clear()
            st.rerun()
        if v.table.empty:
            st.warning("Nothing eligible. Is the price history current? "
                       "Use **Refresh prices** in the sidebar.")
            return
        _list(v, cfg, capital, show_all, cal, on_watch)


def _results(view: pd.DataFrame, as_of) -> list[str]:
    """Results badge per row. The scan already looked up announced dates
    (results_on), so this reads only the local results store."""
    try:
        from results import engine as results_engine
        upcoming = {}
        if "results_on" in view:
            upcoming = {s: d for s, d in zip(view["symbol"], view["results_on"])
                        if isinstance(d, dt.date)}
        b = results_engine.badges(list(view["symbol"]), dt.date.today(), upcoming)
        return [b.get(s, "") for s in view["symbol"]]
    except Exception:
        return [""] * len(view)


def _list(v: sr.SwingView, cfg: dict, capital: float, show_all: bool,
          cal: dict | None, on_watch=None) -> None:
    s = cfg["swing"]
    t = sr.suggestion_rows(v.table, capital, cfg)
    sug = t[t["suggest"]]
    gate = s.get("regime_gate") and v.regime and v.regime.state != "favourable"

    # The backdrop goes on its own line below: as a metric, "Unfavourable"
    # does not fit a fifth of the width.
    m = st.columns(4)
    m[0].metric("Data through", f"{v.as_of:%d-%b-%Y}")
    m[1].metric("Breadth", f"{v.regime.breadth_pct:.0f}%"
                if v.regime and v.regime.breadth_pct is not None else "--",
                help="Share of the universe above its own 50 DMA")
    m[2].metric("Eligible", f"{len(t)}", f"of {v.universe_size}", delta_color="off",
                delta_arrow="off", help="In an uptrend, liquid, tradable volatility")
    m[3].metric("With a setup", f"{len(sug)}")
    if v.regime:
        st.markdown(f"Market backdrop &nbsp; "
                    f"{c.pill(v.regime.state, REGIME_TONE[v.regime.state])} "
                    f"&nbsp; <span style='font-size:13px;opacity:.8'>{v.regime.note}. "
                    f"Context only; the Backtest tab shows how setups did in each "
                    f"backdrop.</span>", unsafe_allow_html=True)
    if not v.results_checked:
        st.caption("⚠️ NSE's results calendar couldn't be reached, so the "
                   "results-week filter was skipped.")
    if verdict := _verdict(cal):
        st.warning(verdict)
    if gate:
        st.error("`regime_gate` is on and the backdrop isn't favourable, so "
                 "nothing is suggested. The full list still shows the setups.")
        sug = sug.iloc[0:0]

    view = t if show_all else sug.head(s["top_n"])
    if not show_all:
        st.markdown(f"**Top {min(s['top_n'], len(sug))} of {len(sug)} setups**, by score."
                    " Turn on *Show full list* for every eligible stock.")
    setups = sorted(x for x in view["setup"].unique() if x)
    if len(setups) > 1:
        pick = st.segmented_control("Setup", ["All", *setups], default="All",
                                    key="swing_setup")
        if pick not in (None, "All"):
            view = view[view["setup"] == pick]
    if view.empty:
        st.info("No stock has an active setup today. Turn on *Show full list* to "
                "see the uptrend names that are waiting for one.")
        return

    # Status only varies in the full list; in the top list every row is
    # "suggested", so it would only push useful columns off the edge.
    view = view.assign(results=_results(view, v.as_of))
    cols = (["rank", "symbol", "setup"] + (["status"] if show_all else [])
            + ["score", "close", "entry", "stop", "stop_pct", "t1", "t2", "qty",
               "position", "results", "sector"])
    st.dataframe(
        view[cols].replace({"setup": {"": "—"}}).rename(columns={
            "rank": "#", "symbol": "Stock", "setup": "Setup", "score": "Score",
            "close": "Close", "entry": "Buy above", "stop": "Stop",
            "stop_pct": "Risk %", "t1": "T1", "t2": "T2", "qty": "Qty",
            "position": "Position", "status": "Status", "sector": "Sector",
            "results": "Results"}),
        hide_index=True, width="stretch",
        height=min(38 + 35 * len(view), 720),
        column_config={
            "Score": st.column_config.ProgressColumn(
                min_value=0, max_value=100, format="%d",
                help="0-100. Orders the setups; see the backtest for how much "
                     "that ordering has been worth"),
            "Close": st.column_config.NumberColumn(format="₹%.2f"),
            "Buy above": st.column_config.NumberColumn(
                format="₹%.2f", help=f"Buy-stop, valid {s['entry_valid_sessions']} sessions"),
            "Stop": st.column_config.NumberColumn(format="₹%.2f"),
            "Risk %": st.column_config.NumberColumn(
                format="%.1f%%", help="Entry to stop, % of entry"),
            "T1": st.column_config.NumberColumn(
                format="₹%.2f", help=f"{s['t1_r']}R: book {s['t1_book_pct']:.0f}%, stop to entry"),
            "T2": st.column_config.NumberColumn(format="₹%.2f", help=f"{s['t2_r']}R"),
            "Qty": st.column_config.NumberColumn(
                format="%d", help=f"Shares so the stop loses {s['risk_per_trade_pct']}% "
                                  f"of capital (max {s['max_position_pct']:.0f}% in one name)"),
            "Position": st.column_config.NumberColumn(format="₹%d"),
            "Results": st.column_config.TextColumn(
                help="This quarter's results verdict (vs the company's own "
                     "trend), or its announced results date"),
        })

    st.divider()
    st.markdown("**Trade plan**")
    pick = st.selectbox("Stock", view["symbol"].tolist(), key="swing_pick")
    r = t[t["symbol"] == pick].iloc[0]
    head = st.columns([2, 2, 2, 3])
    head[0].markdown(f"**{r['symbol']}** &nbsp; #{int(r['rank'])}",
                     unsafe_allow_html=True)
    head[1].markdown(c.pill(r["setup"] or "no setup", "info" if r["setup"] else "neutral")
                     + " &nbsp; " + c.pill(f"score {r['score']:.0f}", "neutral"),
                     unsafe_allow_html=True)
    head[2].markdown(c.pill(r["status"], "good" if r["suggest"] else "warn"),
                     unsafe_allow_html=True)
    head[3].markdown(f"<span style='font-size:13px;opacity:.8'>{r['sector']}</span>",
                     unsafe_allow_html=True)
    st.markdown(c.trade_plan(
        r["entry"], r["stop"], r["t1"], r["t2"],
        f"Buy-stop valid {s['entry_valid_sessions']} sessions · book "
        f"{s['t1_book_pct']:.0f}% at T1 and move the stop to entry · exit the rest "
        f"at T2 or after {s['max_hold_sessions']} sessions",
        label="buy above"), unsafe_allow_html=True)
    if capital and r["qty"]:
        st.caption(f"{int(r['qty']):,} shares ≈ {c.money(r['position'])} "
                   f"({100 * r['position'] / capital:.1f}% of capital), "
                   f"{c.money(r['risk_amt'])} at risk.")
    comp = pd.DataFrame([{
        "Trend": r["sc_trend"], "Relative strength": r["sc_rs"],
        "Setup quality": r["sc_setup"], "Volume": r["sc_volume"], "Risk": r["sc_risk"]}])
    st.dataframe(comp, hide_index=True, width="stretch", column_config={
        k: st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d")
        for k in comp.columns})
    st.markdown(f"<div style='color:#6b7280;font-size:13px;'>"
                f"{c.trend_note({'price': r['close'], 'sma50': r['sma50'], 'sma200': r['sma200'], 'rsi': r['rsi14']})}"
                f" · ATR {r['atr_pct']:.1f}% · volume {r['vol_ratio']:.1f}× average"
                f"</div>", unsafe_allow_html=True)

    from book import watching as wg
    if r["symbol"] in set(wg.symbols()):
        st.caption(f"✓ {r['symbol']} is on your watch list.")
    elif st.button(f"Watch {r['symbol']}", key=f"swing_watch_{r['symbol']}",
                   help="Adds it to the Watching tab, measured from today's close "
                        "with this plan's stop, T1 and T2."):
        note = (f"swing {r['setup'] or 'no setup'} {v.as_of:%d-%b}: buy above "
                f"{r['entry']:,.2f}, stop {r['stop']:,.2f}")
        ok, msg = wg.add(r["symbol"], source="swing", notes=note,
                         price=float(r["close"]),
                         stop=float(r["stop"]),
                         targets=(float(r["t1"]), float(r["t2"])), use_log=False)
        (st.success if ok else st.info)(msg)
        if ok and on_watch:
            on_watch()
