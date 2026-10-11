"""Trailing stop between rebalances in the point-in-time momentum engine."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from backtest import pit_engine as pit
from data import bhavcopy as bc

START = dt.date(2025, 1, 1)
CRASH = 70            # session index: three weeks after the rebalance at 63
SYMS = {"AAA": 0.004, "BBB": 0.003, "CCC": 0.002}


def _days(n):
    d = START
    while n:
        if d.weekday() < 5:
            yield d
            n -= 1
        d += dt.timedelta(days=1)


@pytest.fixture
def market(tmp_path):
    """Three steady risers; AAA, the strongest, falls 13% a day for three days."""
    con = bc.connect(tmp_path / "m.db")
    px = {s: 100.0 for s in SYMS}
    for i, d in enumerate(_days(110)):
        for s, g in SYMS.items():
            step = 1 + g + 0.001 * np.sin(i + len(s))
            if s == "AAA" and i in (CRASH, CRASH + 1, CRASH + 2):
                step = 0.87
            px[s] *= step
        bc.save_day(con, d, pd.DataFrame(
            [(s, px[s], 1e6, 1e9, f"INE{k:03d}A01011") for k, s in enumerate(SYMS)],
            columns=["symbol", "close", "volume", "turnover", "isin"]))
    return con


def _cfg(**stop):
    return {"signals": {"regime_sma": 200},
            "momentum_strategy": {"n_hold": 2, "enter_rank": 2, "exit_rank": 2,
                                  "lookback_months": 1, "skip_months": 0,
                                  "vol_window": 20, "rebalance_days": 63, **stop}}


@pytest.fixture(autouse=True)
def long_history(monkeypatch):
    # The 200 DMA filter needs 200 bars; the fixture has 110.
    monkeypatch.setattr(pit, "load_wide", _short_sma(pit.load_wide))


def _run(con, monkeypatch, **stop):
    bench = pd.DataFrame(columns=["date", "close"])
    return pit.run(con, _cfg(**stop), START + dt.timedelta(days=60),
                   START + dt.timedelta(days=200), bench, universe_size=3)


def _short_sma(load):
    """Prepend 200 flat-ish bars below the series so the 200 DMA exists."""
    def wrapped(con, start, end):
        px = load(con, start, end)
        pre = pd.DataFrame(50.0, columns=px.columns,
                           index=pd.bdate_range(end=px.index[0] - pd.Timedelta(days=1),
                                                periods=200))
        return pd.concat([pre, px])
    return wrapped


def test_no_stop_holds_through_the_fall(market, monkeypatch):
    res = _run(market, monkeypatch)
    assert "stops_fired" not in res.metrics


def test_pct_stop_sells_and_waits_in_cash(market, monkeypatch):
    base = _run(market, monkeypatch)
    res = _run(market, monkeypatch, stop="pct", stop_pct=10.0)
    assert res.metrics["stops_fired"] == 1
    # Judged on the first -13% close, sold on the second: missed the third.
    assert res.metrics["max_drawdown_pct"] > base.metrics["max_drawdown_pct"]


def test_refill_buys_the_next_ranked_name(market, monkeypatch):
    res = _run(market, monkeypatch, stop="pct", stop_pct=10.0, stop_refill="next")
    cash = _run(market, monkeypatch, stop="pct", stop_pct=10.0)
    assert res.metrics["stops_fired"] == 1
    # The next-ranked riser earns more than idle cash until the rebalance.
    assert res.equity.iloc[-1] > cash.equity.iloc[-1]


def test_wide_stop_does_not_fire(market, monkeypatch):
    res = _run(market, monkeypatch, stop="pct", stop_pct=40.0)
    assert res.metrics["stops_fired"] == 0
