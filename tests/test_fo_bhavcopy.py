"""F&O bhavcopy -> live-shaped chains, on a trimmed real file (2026-10-07)."""
import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from data import fo_bhavcopy as fb
from strategy import fno

FIX = Path(__file__).parent / "fixtures" / "fno" / "fo_bhav_20261007.csv"
DAY = dt.date(2026, 10, 7)


@pytest.fixture(scope="module")
def norm():
    return fb.normalise(pd.read_csv(FIX, low_memory=False))


def test_near_monthly_comes_from_futures_not_weeklies(norm):
    near = fb.near_monthly(norm, DAY)
    assert near["NIFTY"] == dt.date(2026, 10, 27)       # not the 13-Oct weekly
    assert near["RELIANCE"] == dt.date(2026, 10, 27)


def test_spots_and_lots(norm):
    sp = fb.spots(norm, DAY).set_index("symbol")
    assert sp.loc["NIFTY", "kind"] == "index"
    assert sp.loc["RELIANCE", "lot"] == 500
    assert sp.loc["NIFTY", "spot"] == pytest.approx(22603.05)


def test_chain_oi_is_in_contracts(norm):
    ch = fb.chain_of(norm, "RELIANCE", dt.date(2026, 10, 27))
    raw = norm[(norm.symbol == "RELIANCE") & (norm.expiry == dt.date(2026, 10, 27))
               & (norm.opt == "PE") & (norm.strike == 1160)]
    assert ch.set_index("strike").loc[1160, "pe_oi"] == raw["oi"].iloc[0] / 500


def test_untraded_close_is_nan(norm):
    ch = fb.chain_of(norm, "RELIANCE", dt.date(2026, 10, 27)).set_index("strike")
    assert ch.loc[ch["ce_vol"] == 0, "ce_ltp"].isna().all()
    assert ch.loc[ch["ce_vol"] > 0, "ce_ltp"].notna().all()


def test_chain_runs_through_the_live_analysis(norm):
    ch = fb.chain_of(norm, "NIFTY", dt.date(2026, 10, 27))
    v = fno.analyse("NIFTY", ch, 22603.05, dt.date(2026, 10, 27),
                    dt.datetime(2026, 10, 7, 15, 30))
    assert v.support.strike == 22000        # same wall the live chain showed next morning
    assert v.support.strike < 22603.05 < v.resistance.strike
    assert v.straddle and v.range_low < 22603.05 < v.range_high


def test_save_day_keeps_chains_only_on_offset_days(norm, tmp_path):
    con = fb.connect(tmp_path / "h.db")
    # 7-Oct is 14 sessions before the 27-Oct monthly (no monthly chain), and
    # 4 before NIFTY's 13-Oct weekly (weekly chain kept).
    n, c = fb.save_day(con, DAY, norm)
    assert n == 2 and c == 1
    cyc = con.execute("SELECT DISTINCT symbol, cycle, sessions FROM fo_chain").fetchall()
    assert cyc == [("NIFTY", "weekly", 4)]
    n, c = fb.save_day(con, DAY, norm, offsets=(14,), weekly_offsets=())
    assert c == 2
    obs = fb.observations(con)
    assert set(obs["symbol"]) == {"NIFTY", "RELIANCE"}
    assert len(fb.load_chain(con, "2026-10-07", "NIFTY")) > 10


def test_features_straddle_iv_wings_and_range(norm):
    ft = fb.features(norm, DAY).set_index("symbol")
    n = ft.loc["NIFTY"]
    assert n["days_left"] == 20 and n["sessions_left"] == 14
    assert 5 < n["iv_straddle"] < 40
    # puts priced richer than calls: the usual index skew
    assert n["iv_put_wing"] > n["iv_call_wing"]
    assert n["spot_low"] <= n["spot"] <= n["spot_high"]
    assert ft.loc["RELIANCE", "next_expiry"] == dt.date(2026, 11, 23)


def test_holidays_reduce_sessions(norm):
    ft = fb.features(norm, DAY, holidays=[dt.date(2026, 10, 20)]).set_index("symbol")
    assert ft.loc["NIFTY", "sessions_left"] == 13


def test_old_format_indices_only_with_spot_from_index():
    raw = pd.DataFrame({
        "INSTRUMENT": ["FUTIDX", "OPTIDX", "OPTIDX", "OPTSTK"],
        "SYMBOL": ["NIFTY", "NIFTY", "NIFTY", "INFY"],
        "EXPIRY_DT": ["25-Feb-2021"] * 4, "STRIKE_PR": [0, 13600, 13600, 1300],
        "OPTION_TYP": ["XX", "CE", "PE", "CE"], "OPEN": 1, "HIGH": [13800, 400, 300, 5],
        "LOW": [13500, 300, 200, 4], "CLOSE": [13700, 350, 250, 4.5],
        "SETTLE_PR": 0, "CONTRACTS": [100, 50, 60, 1], "VAL_INLAKH": 0,
        "OPEN_INT": [1000, 75000, 90000, 10], "CHG_IN_OI": [0, 750, -75, 0],
        "TIMESTAMP": "29-JAN-2021"})
    n = fb.normalise_old(raw, {"NIFTY": 13634.6})
    assert set(n["symbol"]) == {"NIFTY"} and len(n) == 3
    assert (n["spot"] == 13634.6).all() and n["lot"].isna().all()
    ft = fb.features(n, dt.date(2021, 1, 29)).set_index("symbol")
    assert ft.loc["NIFTY", "straddle"] == 600
