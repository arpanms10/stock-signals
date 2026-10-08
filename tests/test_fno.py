"""F&O analysis: hand-built chains for the arithmetic, real ones for sanity."""
import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from data.sources import nse_derivatives as d
from strategy import fno

FIX = Path(__file__).parent / "fixtures" / "fno"
S = fno.settings()


def chain(rows):
    """rows: (strike, ce_oi, pe_oi) plus optional overrides via dicts."""
    out = []
    for r in rows:
        k, ce, pe = r[:3]
        extra = r[3] if len(r) > 3 else {}
        base = {"strike": float(k), "ce_oi": ce, "pe_oi": pe, "ce_chg_oi": 0.0,
                "pe_chg_oi": 0.0, "ce_vol": 0.0, "pe_vol": 0.0, "ce_iv": np.nan,
                "pe_iv": np.nan, "ce_ltp": np.nan, "pe_ltp": np.nan,
                "ce_bid": np.nan, "ce_ask": np.nan, "pe_bid": np.nan, "pe_ask": np.nan}
        base.update(extra)
        out.append(base)
    return pd.DataFrame(out)


def test_pcr_on_oi():
    c = chain([(90, 100, 300), (100, 100, 100), (110, 200, 0)])
    assert fno.pcr(c)["oi"] == pytest.approx(1.0)


def test_chg_pcr_none_when_a_side_unwinds():
    c = chain([(100, 10, 10, {"ce_chg_oi": -50.0, "pe_chg_oi": 80.0})])
    p = fno.pcr(c)
    assert p["chg_oi"] is None and p["ce_chg_oi"] == -50


def test_chg_pcr_when_both_add():
    c = chain([(100, 10, 10, {"ce_chg_oi": 50.0, "pe_chg_oi": 100.0})])
    assert fno.pcr(c)["chg_oi"] == pytest.approx(2.0)


@pytest.mark.parametrize("v,bias", [(0.5, "bearish"), (1.0, "neutral"),
                                    (1.3, "bullish"), (2.0, "stretched"),
                                    (None, "n/a")])
def test_pcr_bias(v, bias):
    assert fno.pcr_bias(v, S) == bias


def test_max_pain_by_hand():
    # Settle 100: calls at 90 pay 10*100=1000, puts at 110 pay 10*100=1000 -> 2000.
    # Settle 90: puts at 100 pay 10*50 + puts at 110 pay 20*100 = 2500.
    # Settle 110: calls at 90 pay 20*100 + calls at 100 pay 10*20 = 2200.
    c = chain([(90, 100, 0), (100, 20, 50), (110, 0, 100)])
    assert fno.max_pain(c) == 100


def test_walls_only_out_of_the_money():
    # Huge call OI below spot (ITM) and huge put OI above spot must be ignored.
    c = chain([(90, 9999, 500), (95, 0, 800), (105, 700, 0), (110, 300, 9999)])
    sup = fno.walls(c, 100, "pe", S)
    res = fno.walls(c, 100, "ce", S)
    assert [w.strike for w in sup] == [95, 90]
    assert [w.strike for w in res] == [105, 110]


def test_walls_respect_window_and_min_share():
    c = chain([(80, 0, 10_000), (98, 0, 1000), (99, 0, 20)])   # 80 is >10% away
    sup = fno.walls(c, 100, "pe", S)
    assert [w.strike for w in sup] == [98]   # 99 is <5% of the window's puts


def test_wall_change_pct():
    w = fno.Wall(100, oi=120, chg_oi=20, share_pct=50)
    assert w.chg_pct == pytest.approx(20.0)


def test_straddle_prefers_mid_over_ltp():
    c = chain([(100, 1, 1, {"ce_bid": 4.0, "ce_ask": 6.0, "ce_ltp": 9.0,
                            "pe_ltp": 3.0})])
    assert fno.straddle(c, 101) == pytest.approx(5.0 + 3.0)


def test_iv_move():
    assert fno.iv_move(10000, 20, 365) == pytest.approx(2000)


