"""Option rule backtest and its reconciliation, on a tiny synthetic history."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from backtest import option_backtest as ob
from backtest import reconcile as rc
from backtest.costs import OptionCostModel
from data import fo_bhavcopy as fb

ENTRY, EXPIRY = "2025-01-16", "2025-01-30"


def make_db(tmp_path, expiry_close=980.0, lot_at_expiry=50):
    con = fb.connect(tmp_path / "h.db")
    spot_rows = [(ENTRY, "IDX", "index", 1000.0, EXPIRY, 50, "udiff"),
                 ("2025-01-23", "IDX", "index", 990.0, EXPIRY, 50, "udiff"),
                 (EXPIRY, "IDX", "index", expiry_close, EXPIRY, lot_at_expiry, "udiff")]
    con.executemany("INSERT INTO fo_spot (date, symbol, kind, spot, near_expiry, lot, src) "
                    "VALUES (?,?,?,?,?,?,?)", spot_rows)
    # strikes 950..1050; ATM straddle 20+20 = 40; puts/calls priced roughly
    rows = []
    for k in range(950, 1051, 10):
        ce = max(1000 - k, 0) + 20 - abs(1000 - k) * 0.1
        pe = max(k - 1000, 0) + 20 - abs(1000 - k) * 0.1
        rows.append((ENTRY, "IDX", EXPIRY, "monthly", 10, 1000.0, float(k),
                     1000.0, 0, 100, ce, 1000.0, 0, 100, pe))
    con.executemany(f"INSERT INTO fo_chain VALUES ({','.join('?' * 15)})", rows)
    con.commit()
    return con


def test_costs_fill_and_exercise():
    c = OptionCostModel()
    assert c.fill(100, "buy") == pytest.approx(101) and c.fill(100, "sell") == pytest.approx(99)
    assert c.fill(1, "buy") == pytest.approx(1.05)                  # at least a tick
    assert c.exercise_cost(-5, 50) == 0
    assert c.exercise_cost(20, 50) == pytest.approx(20 * 50 * 0.00125)
    assert c.sell_cost(100, 50) > c.buy_cost(100, 50)               # STT is on the sell side


def test_buy_put_one_straddle_below(tmp_path):
    con = make_db(tmp_path, expiry_close=940.0)
    res = ob.run(con, ob.Rule("buy", "PE", "moves:-1", 10, "monthly", "indices"))
    t = res.trades.iloc[0]
    assert t["strike"] == 960.0                                     # 1000 - 40
    prem = 20 - 0.1 * 40                                            # OTM put, 40 below spot
    assert t["close_premium"] == pytest.approx(prem)
    assert t["intrinsic"] == 20.0                                   # 960 - 940
    fill = OptionCostModel().fill(prem, "buy")
    c = OptionCostModel()
    expect = (20 - fill) * 50 - c.buy_cost(fill, 50) - c.exercise_cost(20, 50)
    assert t["pnl"] == pytest.approx(expect, abs=0.01)


def test_sell_call_expires_worthless(tmp_path):
    con = make_db(tmp_path, expiry_close=980.0)
    t = ob.run(con, ob.Rule("sell", "CE", "atm", 10, "monthly", "all")).trades.iloc[0]
    assert t["strike"] == 1000.0 and t["intrinsic"] == 0
    c = OptionCostModel()
    fill = c.fill(20.0, "sell")
    assert t["pnl"] == pytest.approx(fill * 50 - c.sell_cost(fill, 50), abs=0.01)


def test_split_inside_cycle_is_dropped(tmp_path):
    con = make_db(tmp_path, lot_at_expiry=100)
    res = ob.run(con, ob.Rule("buy", "CE", "atm", 10, "monthly", "indices"))
    assert res.trades.empty and res.skipped == {"split/bonus inside the cycle": 1}


def test_universe_and_bad_rules(tmp_path):
    con = make_db(tmp_path)
    assert ob.run(con, ob.Rule(universe="stocks")).trades.empty
    assert len(ob.run(con, ob.Rule(universe=["IDX"])).trades) == 1
    with pytest.raises(ValueError):
        ob.Rule(strike="far").validate()
    with pytest.raises(ValueError):
        ob.Rule(action="hold").validate()


def test_reconcile_flags_a_wrong_close(tmp_path, monkeypatch):
    con = make_db(tmp_path, expiry_close=940.0)
    trades = ob.run(con, ob.Rule("buy", "PE", "moves:-1", 10, "monthly", "indices")).trades

    def files(day, src, symbol, close_premium=16.0):
        norm = pd.DataFrame([{"symbol": "IDX", "instr": "opt",
                              "expiry": dt.date(2025, 1, 30), "strike": 960.0, "opt": "PE",
                              "close": close_premium, "vol": 100}])
        spot = 1000.0 if day == dt.date(2025, 1, 16) else 940.0
        return norm, spot

    monkeypatch.setattr(rc, "_day_files", files)
    ok = rc.options(trades, k=1)
    assert ok["ok"].all() and len(ok) == 4
    monkeypatch.setattr(rc, "_day_files", lambda d, s, y: files(d, s, y, close_premium=17.5))
    bad = rc.options(trades, k=1)
    assert not bad.set_index("check").loc["option close on entry day", "ok"]
    assert "FAILURES" in rc.summary(bad)


def test_settle_index_vs_stock():
    c = OptionCostModel()
    # Index, long, in the money: cash-settled with exercise STT, no sell order.
    pu, cost, how = ob.settle("buy", "index", 10.0, 30.0, 50, c)
    assert how == "cash-settled" and pu == 20.0
    assert cost == pytest.approx(c.buy_cost(10.0, 50) + c.exercise_cost(30.0, 50))
    # Stock, long, in the money: squared off at intrinsic minus slippage.
    pu, cost, how = ob.settle("buy", "equity", 10.0, 30.0, 50, c)
    out = c.fill(30.0, "sell")
    assert how == "squared off" and pu == pytest.approx(out - 10.0)
    assert cost == pytest.approx(c.buy_cost(10.0, 50) + c.sell_cost(out, 50))
    # Stock, short, in the money: bought back above intrinsic.
    pu, cost, how = ob.settle("sell", "equity", 10.0, 30.0, 50, c)
    back = c.fill(30.0, "buy")
    assert how == "squared off" and pu == pytest.approx(10.0 - back)
    # Out of the money: nothing to close, either kind.
    assert ob.settle("sell", "equity", 10.0, 0.0, 50, c)[2] == "expired worthless"


def test_no_trades_explains_why(tmp_path):
    con = make_db(tmp_path)
    r = ob.run(con, ob.Rule(universe=["ZZZ"]))
    assert any("not in the F&O history: ZZZ" in k for k in r.skipped)
    r = ob.run(con, ob.Rule(universe=["IDX"], cycle="weekly", sessions=2))
    assert any("weekly expiries exist for indices only" in k for k in r.skipped)
    r = ob.run(con, ob.Rule(universe=["IDX"], sessions=7))
    assert any("no stored chains 7 sessions" in k for k in r.skipped)


def test_bad_strike_rule_message():
    with pytest.raises(ValueError, match="expected a number"):
        ob.target_kind("moves:abc")
