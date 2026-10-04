"""Regression: no ETF or fund may enter the backtest universe or a live ranking.

LIQUIDCASE (ISIN INF0R8F01034) appeared in run_pit_momentum.py's latest
holdings. ETFs trade in the EQ series, so they are in the bhavcopy alongside
stocks; the ISIN is the only discriminator. Ingestion discarded it, nothing
ever built the instrument map, and both universe functions skipped the equity
filter whenever the map was empty -- so the filter had never run.
"""
import datetime as dt

import pandas as pd
import pytest

from backtest import pit_engine as pit
from data import bhavcopy as bc
from scoring import timing
from strategy import combined
from strategy import momentum as mom

STOCKS = [(f"STK{i}", f"INE{i:03d}A01011") for i in range(5)]
ETFS = [("LIQUIDCASE", "INF0R8F01034"), ("NIFTYBEES", "INF204KB14I2"),
        ("GOLDBEES", "INF204KB17I5")]


def _days(n=60, start=dt.date(2026, 6, 1)):
    for i in range(n):
        d = start + dt.timedelta(days=i)
        if d.weekday() < 5:
            yield d


@pytest.fixture
def market(tmp_path):
    """ETFs are the most liquid instruments here, as they are on NSE."""
    con = bc.connect(tmp_path / "m.db")
    for d in _days():
        rows = [(s, 500.0, 1e6, (10 - i) * 1e8, isin)
                for i, (s, isin) in enumerate(STOCKS)]
        rows += [(s, 1000.0, 1e7, 1e11, isin) for s, isin in ETFS]
        bc.save_day(con, d, pd.DataFrame(
            rows, columns=["symbol", "close", "volume", "turnover", "isin"]))
    return con


def test_ingestion_records_isins(market):
    """save_day must keep the ISIN the bhavcopy carries."""
    assert bc.equity_symbols(market) == {s for s, _ in STOCKS}
    assert {s for s, _ in ETFS} <= bc.non_equity_symbols(market)


@pytest.mark.parametrize("universe", [
    lambda con, d, n: bc.universe_on(con, d, top_n=n, min_days=10),
    lambda con, d, n: pit.liquid_universe(con, d, n, min_days=10),
], ids=["live", "backtest"])
def test_etfs_never_enter_the_universe(market, universe):
    u = universe(market, dt.date(2026, 7, 25), 5)
    assert not {s for s, _ in ETFS} & set(u)
    # ETFs top the turnover table, but must not crowd stocks out of top_n.
    assert u == [s for s, _ in STOCKS]


@pytest.mark.parametrize("universe", [
    lambda con, d: bc.universe_on(con, d, top_n=5, min_days=10),
    lambda con, d: pit.liquid_universe(con, d, 5, min_days=10),
], ids=["live", "backtest"])
def test_empty_instrument_map_fails_closed(market, universe):
    market.execute("DELETE FROM instruments")
    with pytest.raises(bc.InstrumentMapMissing):
        universe(market, dt.date(2026, 7, 25))


def test_unmapped_symbol_is_not_assumed_equity(market):
    market.execute("DELETE FROM instruments WHERE symbol = 'LIQUIDCASE'")
    u = pit.liquid_universe(market, dt.date(2026, 7, 25), 10, min_days=10)
    assert "LIQUIDCASE" not in u


def test_backfill_maps_symbols_ingested_without_isin(market, monkeypatch):
    """Days saved before ISINs were kept get mapped by re-fetching as few
    days as possible."""
    market.execute("DELETE FROM instruments")
    day = pd.DataFrame([(s, 1.0, 1.0, 1.0, i) for s, i in STOCKS + ETFS],
                       columns=["symbol", "close", "volume", "turnover", "isin"])
    fetched = []
    monkeypatch.setattr(bc, "fetch_day", lambda d: fetched.append(d) or day)
    assert bc.backfill_isins(market, pause=0, verbose=False) == 0
    assert len(fetched) == 1
    assert "LIQUIDCASE" in bc.non_equity_symbols(market)


def test_live_ranking_excludes_a_held_etf():
    """Live panels include holdings. A held liquid ETF has near-zero volatility
    and so the best risk-adjusted momentum -- it must not be ranked."""
    cfg = timing.load_config()
    d = dt.date(2026, 9, 8)
    panel = pd.DataFrame({
        "date": [d] * 3, "symbol": ["LIQUIDCASE", "STK0", "STK1"],
        "ram": [40.0, 2.0, 1.0], "mom": [6.5, 30.0, 20.0],
        "close": [1050.0, 110.0, 120.0], "sma200": [1020.0, 100.0, 100.0],
    })
    eligible = {"STK0", "STK1"}
    assert list(mom.rank_on(panel, d, cfg, eligible=eligible)["symbol"]) == \
        ["STK0", "STK1"]
    assert "LIQUIDCASE" not in list(
        combined.combined_rank(panel, d, cfg, None, eligible=eligible)["symbol"])
