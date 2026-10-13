"""Swing record: CSV store written once per scan, workbook rebuilt from it."""
import datetime as dt
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import record_swing as rs
import swing_store as ss
from scoring import timing
from strategy import swing as sw


@pytest.fixture
def cfg():
    return timing.load_config()


D0 = dt.date(2026, 10, 9)


def view(day=D0, n=12, universe="nifty200", entry=100.0, members=None):
    t = pd.DataFrame({
        "symbol": [f"S{i}" for i in range(n)], "rank": range(1, n + 1),
        "setup": ["pullback"] * (n - 2) + ["", ""],
        "score": np.linspace(80, 40, n),
        "close": 100.0, "entry": entry, "stop": entry - 4, "stop_pct": 4.0,
        "t1": entry + 6, "t2": entry + 12, "sector": "IT", "results_on": None,
        "setup_pullback": [True] * (n - 2) + [False, False],
        "setup_breakout": False, "rsi14": 50.0,
        "suggest": [True] * (n - 2) + [False, False]})
    reg = sw.Regime(False, -4.6, 15.0, "unfavourable", "")
    members = members or {f"S{i}": "IT" for i in range(n + 5)}
    return SimpleNamespace(table=t, as_of=day, universe=universe, regime=reg,
                           sectors=members, results_checked=True,
                           universe_size=len(members), priced=len(members))


def bars(rows, start=D0):
    """OHLC frame whose first bar is the signal day."""
    dates = [start + dt.timedelta(days=i) for i in range(len(rows))]
    a = np.array(rows, dtype=float)
    return pd.DataFrame({"date": dates, "open": a[:, 0], "high": a[:, 1],
                         "low": a[:, 2], "close": a[:, 3]})


ROW = {"price_date": D0, "entry": 100.0, "stop": 96.0}


# --------------------------------------------------------------- the store

def test_scan_is_stored_in_full_with_its_run(tmp_path, cfg):
    assert ss.save_scan(view(), cfg, tmp_path, benchmark_close=21000.0) == 12
    s, r = ss.scans(tmp_path), ss.runs(tmp_path)
    assert len(s) == 12                       # every eligible name, not just the top
    assert s["suggest"].sum() == 10
    assert (tmp_path / "scans" / "2026-10.csv").exists()
    assert len(r) == 1 and r.loc[0, "n_suggested"] == 10
    assert json.loads(r.loc[0, "config_json"]) == cfg["swing"]
    assert r.loc[0, "config_hash"] == ss.config_hash(cfg["swing"])


def test_a_scan_is_recorded_once_unless_forced(tmp_path, cfg):
    assert ss.save_scan(view(), cfg, tmp_path) == 12
    assert ss.save_scan(view(entry=150.0), cfg, tmp_path) == 0
    assert set(ss.scans(tmp_path)["entry"]) == {100.0}
    assert ss.save_scan(view(n=5, entry=150.0), cfg, tmp_path, force=True) == 5
    s = ss.scans(tmp_path)
    assert len(s) == 5 and set(s["entry"]) == {150.0}
    assert len(ss.runs(tmp_path)) == 1
    # Another universe on the same date is its own scan.
    assert ss.save_scan(view(universe="nifty500"), cfg, tmp_path) == 12
    assert len(ss.runs(tmp_path)) == 2


def test_new_dates_append_and_leave_earlier_scans_untouched(tmp_path, cfg):
    ss.save_scan(view(), cfg, tmp_path)
    before = ss.scans(tmp_path)
    d2 = D0 + dt.timedelta(days=3)
    ss.save_scan(view(day=d2, entry=150.0), cfg, tmp_path)
    after = ss.scans(tmp_path)
    assert len(after) == 24
    pd.testing.assert_frame_equal(after[after["price_date"] == D0].reset_index(drop=True),
                                  before)
    # A later month goes to its own file.
    ss.save_scan(view(day=dt.date(2026, 11, 2)), cfg, tmp_path)
    assert sorted(p.name for p in (tmp_path / "scans").glob("*.csv")) == \
        ["2026-10.csv", "2026-11.csv"]


