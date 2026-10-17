"""Point-in-time universe for the results and picker backtests. Network-free."""
import datetime as dt

import pandas as pd
import pytest

from data import bhavcopy as bc
from picker import engine as pe
from results import pit_universe as pu

D = dt.date


@pytest.fixture
def con(tmp_path):
    c = bc.connect(tmp_path / "market.db")
    days = pd.bdate_range("2024-01-01", "2024-06-28")
    rows = []
    # BIG trades least but is worth the most; MID trades most.
    for sym, close, turnover in (("BIG", 1000.0, 1e8), ("MID", 100.0, 9e8),
                                 ("SMALL", 10.0, 5e8), ("NOFILE", 50.0, 7e8)):
        rows += [(d.date().isoformat(), sym, close, 1e5, turnover) for d in days]
    c.executemany("INSERT INTO market VALUES (?,?,?,?,?)", rows)
    bc.record_isins(c, [("BIG", "INE000A01011"), ("MID", "INE000B01011"),
                        ("SMALL", "INE000C01011"), ("NOFILE", "INE000D01011"),
                        ("OLDNAME", "INE000E01011"), ("NEWNAME", "INE000E01011")])
    return c


def filings(sym, shares, broadcast=dt.datetime(2024, 2, 10, 18)):
    return {"symbol": sym, "to_date": D(2023, 12, 31), "broadcast": broadcast,
            "shares_cr": shares}


def test_mcap_uses_the_share_count_filed_by_the_date():
    shares = [(D(2024, 2, 10), 10.0), (D(2024, 5, 10), 20.0)]
    closes = ([D(2024, 3, 1), D(2024, 6, 3)], [100.0, 100.0])
    assert pu.mcap_on(shares, closes, D(2024, 3, 1)) == (1000.0, "mcap")
    assert pu.mcap_on(shares, closes, D(2024, 6, 3)) == (2000.0, "mcap")


def test_mcap_needs_a_recent_price():
    shares = [(D(2024, 2, 10), 10.0)]
    closes = ([D(2024, 3, 1)], [100.0])
    assert pu.mcap_on(shares, closes, D(2024, 3, 20)) == (None, "")


def test_mcap_before_the_first_filing_unless_a_split_intervenes():
    shares = [(D(2024, 2, 10), 10.0)]
    closes = ([D(2024, 1, 5)], [100.0])
    assert pu.mcap_on(shares, closes, D(2024, 1, 5)) == (1000.0, "mcap_early")
    # A 1:1 bonus between the date and the filing: the count would be double.
    assert pu.mcap_on(shares, closes, D(2024, 1, 5), [D(2024, 1, 20)]) == (None, "")


def test_members_rank_by_market_cap_and_fill_gaps_by_turnover(con):
    pool = {D(2024, 6, 28): ["MID", "NOFILE", "SMALL", "BIG"]}
    q = pd.DataFrame([filings("BIG", 10.0), filings("MID", 5.0), filings("SMALL", 2.0)])
    df = pu.members(pool, q, con, size=2)
    assert list(df["symbol"]) == ["BIG", "MID"]           # 10,000 cr and 500 cr
    assert list(df["turnover_rank"]) == [4, 1]
    df = pu.members(pool, q, con, size=4)
    assert list(df["symbol"]) == ["BIG", "MID", "SMALL", "NOFILE"]
    assert df.set_index("symbol").at["NOFILE", "basis"] == "turnover"


def test_pool_is_by_turnover(con):
    pools = pu.pools(con, [D(2024, 6, 28)], size=2)
    assert pools[D(2024, 6, 28)] == ["MID", "NOFILE"]


def test_members_on_a_date_are_the_latest_list_before_it():
    df = pd.DataFrame({"date": [D(2024, 3, 31), D(2024, 6, 30)],
                       "symbol": ["A", "B"]})
    assert pu.on(df, D(2024, 5, 1)) == {"A"}
    assert pu.on(df, D(2024, 6, 30)) == {"B"}
    assert pu.on(df, D(2024, 1, 1)) == set()


def test_market_prices_join_renames_and_adjust_splits(con):
    con.executemany("INSERT INTO market VALUES (?,?,?,?,?)", [
        ("2024-07-01", "OLDNAME", 200.0, 1, 1), ("2024-07-02", "OLDNAME", 200.0, 1, 1),
        ("2024-07-03", "NEWNAME", 100.0, 1, 1), ("2024-07-04", "NEWNAME", 100.0, 1, 1)])
    con.execute("INSERT INTO market_actions VALUES ('OLDNAME', '2024-07-03', 2.0, "
                "'split', 'split 2:1')")
    mp = pu.MarketPrices(con)
    raw = mp.load("NEWNAME", adjusted=False)
    assert list(raw["close"]) == [200.0, 200.0, 100.0, 100.0]
    adj = mp.load("NEWNAME")
    assert list(adj["close"]) == [100.0, 100.0, 100.0, 100.0]
    assert (adj["high"] == adj["close"]).all() and adj["open"].isna().all()


def test_prices_use_the_loader_and_keep_the_last_price_when_delisted():
    frame = pd.DataFrame({"date": [D(2024, 1, 1), D(2024, 1, 2)],
                          "close": [100.0, 80.0]})
    adj = pe.Prices(None, adjusted=True, loader=lambda s, a: frame)
    # Delisted on 2 Jan: the holder is stuck at the last price.
    assert pe._period_return(adj, ["GONE"], D(2024, 1, 1), D(2024, 3, 1)) == \
        {"GONE": pytest.approx(-20.0)}
