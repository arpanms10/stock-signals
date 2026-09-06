"""Full-market bhavcopy normalisation and point-in-time universe selection."""
import datetime as dt

import pandas as pd
import pytest

from data import bhavcopy as bc


def old_format():
    """Pre-July-2024 bhavcopy."""
    return pd.DataFrame({
        "SYMBOL": ["RELIANCE", "TCS", "SOMEBOND", "TINYCO"],
        "SERIES": ["EQ", "EQ", "N2", "BE"],
        "CLOSE": [1200.0, 3400.0, 985.0, 12.0],
        "TOTTRDQTY": [1e6, 5e5, 100.0, 900.0],
        "TOTTRDVAL": [1.2e9, 1.7e9, 98500.0, 10800.0],
        "OPEN": [1190.0, 3390.0, 985.0, 12.0],
    })


def udiff_format():
    """UDiFF, from July 2024."""
    return pd.DataFrame({
        "TckrSymb": ["RELIANCE", "TCS", "SOMEBOND"],
        "SctySrs": ["EQ", "EQ", "N2"],
        "ClsPric": [1300.0, 3500.0, 990.0],
        "TtlTradgVol": [2e6, 6e5, 50.0],
        "TtlTrfVal": [2.6e9, 2.1e9, 49500.0],
    })


def test_old_format_keeps_only_eq_series():
    out = bc._normalise_old(old_format())
    assert sorted(out["symbol"]) == ["RELIANCE", "TCS"]


def test_udiff_format_keeps_only_eq_series():
    out = bc._normalise_udiff(udiff_format())
    assert sorted(out["symbol"]) == ["RELIANCE", "TCS"]


def test_both_formats_produce_identical_columns():
    """NSE switched format mid-history; nothing downstream should notice."""
    a = bc._normalise_old(old_format())
    b = bc._normalise_udiff(udiff_format())
    assert list(a.columns) == list(b.columns) == ["symbol", "close", "volume", "turnover"]


# ------------------------------------------------------------------ universe

@pytest.fixture
def market(tmp_path):
    """A market where one stock delists partway through and another lists late."""
    con = bc.connect(tmp_path / "m.db")
    start = dt.date(2020, 1, 1)
    for i in range(120):
        d = start + dt.timedelta(days=i)
        if d.weekday() >= 5:
            continue
        rows = [("BIG", 100.0, 1e6, 1e9), ("MID", 50.0, 5e5, 2.5e8),
                ("SMALL", 10.0, 1e4, 1e5)]
        if i < 60:
            rows.append(("DELISTED", 20.0, 3e5, 6e6))   # stops trading at day 60
        if i > 80:
            rows.append(("NEWLIST", 30.0, 4e5, 1.2e7))  # lists at day 80
        df = pd.DataFrame(rows, columns=["symbol", "close", "volume", "turnover"])
        bc.save_day(con, d, df)
    return con


def test_universe_ranks_by_liquidity(market):
    u = bc.universe_on(market, dt.date(2020, 2, 20), top_n=2, min_days=10)
    assert u == ["BIG", "MID"]


def test_delisted_stock_is_present_before_and_absent_after(market):
    """The entire point: a company that stopped trading must still appear in
    the universe on the dates it actually traded."""
    before = bc.universe_on(market, dt.date(2020, 2, 20), top_n=10, min_days=10)
    after = bc.universe_on(market, dt.date(2020, 4, 25), top_n=10, min_days=10)
    assert "DELISTED" in before
    assert "DELISTED" not in after


def test_recently_listed_stock_is_excluded_until_it_has_history(market):
    early = bc.universe_on(market, dt.date(2020, 2, 20), top_n=10, min_days=10)
    assert "NEWLIST" not in early


def test_universe_uses_only_prior_data(market):
    """Selection must be makeable on the day -- no peeking forward."""
    u = bc.universe_on(market, dt.date(2020, 1, 20), top_n=10, min_days=5)
    assert "NEWLIST" not in u


def test_holiday_is_recorded_as_zero_rows(tmp_path):
    con = bc.connect(tmp_path / "h.db")
    bc.save_day(con, dt.date(2026, 1, 26), pd.DataFrame(
        columns=["symbol", "close", "volume", "turnover"]))
    assert dt.date(2026, 1, 26).isoformat() in bc.have_days(con)
    assert con.execute("SELECT COUNT(*) FROM market").fetchone()[0] == 0


def test_etfs_are_excluded_from_the_universe(market):
    """ETFs pass any liquidity filter and, being near-zero volatility, top a
    risk-adjusted momentum ranking -- a liquid-fund ETF is cash with an
    apparently infinite Sharpe. LIQUIDCASE reached a backtest's holdings this
    way before the ISIN filter existed."""
    market.executemany(
        "INSERT OR REPLACE INTO instruments (symbol, isin) VALUES (?,?)",
        [("BIG", "INE001A01001"), ("MID", "INE002A01002"),
         ("SMALL", "INE003A01003"), ("DELISTED", "INE004A01004"),
         ("LIQUIDCASE", "INF247L01AP3")])
    market.commit()
    # Make the ETF the single most liquid instrument in the market.
    for i in range(60):
        d = dt.date(2020, 1, 1) + dt.timedelta(days=i)
        if d.weekday() >= 5:
            continue
        market.execute("INSERT OR REPLACE INTO market VALUES (?,?,?,?,?)",
                       (d.isoformat(), "LIQUIDCASE", 1000.0, 1e7, 1e10))
    market.commit()
    u = bc.universe_on(market, dt.date(2020, 2, 20), top_n=3, min_days=10)
    assert "LIQUIDCASE" not in u
    assert u[0] == "BIG"


def test_universe_unfiltered_when_instrument_map_is_empty(market):
    """No map means no filtering -- degrade to including everything rather
    than silently returning an empty universe."""
    assert bc.equity_symbols(market) == set()
    assert bc.universe_on(market, dt.date(2020, 2, 20), top_n=2, min_days=10)