def test_members_are_written_only_when_they_change(tmp_path, cfg):
    ss.save_scan(view(), cfg, tmp_path)
    ss.save_scan(view(day=D0 + dt.timedelta(days=1)), cfg, tmp_path)
    assert ss.members(tmp_path)["since"].nunique() == 1
    changed = {f"S{i}": "IT" for i in range(1, 20)}
    d3 = D0 + dt.timedelta(days=2)
    ss.save_scan(view(day=d3, members=changed), cfg, tmp_path)
    assert ss.members_on(D0 + dt.timedelta(days=1), "nifty200", tmp_path)[0] == "S0"
    assert "S0" not in ss.members_on(d3, "nifty200", tmp_path)
    assert len(ss.members_on(d3, "nifty200", tmp_path)) == 19


# ---------------------------------------------------------------- outcomes

def test_settle_closed_at_t2(cfg):
    f = bars([[99, 99, 98, 99], [99, 101, 99, 100.5], [101, 106.5, 100.5, 106],
              [106, 112.5, 105, 112], [112, 113, 111, 112]])
    out = rs.settle(ROW, f, cfg)
    assert out["outcome"] == "closed" and out["exit_reason"] == "t2"
    assert out["exit_date"] == D0 + dt.timedelta(days=3)
    assert out["r_net"] < 2.25            # 2.25R gross, less costs


def test_settle_not_triggered_only_after_the_order_expires(cfg):
    quiet = [[99, 99.5, 98, 99]] * 6
    assert rs.settle(ROW, bars(quiet[:2]), cfg)["outcome"] == "pending"
    assert rs.settle(ROW, bars(quiet), cfg)["outcome"] == "not triggered"


def test_settle_open_trade_is_not_a_result(cfg):
    f = bars([[99, 99, 98, 99], [99, 101, 99, 100.5], [100.5, 102, 100, 101]])
    out = rs.settle(ROW, f, cfg)
    assert out["outcome"] == "open" and out["exit_date"] is None


def test_outcomes_use_the_plan_the_scan_was_recorded_with(tmp_path, cfg):
    """Changing the config later must not re-score old picks."""
    old = {**cfg, "swing": {**cfg["swing"], "t2_r": 2.0}}
    ss.save_scan(view(n=3), old, tmp_path)
    f = bars([[99, 99, 98, 99], [99, 101, 99, 100.5], [101, 108.5, 100.5, 108]])
    s = ss.outcomes(ss.scans(tmp_path).head(1), {"S0": f}, tmp_path)
    # T2 at 2R = 108 is hit under the recorded plan; at today's 3R it would not be.
    assert s.loc[0, "exit_reason"] == "t2"


# ----------------------------------------------------------------- workbook

def test_workbook_is_rebuilt_from_the_record(tmp_path, cfg):
    root, book = tmp_path / "swing", tmp_path / "swing_picks.xlsx"
    f = bars([[99, 99, 98, 99], [99, 101, 99, 100.5], [101, 106.5, 100.5, 106],
              [106, 112.5, 105, 112]])
    ss.save_scan(view(), cfg, root)
    ss.save_scan(view(day=D0 + dt.timedelta(days=1)), cfg, root)
    df = rs.picks(root, frames={"S0": f})
    rs.save(df, book)
    back = pd.read_excel(book, sheet_name="Picks")
    assert len(back) == 20                     # top 10 of each of two scans
    assert list(back["rank"][:10]) == list(range(1, 11))
    assert back.loc[0, "outcome"] == "closed"
    s = pd.read_excel(book, sheet_name="Summary")
    assert s.loc[s["group"] == "all", "trades"].iloc[0] >= 1


def test_replay_compares_groups_over_recorded_scans(tmp_path, cfg):
    ss.save_scan(view(n=4), cfg, tmp_path)
    f = bars([[99, 99, 98, 99], [99, 101, 99, 100.5], [101, 106.5, 100.5, 106],
              [106, 112.5, 105, 112]])
    res = ss.replay(tmp_path, frames={f"S{i}": f for i in range(4)})
    assert res["scans"] == 1 and res["rows"] == 4
    assert res["groups"]["all eligible"]["trades"] == 4
    assert res["groups"]["top suggested"]["trades"] == 2
