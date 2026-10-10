"""The backtest's price matrix is continuous across splits, renames and
spells in the trade-for-trade series -- raw bhavcopy closes are not."""
import datetime as dt

import pandas as pd
import pytest

from backtest import pit_engine as pit
from data import bhavcopy as bc

D = [dt.date(2025, 1, 6) + dt.timedelta(days=i) for i in range(5)]   # Mon-Fri


def _day(con, d, rows, series="EQ"):
    df = pd.DataFrame([(s, series, c, 1e5, 1e9, isin) for s, c, isin in rows],
                      columns=["symbol", "series", "close", "volume", "turnover", "isin"])
    if series == "EQ":
        bc.save_day(con, d, df)
    else:
        bc.save_t2t_day(con, d, df)


@pytest.fixture
def con(tmp_path):
    con = bc.connect(tmp_path / "m.db")
    # SPLITCO: 1:1 bonus, ex-date D[2], so the raw close halves.
    # OLDNAME -> NEWNAME on D[3], same ISIN.
    # SURVCO: moved to BE for D[2]-D[3], back to EQ on D[4].
    for i, d in enumerate(D):
        eq = [("SPLITCO", 200.0 if i < 2 else 101.0, "INE001A01011")]
        eq.append(("OLDNAME" if i < 3 else "NEWNAME", 50.0 + i, "INE002A01011"))
        if i not in (2, 3):
            eq.append(("SURVCO", 80.0 + i, "INE003A01011"))
        _day(con, d, eq)
        if i in (2, 3):
            _day(con, d, [("SURVCO", 80.0 + i, "INE003A01011")], series="BE")
    con.execute("INSERT INTO market_actions VALUES (?,?,?,?,?)",
                ("SPLITCO", D[2].isoformat(), 2.0, "bonus", "Bonus 1:1"))
    con.commit()
    return con


def test_split_is_back_adjusted(con):
    px = pit.load_wide(con, D[0], D[-1])
    assert px["SPLITCO"].tolist() == [100.0, 100.0, 101.0, 101.0, 101.0]


def test_rename_is_one_series_under_the_new_ticker(con):
    assert bc.symbol_aliases(con) == {"OLDNAME": "NEWNAME"}
    px = pit.load_wide(con, D[0], D[-1])
    assert "OLDNAME" not in px.columns
    assert px["NEWNAME"].tolist() == [50.0, 51.0, 52.0, 53.0, 54.0]


def test_trade_for_trade_days_are_priced(con):
    px = pit.load_wide(con, D[0], D[-1])
    assert px["SURVCO"].notna().all()


def test_universe_counts_a_renamed_company_once(con):
    u = pit.liquid_universe(con, D[-1], 10, min_days=5)
    assert "NEWNAME" in u and "OLDNAME" not in u
    assert "SURVCO" not in u          # 3 EQ days only: BE days never count
