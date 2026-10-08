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
        "ISIN": ["INE002A01018", "INE467B01029", "INE000X00000", "INE999Z01011"],
    })


def udiff_format():
    """UDiFF, from July 2024."""
    return pd.DataFrame({
        "TckrSymb": ["RELIANCE", "TCS", "SOMEBOND"],
        "SctySrs": ["EQ", "EQ", "N2"],
        "ClsPric": [1300.0, 3500.0, 990.0],
        "TtlTradgVol": [2e6, 6e5, 50.0],
        "TtlTrfVal": [2.6e9, 2.1e9, 49500.0],
        "ISIN": ["INE002A01018", "INE467B01029", "INE000X00000"],
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
    assert list(a.columns) == list(b.columns) == ["symbol", "close", "volume", "turnover",
                                               "isin"]


def test_isin_survives_normalisation():
    """The ISIN is the only thing that tells a stock from an ETF; keep it."""
    out = bc._normalise_udiff(udiff_format())
    assert dict(zip(out["symbol"], out["isin"]))["RELIANCE"] == "INE002A01018"


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
        df["isin"] = [f"INE{n:03d}A01001" for n in range(len(df))]
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


def test_universe_refuses_to_run_without_an_instrument_map(tmp_path):
    """An empty map once meant "skip the filter", and with the map never built
    LIQUIDCASE reached the backtest's holdings. It must fail closed."""
    con = bc.connect(tmp_path / "m.db")
    for i in range(20):
        d = dt.date(2020, 1, 1) + dt.timedelta(days=i)
        bc.save_day(con, d, pd.DataFrame(
            [("BIG", 100.0, 1e6, 1e9)],
            columns=["symbol", "close", "volume", "turnover"]))
    assert bc.equity_symbols(con) == set()
    with pytest.raises(bc.InstrumentMapMissing):
        bc.universe_on(con, dt.date(2020, 1, 25), top_n=2, min_days=5)


# ---------------------------------------------------------- failed vs missing

class _Resp:
    def __init__(self, code):
        self.status_code = code


class _HTTP(Exception):
    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.response = _Resp(code)


def _patch(monkeypatch, old, udiff):
    import jugaad_data.nse as jn
    monkeypatch.setattr(jn, "bhavcopy_raw", old)
    monkeypatch.setattr(jn, "bhavcopy_udiff_raw", udiff)


OLD_CSV = "SYMBOL,SERIES,CLOSE,TOTTRDQTY,TOTTRDVAL,ISIN\nAAA,EQ,10,100,1000,INE000A01010\n"
UDIFF_CSV = "TckrSymb,SctySrs,ClsPric,TtlTradgVol,TtlTrfVal,ISIN\nAAA,EQ,10,100,1000,INE000A01010\n"


def _raise(exc):
    def f(d):
        raise exc
    return f


def test_falls_back_to_the_other_format(monkeypatch):
    import datetime as dt
    from data import bhavcopy as bc
    _patch(monkeypatch, _raise(_HTTP(404)), lambda d: UDIFF_CSV)
    df = bc.fetch_day(dt.date(2024, 6, 12))          # old format missing, UDiFF present
    assert list(df["symbol"]) == ["AAA"]


def test_missing_in_both_is_empty_but_a_failure_is_none(monkeypatch):
    import datetime as dt
    import zipfile
    from data import bhavcopy as bc
    _patch(monkeypatch, _raise(_HTTP(404)), _raise(zipfile.BadZipFile()))
    assert bc.fetch_day(dt.date(2023, 1, 2)).empty
    _patch(monkeypatch, _raise(ConnectionError("reset")), _raise(_HTTP(404)))
    assert bc.fetch_day(dt.date(2023, 1, 2)) is None


def test_keep_day_rules():
    import datetime as dt
    import pandas as pd
    from data import bhavcopy as bc
    old = dt.date.today() - dt.timedelta(days=30)
    new = dt.date.today() - dt.timedelta(days=1)
    assert not bc.keep_day(old, None)                 # failed: retry
    assert bc.keep_day(old, pd.DataFrame())           # long missing: record empty
    assert not bc.keep_day(new, pd.DataFrame())       # recent: maybe not published
    assert bc.keep_day(new, pd.DataFrame({"symbol": ["A"]}))


def test_failed_day_is_not_marked_done(tmp_path, monkeypatch):
    import datetime as dt
    from data import bhavcopy as bc
    con = bc.connect(tmp_path / "m.db")
    monkeypatch.setattr(bc, "fetch_day", lambda d: None)
    bc.ingest_range(con, dt.date(2023, 1, 2), dt.date(2023, 1, 3), pause=0, verbose=False)
    assert bc.have_days(con) == set()                 # will be retried


def test_repair_clears_only_non_holiday_weekdays(tmp_path):
    import datetime as dt
    from data import bhavcopy as bc
    con = bc.connect(tmp_path / "m.db")
    con.executemany("INSERT INTO market_days VALUES (?,?,?)",
                    [("2023-01-02", 0, "x"), ("2023-01-26", 0, "x"),   # weekday / holiday
                     ("2023-01-07", 0, "x"), ("2023-01-03", 100, "x")])  # Saturday / full
    cleared = bc.repair_false_empty_days(con, {dt.date(2023, 1, 26)}, verbose=False)
    assert cleared == ["2023-01-02"]
    assert bc.have_days(con) == {"2023-01-26", "2023-01-07", "2023-01-03"}
