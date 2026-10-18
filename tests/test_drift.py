"""Reaction-drift book (results/drift.py): slots, costs, delistings, the
equal-weight universe. Synthetic prices only."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from results import drift

CAL = [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(10)]


def frame(closes):
    return pd.DataFrame({"date": CAL[:len(closes)], "close": closes})


def test_price_matrix_carries_last_price_after_delisting():
    data = {"A": frame([10, 11, 12]), "B": frame([5.0] * 10)}
    px, live = drift.price_matrix(["A", "B"], lambda s: data[s], CAL)
    assert px["A"].iloc[-1] == 12
    assert live["A"].sum() == 3 and live["B"].all()


def test_simulate_one_trade_pays_both_halves_of_the_cost():
    px = np.array([[10.0], [10], [20], [20], [20]])
    park = np.ones(5)
    trades = pd.DataFrame({"symbol": ["A"], "i0": [1], "i1": [3]})
    r = drift.simulate(trades, px, {"A": 0}, park, slots=2, cost_pct=1.0)
    # Half the book in A: 0.5 x 0.995 doubles, then 0.995 on the sale.
    assert r["nav"][-1] == pytest.approx(0.5 + 0.5 * 0.995 * 2 * 0.995)
    assert r["buys"] == 1 and r["held"].tolist() == [0, 1, 1, 0, 0]


def test_simulate_skips_when_slots_full_or_name_held():
    px = np.ones((5, 3))
    trades = pd.DataFrame({"symbol": ["A", "A", "B", "C"], "i0": [0, 1, 1, 1],
                           "i1": [4, 4, 4, 4]})
    r = drift.simulate(trades, px, {"A": 0, "B": 1, "C": 2}, np.ones(5), 2, 0.0)
    assert r["buys"] == 2 and r["skipped"] == 2


def test_idle_capital_follows_the_park_index():
    px = np.ones((3, 1))
    park = np.array([100.0, 110, 121])
    r = drift.simulate(pd.DataFrame(columns=["symbol", "i0", "i1"]), px, {"A": 0},
                       park, 5, 0.35)
    assert r["nav"][-1] == pytest.approx(1.21)


def test_ew_universe_and_swap_cost():
    px = pd.DataFrame({"A": [10, 11, 11], "B": [10, 9, 9], "C": [10, 10, 10]},
                      index=CAL[:3], dtype=float)
    member = pd.DataFrame({"A": [1, 1, 0], "B": [1, 1, 1], "C": [0, 0, 1]},
                          index=CAL[:3]).astype(bool)
    r = drift.ew_universe(px, member, cost_pct=1.0)
    assert r.iloc[1] == pytest.approx(0.0)            # +10% and -10%
    assert r.iloc[2] == pytest.approx(-0.01 * 0.5)    # half the names swapped


def test_membership_uses_latest_universe_date():
    members = pd.DataFrame({"date": [CAL[0], CAL[5]], "symbol": ["A", "B"]})
    m = drift.membership(members, CAL, ["A", "B"])
    assert m["A"].iloc[4] and not m["B"].iloc[4]
    assert m["B"].iloc[5] and not m["A"].iloc[5]


def test_entries_keep_rallied_in_broadcast_order():
    ev = pd.DataFrame({"symbol": ["A", "B", "C"], "reaction": ["rallied", "rallied", "muted"],
                       "reaction_date": [CAL[2], CAL[2], CAL[2]],
                       "broadcast": ["2024-01-02 18:00", "2024-01-02 09:00", "x"],
                       "to_date": [CAL[0]] * 3})
    e = drift.entries(ev, CAL, hold=3)
    assert e["symbol"].tolist() == ["B", "A"] and e["i1"].tolist() == [5, 5]


def test_quarterly_and_drawdown():
    idx = [dt.date(2024, 3, 29), dt.date(2024, 4, 1), dt.date(2024, 6, 28)]
    nav = pd.Series([1.0, 0.8, 1.2], index=idx)
    q = drift.quarterly(nav)
    assert q.round(6).tolist() == [0.0, 20.0]
    assert drift.max_drawdown(nav) == -20.0