def test_sessions_to_counts_weekdays_after_today():
    thu, nxt_tue = dt.date(2026, 10, 8), dt.date(2026, 10, 13)
    assert fno.sessions_to(thu, nxt_tue) == 3       # Fri, Mon, Tue
    assert fno.sessions_to(nxt_tue, nxt_tue) == 0


@pytest.mark.parametrize("p,oi,label", [(1, 1, "long buildup"), (-1, 1, "short buildup"),
                                        (1, -1, "short covering"), (-1, -1, "long unwinding"),
                                        (0, 1, None)])
def test_buildup(p, oi, label):
    assert fno.buildup(p, oi) == label


def test_range_takes_the_tighter_side_each_way():
    sup, res = fno.Wall(95, 1, 0, 50), fno.Wall(120, 1, 0, 50)
    lo, hi, lf, hf = fno.expected_range(100, 10, sup, res)
    assert (lo, lf) == (95, "support")            # wall inside the move binds
    assert (hi, hf) == (110, "expected move")     # wall beyond it does not


def test_range_without_walls_is_the_move():
    lo, hi, lf, hf = fno.expected_range(100, 10, None, None)
    assert (lo, hi, lf, hf) == (90, 110, "expected move", "expected move")


def _real(name, sym):
    c = d.parse_chain(json.loads((FIX / f"{name}_chain.json").read_text()), sym)
    f = d.parse_futures(json.loads((FIX / f"{name}_quote.json").read_text()))
    return fno.analyse(sym, c.strikes, c.spot, c.expiry, c.timestamp, fut=f)


@pytest.mark.parametrize("name,sym", [("nifty", "NIFTY"), ("reliance", "RELIANCE")])
def test_real_chain_end_to_end(name, sym):
    v = _real(name, sym)
    assert v.support and v.resistance
    assert v.support.strike < v.spot < v.resistance.strike
    assert v.range_low <= v.spot <= v.range_high
    assert v.straddle and 0 < v.straddle < 0.2 * v.spot
    assert v.futures and v.futures.basis > 0          # contango on both, that morning
    assert v.futures.buildup == "short buildup"       # both fell with OI up
    assert v.reasons and all(isinstance(r, str) for r in v.reasons)


def test_near_expiry_warns():
    c = d.parse_chain(json.loads((FIX / "nifty_chain.json").read_text()), "NIFTY")
    v = fno.analyse("NIFTY", c.strikes, c.spot, c.expiry, c.timestamp,
                    today=dt.date(2026, 10, 26))
    assert any("to expiry" in w for w in v.warnings)


def test_snapshot_roundtrip_and_history(tmp_path):
    from data import store
    p = json.loads((FIX / "reliance_chain.json").read_text())
    c = d.parse_chain(p, "RELIANCE")
    f = d.parse_futures(json.loads((FIX / "reliance_quote.json").read_text()))
    con = store.connect(tmp_path / "t.db")
    n = store.save_option_snapshot(con, c, f)
    assert n == len(c.strikes)
    store.save_option_snapshot(con, c, f)              # same NSE time: replaced
    later = d.parse_chain(p, "RELIANCE")
    later.timestamp = c.timestamp + dt.timedelta(minutes=3)
    later.strikes.loc[later.strikes.strike == 1160, "pe_oi"] += 1_000_000
    store.save_option_snapshot(con, later)
    snaps = store.load_option_snapshots(con, "RELIANCE", c.expiry)
    assert len(snaps) == 2 * len(c.strikes)
    # three futures stored, one of them on this expiry
    assert len(store.load_futures_snapshots(con, "RELIANCE", c.expiry)) == 1

    h = fno.history(snaps)
    assert len(h) == 2 and h["ts"].is_monotonic_increasing
    v = fno.analyse("RELIANCE", c.strikes, c.spot, c.expiry, c.timestamp)
    assert h["pcr_oi"].iloc[0] == pytest.approx(v.pcr["oi"])
    assert h["support"].iloc[0] == v.support.strike
    assert h["pcr_oi"].iloc[1] > h["pcr_oi"].iloc[0]
    assert h["support"].iloc[1] == 1160
