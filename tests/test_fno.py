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


def test_stock_pcr_read_against_stock_norms():
    # 0.6 is "bearish" for an index but ordinary for a stock.
    assert fno.pcr_bias(0.6, S, "index") == "bearish"
    assert fno.pcr_bias(0.6, S, "equity") == "neutral"
    assert fno.pcr_bias(0.45, S, "equity") == "bearish"
    assert fno.pcr_bias(0.9, S, "equity") == "bullish"
    assert fno.pcr_bias(1.1, S, "equity") == "stretched"


def test_range_hit_pct_takes_nearest_measured_offset():
    assert fno.range_hit_pct("equity", 4, S) == 61
    assert fno.range_hit_pct("equity", 13, S) == 63
    assert fno.range_hit_pct("index", 25, S) == 58


def test_live_range_is_straddle_not_walls():
    v = _real("reliance", "RELIANCE")
    assert v.range_from == "straddle"
    assert v.range_low == pytest.approx(v.spot - v.straddle)
    assert v.range_high == pytest.approx(v.spot + v.straddle)
    assert v.range_hit_pct is not None


def test_iv_fallback_range_has_no_hit_rate():
    c = chain([(100, 10, 10, {"ce_iv": 20.0, "pe_iv": 20.0})])
    v = fno.analyse("X", c, 100.0, dt.date(2027, 1, 1),
                    dt.datetime(2026, 1, 1, 15, 30), kind="equity")
    assert v.range_from == "IV" and v.range_hit_pct is None
    assert v.range_low < 100 < v.range_high


def _obs(rows):
    """rows: (kind, sessions, spot, straddle, path_min, path_max, close)."""
    return pd.DataFrame([dict(kind=k, sessions=s, spot=sp, straddle=st, path_min=lo,
                              path_max=hi, close=c, adjusted=False)
                         for k, s, sp, st, lo, hi, c in rows])


def test_reach_curve_pools_up_and_down_in_straddle_units():
    obs = _obs([("equity", 5, 100, 10, 95, 120, 110),    # up 2 moves, down 0.5
                ("equity", 10, 100, 10, 80, 101, 85),    # up 0.1, down 2 moves
                ("equity", 20, 100, 10, 50, 150, 50)])   # 20 out: excluded
    c = fno.reach_curve(obs)["equity"]
    assert c["n"] == 2
    z = c["z"].index(1.0)
    assert c["touch"][z] == 0.5           # 2 of the 4 pooled moves went >= 1
    assert c["expiry"][z] == 0.5          # +1.0 and +1.5 (as -ret) of 4 -> 2/4
    assert c["touch"][0] == 1.0


def test_reach_interpolates_and_is_symmetric():
    curve = {"equity": {"n": 10, "z": [0, 1, 2], "touch": [1, .4, .1],
                        "expiry": [.5, .2, .05]}}
    up = fno.reach(100, 10, 115, "equity", curve)
    dn = fno.reach(100, 10, 85, "equity", curve)
    assert up.side == "up" and dn.side == "down"
    assert up.hist_touch == pytest.approx(0.25) == dn.hist_touch
    assert up.moves == pytest.approx(1.5)


def test_reach_model_at_one_straddle():
    # one straddle ~ 0.8 sigma: P(beyond) ~ 1 - N(0.8) ~ 21%
    x = fno.reach(1000, 10, 1010, "index", None)
    assert x.model_expiry == pytest.approx(0.212, abs=0.01)
    assert x.hist_touch is None


def test_reach_none_without_straddle_or_at_spot():
    assert fno.reach(100, None, 110, "equity", None) is None
    assert fno.reach(100, 10, 100, "equity", None) is None


def test_walls_get_reach_when_curve_given():
    curve = {"index": {"n": 1, "z": [0, 4], "touch": [1, 0], "expiry": [.5, 0]}}
    c = d.parse_chain(json.loads((FIX / "nifty_chain.json").read_text()), "NIFTY")
    v = fno.analyse("NIFTY", c.strikes, c.spot, c.expiry, c.timestamp, curve=curve)
    assert v.resistance_reach.side == "up" and v.support_reach.side == "down"
    assert 0 < v.resistance_reach.hist_touch < 1


# ---------------------------------------------------------- pricing, vol

def test_implied_vol_inverts_price_and_rejects_impossible():
    p = fno.bs_price(100, 110, 0.1, 0.065, 0.3, True)
    assert float(fno.implied_vol(p, 100, 110, 0.1, 0.065, True)) == pytest.approx(0.3, abs=1e-6)
    # below intrinsic: no volatility produces it
    assert np.isnan(float(fno.implied_vol(1.0, 100, 90, 0.1, 0.065, True)))


def test_greeks_signs_and_atm_delta():
    c = chain([(95, 1, 1, {"ce_iv": 20.0, "pe_iv": 20.0}),
               (100, 1, 1, {"ce_iv": 20.0, "pe_iv": 20.0}),
               (105, 1, 1, {"ce_iv": 20.0, "pe_iv": 20.0})])
    g = fno.add_greeks(c, 100.0, 30, 6.5).set_index("strike")
    assert 0.5 < g.loc[100, "ce_delta"] < 0.6          # ATM call, small carry
    assert g.loc[100, "ce_delta"] - g.loc[100, "pe_delta"] == pytest.approx(1, abs=0.002)
    assert (g["ce_theta"] < 0).all() and (g["ce_gamma"] > 0).all()
    assert g.loc[95, "ce_prob_itm"] > g.loc[105, "ce_prob_itm"]


