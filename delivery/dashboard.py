"""Local Streamlit dashboard. Bind to localhost only -- this shows your positions.

Run:  streamlit run delivery/dashboard.py --server.address 127.0.0.1
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import watchlist as wl                                    # noqa: E402
from data import store                                    # noqa: E402
from scoring import pipeline, timing                      # noqa: E402
from signals import engine, state as sig_state            # noqa: E402
import run_daily                                          # noqa: E402

st.set_page_config(page_title="Stock Signals", layout="wide")


@st.cache_resource
def _con():
    return store.connect()


@st.cache_data(ttl=600)
def _scores(symbols: tuple[str, ...]) -> pd.DataFrame:
    return pipeline.latest_scores(_con(), list(symbols), timing.load_config())


cfg = timing.load_config()
con = _con()
symbols = wl.active_symbols()

st.title("Stock Signals")
st.caption("Decision support, not advice. Every order is placed by you.")

bench = store.load_index(con, cfg["signals"]["regime_index"])
regime = engine.regime_ok(bench, dt.date.today(), cfg) if not bench.empty else True
c1, c2, c3 = st.columns(3)
c1.metric("Market regime", "RISK-ON" if regime else "RISK-OFF",
          help=f"{cfg['signals']['regime_index']} vs its {cfg['signals']['regime_sma']} DMA. "
               "Risk-off suppresses new buy signals; exits always stay active.")
c2.metric("Watchlist", len(symbols))
c3.metric("Open positions", len(sig_state.load_positions(con)))

tabs = st.tabs(["Signals", "Scores", "Positions", "Stock detail", "Watchlist"])

with tabs[0]:
    portfolio_value = st.number_input("Portfolio value for sizing", value=1_000_000,
                                      step=50_000)
    if st.button("Evaluate now"):
        sigs = run_daily.evaluate(con, cfg, portfolio_value)
        if not sigs:
            st.info("No signals today.")
        for s in sigs:
            colour = {"BUY": "green", "SELL": "red", "TRIM": "orange"}[s.action]
            st.markdown(f":{colour}[**{s.action} {s.symbol}**] @ {s.price:,.2f}")
            st.caption(f"{s.rule}: {s.reason}")
            if s.stop_price:
                st.caption(f"Stop {s.stop_price:,.2f} · qty {s.qty:,.0f} · {s.components}")
            st.divider()

with tabs[1]:
    scores = _scores(tuple(symbols))
    if scores.empty:
        st.warning("No scored data yet. Run: python run_backfill.py")
    else:
        st.dataframe(scores.round(1), width="stretch", hide_index=True)

with tabs[2]:
    positions = sig_state.load_positions(con)
    if not positions:
        st.info("No open positions. Import them from Kite, or add them by hand "
                "in data/holdings.json.")
    else:
        rows = []
        for sym, p in positions.items():
            f = pipeline.scored_frame(con, sym, cfg)
            last = float(f["close"].iloc[-1]) if not f.empty else p.entry_price
            rows.append({"symbol": sym, "qty": p.qty, "entry": p.entry_price,
                         "last": last, "P&L %": p.pnl_pct(last),
                         "stop": p.stop_price, "trailing": p.trailing,
                         "risk to stop %": 100 * (last - p.stop_price) / last})
        st.dataframe(pd.DataFrame(rows).round(2), width="stretch", hide_index=True)

with tabs[3]:
    sym = st.selectbox("Symbol", symbols)
    f = pipeline.scored_frame(con, sym, cfg)
    if f.empty:
        st.warning("No data for this symbol yet.")
    else:
        f = f.set_index(pd.to_datetime(f["date"]))
        st.line_chart(f[["close", "sma50", "sma200"]].tail(500))
        st.line_chart(f[["timing_score"]].tail(500))
        cols = ["trend", "momentum", "relative_strength", "volume_score",
                "volatility", "timing_score", "rsi14", "atr_pct", "adx14"]
        st.dataframe(f[cols].tail(10).round(1), width="stretch")

with tabs[4]:
    st.caption("Edit directly. Retiring a stock sets active=no rather than deleting "
               "the row, so the backtest still sees it and past losers are not "
               "quietly erased.")
    entries = wl.load()
    df = pd.DataFrame([e.__dict__ for e in entries])
    edited = st.data_editor(df, num_rows="dynamic", width="stretch",
                            key="watchlist_editor")
    if st.button("Save watchlist"):
        from data.sources import universe
        syms = [str(s).strip().upper() for s in edited["symbol"] if str(s).strip()]
        known, unknown = universe.validate(syms)
        if unknown:
            st.error(f"Not listed on NSE: {', '.join(unknown)}. Fix or remove these "
                     "before saving -- a typo produces no data silently.")
        else:
            wl.save([wl.Entry(**{k: str(v) for k, v in r.items()})
                     for r in edited.to_dict("records") if str(r.get("symbol", "")).strip()])
            st.success(f"Saved {len(known)} symbols.")
            _scores.clear()
