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
    # 7-Oct is 14 sessions before 27-Oct: spots stored, no chain
    n, c = fb.save_day(con, DAY, norm)
    assert n == 2 and c == 0
    n, c = fb.save_day(con, DAY, norm, offsets=(14,))
    assert c == 2
    obs = fb.observations(con)
    assert set(obs["symbol"]) == {"NIFTY", "RELIANCE"} and (obs["sessions"] == 14).all()
    assert len(fb.load_chain(con, "2026-10-07", "NIFTY")) > 10
