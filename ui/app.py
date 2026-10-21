"""Stock signals dashboard.

Navigation is a bar across the top with four menus; each page loads only its
own data, so opening Mutual funds or Analyze no longer waits for the whole
market to be ranked.

  Today                    everything that wants a decision (ui/views/today.py)
  Portfolio  Stocks        holdings with verdicts, and the watch list (ui/views/stocks.py)
             Mutual funds  your MF book against each category (ui/mf_tab.py)
  Ideas      Momentum      top band on both lookbacks (ui/views/momentum.py)
             Swing         pullback / breakout setups with a plan (ui/swing_tab.py)
             Quarterly results                                    (ui/results_tab.py)
             GARP / QARP   fundamental shortlists                 (ui/picker_tab.py)
  Market     Overview      the ranked universe, entry levels      (ui/views/market.py)
             F&O           expected range to expiry, experimental (ui/fno_tab.py)
  Research   Analyze a stock                                      (ui/analyze_tab.py)
             Compare funds                                        (ui/mf_tab.py)

Under the bar, on every page: search (ui/views/search.py) and the data chip,
which opens to Data and jobs (ui/views/data_jobs.py) -- where every refresh
that used to sit in the sidebar now lives.

Read-only throughout. Nothing here places an order.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ui import shell, state       # noqa: E402

st.set_page_config(page_title="Stock Signals", page_icon="📈", layout="wide")

P: dict = {}


def _today():
    from ui.views import today
    today.render(P)


def _stocks():
    from ui.views import stocks
    stocks.render()


def _funds():
    from ui import mf_tab
    mf_tab.render_book(run_script=state.run_and_reload)


def _momentum():
    from ui.views import momentum
    momentum.render()


def _swing():
    from ui import swing_tab
    swing_tab.render(on_watch=state.clear)


def _results():
    from ui import results_tab
    results_tab.render(run_script=state.run_and_reload)


def _picker():
    from book import portfolio as pf
    from ui import picker_tab
    fresh = {f["kind"]: f for f in state.freshness()}
    as_of = fresh.get("market", {}).get("latest")
    if as_of is None:
        as_of = state.snapshot().as_of
    held = {h["tradingsymbol"] for h in pf.load_holdings()}
    picker_tab.render(as_of, held, run_script=state.run_and_reload)


def _market():
    from ui.views import market
    market.render()


def _fno():
    from ui import fno_tab
    fno_tab.render()


def _analyze():
    from ui import analyze_tab
    st.subheader("Analyze a stock")
    st.caption("Both analysts describe the stock as it is today; neither "
               "forecasts its price.")
    analyze_tab.render()


def _compare():
    from ui import mf_tab
    mf_tab.render_compare()


def _data():
    from ui.views import data_jobs
    data_jobs.render()


def _search():
    from ui.views import search
    search.render(P)


P.update(
    today=st.Page(_today, title="Today", icon=":material/today:", url_path="today",
                  default=True),
    stocks=st.Page(_stocks, title="Stocks", url_path="stocks"),
    funds=st.Page(_funds, title="Mutual funds", url_path="mutual-funds"),
    momentum=st.Page(_momentum, title="Momentum", url_path="momentum"),
    swing=st.Page(_swing, title="Swing", url_path="swing"),
    results=st.Page(_results, title="Quarterly results", url_path="results"),
    picker=st.Page(_picker, title="GARP / QARP", url_path="garp-qarp"),
    market=st.Page(_market, title="Market overview", url_path="market"),
    fno=st.Page(_fno, title="F&O (experimental)", url_path="fno"),
    analyze=st.Page(_analyze, title="Analyze a stock", url_path="analyze"),
    compare=st.Page(_compare, title="Compare funds", url_path="compare-funds"),
    data=st.Page(_data, title="Data and jobs", url_path="data", visibility="hidden"),
    search=st.Page(_search, title="Search", url_path="search", visibility="hidden"),
)

nav = st.navigation({
    "": [P["today"]],
    "Portfolio": [P["stocks"], P["funds"]],
    "Ideas": [P["momentum"], P["swing"], P["results"], P["picker"]],
    "Market": [P["market"], P["fno"]],
    "Research": [P["analyze"], P["compare"]],
    "More": [P["data"], P["search"]],
}, position="top")

shell.header(P)
nav.run()