def test_greeks_blank_without_iv():
    g = fno.add_greeks(chain([(100, 1, 1)]), 100.0, 30, 6.5)
    assert np.isnan(g["ce_delta"].iloc[0])


def test_straddle_iv_round_trip():
    # a straddle priced off sigma=20% for 73 days returns ~20%
    T = 73 / 365
    strad = fno.STRADDLE_TO_SIGMA * 100 * 0.20 * np.sqrt(T)
    assert fno.straddle_iv(100, strad, 73) == pytest.approx(20.0, rel=1e-6)
    assert fno.straddle_iv(100, None, 73) is None


def test_iv_percentile_needs_history_and_counts_below():
    hist = pd.Series(range(1, 201))          # 1..200
    pct, n = fno.iv_percentile(150.5, hist)
    assert n == 200 and pct == pytest.approx(75.0)
    assert fno.iv_percentile(10, pd.Series(range(50)))[0] is None


def test_realized_vol():
    rng = np.random.default_rng(0)
    closes = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 400))))
    rv = fno.realized_vol(closes, n=399)
    assert rv == pytest.approx(100 * 0.01 * np.sqrt(252), rel=0.1)
    assert fno.realized_vol(closes.head(5)) is None


def test_wing_skew_positive_when_puts_richer():
    T, r = fno.year_frac(30), 0.065
    rows = []
    for k in (90, 95, 100, 105, 110):
        iv = 0.25 if k < 100 else 0.20
        rows.append((k, 1, 1, {"ce_ltp": float(fno.bs_price(100, k, T, r, iv, True)),
                               "pe_ltp": float(fno.bs_price(100, k, T, r, iv, False))}))
    sk = fno.wing_skew(chain(rows), 100.0, 5.0, 30, 6.5)
    assert sk == pytest.approx(5.0, abs=0.05)


def test_sessions_to_skips_holidays():
    assert fno.sessions_to(dt.date(2026, 10, 8), dt.date(2026, 10, 27)) == 13
    assert fno.sessions_to(dt.date(2026, 10, 8), dt.date(2026, 10, 27),
                           [dt.date(2026, 10, 20)]) == 12


def test_results_warning():
    c = d.parse_chain(json.loads((FIX / "reliance_chain.json").read_text()), "RELIANCE")
    v = fno.analyse("RELIANCE", c.strikes, c.spot, c.expiry, c.timestamp,
                    results=[dt.date(2026, 10, 17)])
    assert any("Results due 17-Oct" in w for w in v.warnings)


def test_conditional_note_only_for_measured_conditions():
    cond = {"overall": 61.0, "low_iv_pct": {"below": 20, "n": 3000, "pct": 56.0},
            "results_in_cycle": {"true": {"n": 1600, "pct": 57.4}}}
    c = d.parse_chain(json.loads((FIX / "reliance_chain.json").read_text()), "RELIANCE")
    hist = pd.Series(np.linspace(10, 60, 200))            # today's IV is low vs this
    v = fno.analyse("RELIANCE", c.strikes, c.spot, c.expiry, c.timestamp,
                    kind="equity", iv_history=hist, results=[dt.date(2026, 10, 17)])
    note = fno.conditional_note(v, cond)
    assert "results inside the cycle it held 57%" in note
    v.vol.iv_pct = 50.0                                     # middle band: not quoted
    assert "IV percentile" not in fno.conditional_note(v, cond)
    v.vol.results = []
    assert fno.conditional_note(v, cond) is None
    v.kind = "index"
    assert fno.conditional_note(v, cond) is None


def test_option_check_put_facts():
    c = d.parse_chain(json.loads((FIX / "nifty_chain.json").read_text()), "NIFTY")
    curve = {"index": {"n": 10, "z": [0, 1, 2, 4], "touch": [1, .5, .2, 0],
                       "expiry": [.5, .25, .08, 0], "touch_intraday": [1, .6, .3, 0]}}
    v = fno.analyse("NIFTY", c.strikes, c.spot, c.expiry, c.timestamp, lot=65,
                    curve=curve, today=dt.date(2026, 10, 8))
    oc = fno.option_check(v, c.strikes, 22000, "PE", lots=2, curve=curve)
    assert oc.breakeven == pytest.approx(22000 - oc.mid)
    assert oc.cost == pytest.approx(oc.mid * 130)
    assert oc.delta < 0 and oc.theta_day < 0 and oc.vega > 0
    assert 0 < oc.p_profit_model < oc.p_itm_model < 0.5     # OTM put, BE further out
    assert oc.p_profit_hist < oc.p_itm_hist
    pl = dict((lbl, p) for lbl, _, p in oc.payoff)
    assert pl["breakeven"] == pytest.approx(0, abs=1e-6)
    assert pl["spot"] == pytest.approx(-oc.mid * 65)          # expires worthless
    assert any("Direction" in n for n in oc.notes)


def test_option_check_itm_call_is_mostly_profitable_side():
    c = d.parse_chain(json.loads((FIX / "nifty_chain.json").read_text()), "NIFTY")
    v = fno.analyse("NIFTY", c.strikes, c.spot, c.expiry, c.timestamp, lot=65,
                    today=dt.date(2026, 10, 8))
    k = float(c.strikes.strike[c.strikes.strike < c.spot - 400].max())   # deep ITM call
    oc = fno.option_check(v, c.strikes, k, "CE")
    assert oc.p_itm_model > 0.5
    assert fno.option_check(v, c.strikes, 1.0, "CE") is None
