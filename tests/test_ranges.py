"""Today ranges: the band maths, the forward log, and its scoring rules."""
import datetime as dt
import math

import numpy as np
import pandas as pd
import pytest

from records import ranges_store as rs
from data import fo_bhavcopy as fb
from strategy import ranges as rg


# ------------------------------------------------------------------ maths

def test_implied_sigma_uses_near_iv_then_next_in_expiry_week():
    s, src = rg.implied_sigma(16.0, 20.0, 10)
    assert src == "near" and s == pytest.approx(0.16 / math.sqrt(252))
    s, src = rg.implied_sigma(16.0, 20.0, 2)
    assert src == "next" and s == pytest.approx(0.20 / math.sqrt(252))
    assert rg.implied_sigma(None, 20.0, 10)[1] == "next"
    assert rg.implied_sigma(float("nan"), None, 10) == (None, "")


def test_band_is_symmetric_in_log_space_and_widens_with_root_time():
    lo1, hi1 = rg.band(100.0, 0.01, 1)
    assert lo1 * hi1 == pytest.approx(100.0 ** 2)
    lo5, hi5 = rg.band(100.0, 0.01, 5)
    assert math.log(hi5 / 100) == pytest.approx(math.sqrt(5) * math.log(hi1 / 100))
    assert rg.band(100.0, None, 1) == (None, None)


def test_realised_sigma_needs_a_full_window():
    c = pd.Series(100 * np.exp(np.cumsum(np.r_[0, np.tile([0.01, -0.01], 15)])))
    assert rg.realised_sigma(c) == pytest.approx(np.std([0.01, -0.01] * 10, ddof=1), rel=1e-6)
    assert rg.realised_sigma(c.iloc[:10]) is None


def test_store_realised_ignores_a_split():
    c = pd.Series([100.0] * 5 + [101, 100, 101, 100] * 5 + [50.5, 50, 50.5])
    s = rs.realised(c)
    assert s is not None and s < 0.02


CAL = {"hit_pct": {"implied|equity|1": 74.0, "implied|equity|1|results": 68.0,
                   "implied|equity|1|clear": 75.0, "implied|index|5": 71.0,
                   "implied|NIFTY|5": 72.0}}


def test_hit_rate_picks_the_most_specific_measure():
    assert rg.hit_rate(CAL, "implied", "equity", 1) == 74.0
    assert rg.hit_rate(CAL, "implied", "equity", 1, results=True) == 68.0
    assert rg.hit_rate(CAL, "implied", "equity", 1, results=False) == 75.0
    assert rg.hit_rate(CAL, "implied", "index", 5, symbol="NIFTY") == 72.0
    assert rg.hit_rate(CAL, "implied", "index", 5, symbol="BANKNIFTY") == 71.0
    assert rg.hit_rate(CAL, "realised", "index", 5) is None
    assert rg.hit_rate(None, "implied", "equity", 1) is None


# -------------------------------------------------------------------- log

