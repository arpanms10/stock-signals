"""Swing picks record: appended once per date, settled as prices arrive."""
import datetime as dt
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import record_swing as rs
from scoring import timing
from strategy import swing as sw


@pytest.fixture
def cfg():
    return timing.load_config()


D0 = dt.date(2026, 10, 9)


def view(day=D0, n=12, universe="nifty200"):
    t = pd.DataFrame({
        "symbol": [f"S{i}" for i in range(n)],
        "setup": ["pullback"] * n, "score": np.linspace(80, 40, n),
        "close": 100.0, "entry": 100.0, "stop": 96.0, "stop_pct": 4.0,
        "t1": 106.0, "t2": 112.0, "sector": "IT",
        "suggest": [True] * (n - 2) + [False, False]})
    reg = sw.Regime(False, -4.6, 15.0, "unfavourable", "")
    return SimpleNamespace(table=t, as_of=day, universe=universe, regime=reg)


def bars(rows, start=D0):
    """OHLC frame whose first bar is the signal day."""
    dates = [start + dt.timedelta(days=i) for i in range(len(rows))]
    a = np.array(rows, dtype=float)
    return pd.DataFrame({"date": dates, "open": a[:, 0], "high": a[:, 1],
                         "low": a[:, 2], "close": a[:, 3]})


def test_only_suggested_names_are_recorded_and_capped(cfg):
    rows = rs.pick_rows(view(), 10, D0, 21000.0)
    assert len(rows) == 10
    assert [r["rank"] for r in rows] == list(range(1, 11))
    assert rows[0]["symbol"] == "S0" and rows[0]["outcome"] == "pending"
    # Rows 11 and 12 are not suggested (e.g. results due) and stay out.
    assert len(rs.pick_rows(view(), 20, D0)) == 10


def test_a_date_is_recorded_once_unless_forced():
    df = rs.load(rs.Path("/nonexistent.xlsx"))
    df, n = rs.add(df, rs.pick_rows(view(), 10, D0))
    assert n == 10
    df, n = rs.add(df, rs.pick_rows(view(), 5, D0))
    assert n == 0 and len(df) == 10
    df, n = rs.add(df, rs.pick_rows(view(), 5, D0), force=True)
    assert n == 5 and len(df) == 5
    # Another universe on the same date is a separate record.
    df, n = rs.add(df, rs.pick_rows(view(universe="nifty500"), 3, D0))
    assert n == 3 and len(df) == 8


def test_settle_closed_at_t2(cfg):
    f = bars([[99, 99, 98, 99], [99, 101, 99, 100.5], [101, 106.5, 100.5, 106],
              [106, 112.5, 105, 112], [112, 113, 111, 112]])
    row = rs.pick_rows(view(), 1, D0)[0]
    out = rs.settle(row, f, cfg)
    assert out["outcome"] == "closed" and out["exit_reason"] == "t2"
    assert out["exit_date"] == D0 + dt.timedelta(days=3)
    assert out["r_net"] < 2.25            # 2.25R gross, less costs


def test_settle_not_triggered_only_after_the_order_expires(cfg):
    row = rs.pick_rows(view(), 1, D0)[0]
    quiet = [[99, 99.5, 98, 99]] * 6
    assert rs.settle(row, bars(quiet[:2]), cfg)["outcome"] == "pending"
    assert rs.settle(row, bars(quiet), cfg)["outcome"] == "not triggered"


def test_settle_open_trade_is_not_a_result(cfg):
    f = bars([[99, 99, 98, 99], [99, 101, 99, 100.5], [100.5, 102, 100, 101]])
    out = rs.settle(rs.pick_rows(view(), 1, D0)[0], f, cfg)
    assert out["outcome"] == "open" and out["exit_date"] is None


def test_record_round_trip_and_summary(tmp_path, cfg):
    p = tmp_path / "swing_picks.xlsx"
    f = bars([[99, 99, 98, 99], [99, 101, 99, 100.5], [101, 106.5, 100.5, 106],
              [106, 112.5, 105, 112]])
    df = rs.load(p)
    df, _ = rs.add(df, rs.pick_rows(view(n=3), 1, D0))
    df = rs.update_outcomes(df, cfg, frames={"S0": f})
    rs.save(df, p)
    back = rs.load(p)
    assert back.loc[0, "outcome"] == "closed"
    assert back.loc[0, "price_date"] == D0
    s = pd.read_excel(p, sheet_name="Summary")
    assert s.loc[s["group"] == "all", "trades"].iloc[0] == 1
    # Settled rows are left alone on the next update.
    again = rs.update_outcomes(back, cfg, frames={})
    assert again.loc[0, "outcome"] == "closed"