def _days(n, start=dt.date(2026, 9, 1)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


DAYS = _days(30)


@pytest.fixture
def con(tmp_path):
    c = fb.connect(tmp_path / "fo.db")
    days = DAYS
    rows = []
    for i, d in enumerate(days):
        px = 100 * (1.01 if i % 2 else 0.99)
        rows.append((d, "AAA", "equity", px, 10, 25.0, 27.0))
        rows.append((d, "NIFTY", "index", 20000.0 + i, 1, 12.0, 13.0))
    c.executemany("INSERT INTO fo_spot (date, symbol, kind, spot, sessions_left, "
                  "iv_straddle, iv_next) VALUES (?,?,?,?,?,?,?)", rows)
    c.execute("INSERT INTO fo_results VALUES ('AAA', ?, 'Results')", (days[26],))
    c.commit()
    return c


def test_build_gives_both_bands_for_every_underlying(con):
    df = rs.build(con, DAYS[25])
    assert set(df["symbol"]) == {"AAA", "NIFTY"}
    a = df.set_index("symbol").loc["AAA"]
    assert a["iv_from"] == "near" and a["iv_used"] == pytest.approx(25.0)
    assert a["implied_low_1"] < a["spot"] < a["implied_high_1"]
    assert a["implied_high_5"] > a["implied_high_1"]
    assert a["realised_low_1"] < a["spot"]
    assert a["results_on"] == DAYS[26]


def test_record_is_append_only(con, tmp_path):
    root = tmp_path / "log"
    df = rs.build(con, DAYS[20])
    assert rs.record(df, root) == 2
    assert rs.record(df, root) == 0
    assert rs.recorded_dates(root) == {DAYS[20]}


def test_score_only_counts_bands_recorded_before_their_close(con, tmp_path):
    root = tmp_path / "log"
    before = dt.datetime.fromisoformat(DAYS[20] + "T19:30:00")
    rs.record(rs.build(con, DAYS[20]), root, now=before)
    late = dt.datetime.fromisoformat(DAYS[22] + "T09:00:00")
    rs.record(rs.build(con, DAYS[21]), root, now=late)
    sc = rs.score(rs.log(root), con)
    # Day 20: both horizons, both methods, both symbols. Day 21's next-session
    # close (day 22) was not yet in at 09:00 -- scored; its 5-session one too.
    d20 = sc[sc["price_date"] == DAYS[20]]
    assert len(d20) == 2 * 2 * 2
    d21 = sc[sc["price_date"] == DAYS[21]]
    assert set(d21["horizon"]) == {1, 5}
    # Recorded after day 22's close: the next-session band is dropped.
    after = dt.datetime.fromisoformat(DAYS[23] + "T19:30:00")
    rs.record(rs.build(con, DAYS[22]), root, now=after)
    sc = rs.score(rs.log(root), con)
    assert set(sc.loc[sc["price_date"] == DAYS[22], "horizon"]) == {5}


def test_score_flags_results_inside_the_window(con, tmp_path):
    root = tmp_path / "log"
    rs.record(rs.build(con, DAYS[25]), root,
              now=dt.datetime.fromisoformat(DAYS[25] + "T19:30:00"))
    sc = rs.score(rs.log(root), con)
    a = sc[(sc["symbol"] == "AAA") & (sc["horizon"] == 1)]
    assert a["results"].all()
    summ = rs.summary(sc, CAL)
    assert {"equity", "index", "NIFTY"} <= set(summ["group"])


def test_today_rows_falls_back_to_realised_for_a_stock_without_options(con):
    closes = pd.Series([100.0, 101, 100, 102, 101] * 5,
                       index=pd.date_range("2026-09-01", periods=25, freq="B").date)
    rows = rs.today_rows(con, ["NIFTY", "NOOPT", "MISSING"],
                         closes_of=lambda s: closes if s == "NOOPT" else None, cal=CAL)
    assert [r["symbol"] for r in rows] == ["NIFTY", "NOOPT"]
    n, x = rows
    assert n["method"] == "implied" and n["iv_from"] == "next"       # 1 session left
    assert n["hit_5"] == 72.0 and n["low_1"] < n["spot"] < n["high_1"]
    assert x["method"] == "realised" and x["price_date"] == str(closes.index[-1])
    assert x["end_1"] > x["price_date"]


def test_sessions_after_skips_weekends_and_holidays():
    assert rs.sessions_after("2026-10-16", 1) == "2026-10-19"
    assert rs.sessions_after("2026-10-16", 1, [dt.date(2026, 10, 19)]) == "2026-10-20"
    assert rs.sessions_after("2026-10-16", 5) == "2026-10-23"


def test_today_rows_flags_results_inside_the_window(con):
    rows = rs.today_rows(con, ["AAA"], cal=CAL)
    # The fixture's latest day is DAYS[29]; its results date (DAYS[26]) is past.
    assert rows[0]["results_1"] is False
    assert rows[0]["hit_1"] == 75.0
