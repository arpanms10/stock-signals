"""Price refresh from NSE's full daily file."""
import datetime as dt

import pandas as pd
import pytest

from data import ingest, store
from data.sources import prices

DAILY = """SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER
AAA, EQ, {d}, 100, 101, 105, 99, 104, 104.5, 102.3, 1000, 10.2, 50, 600, 60.00
AAA, BL, {d}, 100, 900, 900, 900, 900, 900, 900, 5, 0.1, 1, -, -
BBB, EQ, {d}, 50, 50, 51, 49, 50.5, 50.5, 50.1, 2000, 10.0, 80, -, -
"""


def test_parse_daily_maps_fields_and_keeps_eq_only():
    df = prices.parse_daily(DAILY.format(d="07-Oct-2026"))
    assert list(df["symbol"]) == ["AAA", "BBB"]               # BL series dropped
    a = df.set_index("symbol").loc["AAA"]
    assert a["date"] == dt.date(2026, 10, 7) and a["close"] == 104.5
    assert a["vwap"] == 102.3 and a["delivery_pct"] == 60.0 and a["trades"] == 50
    assert pd.isna(df.set_index("symbol").loc["BBB", "delivery_qty"])   # "-" is missing


def _db(tmp_path, rows):
    con = store.connect(tmp_path / "p.db")
    con.executemany("INSERT INTO prices (symbol, date, close) VALUES (?,?,1)", rows)
    con.commit()
    return con


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(ingest, "PAUSE_SECONDS", 0)
    monkeypatch.setattr(ingest.ca, "fetch_all", lambda a, b, s: ([], []))


def test_catches_up_pack_and_leaves_laggard(tmp_path, monkeypatch):
    con = _db(tmp_path, [("AAA", "2026-10-05"), ("BBB", "2026-10-05"), ("OLD", "2026-08-03")])
    monkeypatch.setattr(prices, "daily_bars",
                        lambda d: prices.parse_daily(DAILY.format(d=d.strftime("%d-%b-%Y"))))
    done = ingest.refresh_from_daily_files(con, ["AAA", "BBB", "OLD"],
                                           today=dt.date(2026, 10, 7), verbose=False)
    assert done == {"AAA", "BBB"}                               # OLD goes per symbol
    assert store.last_date(con, "stock", "AAA") == dt.date(2026, 10, 7)


def test_failed_day_stops_and_hands_back_everything(tmp_path, monkeypatch):
    con = _db(tmp_path, [("AAA", "2026-10-05"), ("BBB", "2026-10-05")])
    monkeypatch.setattr(prices, "daily_bars",
                        lambda d: None if d == dt.date(2026, 10, 6)
                        else prices.parse_daily(DAILY.format(d=d.strftime("%d-%b-%Y"))))
    done = ingest.refresh_from_daily_files(con, ["AAA", "BBB"],
                                           today=dt.date(2026, 10, 7), verbose=False)
    assert done == set()                                        # per-symbol path takes over
    assert store.last_date(con, "stock", "AAA") == dt.date(2026, 10, 5)   # no hole left


def test_no_file_day_is_skipped(tmp_path, monkeypatch):
    con = _db(tmp_path, [("AAA", "2026-10-05")])
    monkeypatch.setattr(prices, "daily_bars",
                        lambda d: prices.EMPTY_STOCK.copy() if d == dt.date(2026, 10, 6)
                        else prices.parse_daily(DAILY.format(d=d.strftime("%d-%b-%Y"))))
    done = ingest.refresh_from_daily_files(con, ["AAA"], today=dt.date(2026, 10, 7),
                                           verbose=False)
    assert done == {"AAA"} and store.last_date(con, "stock", "AAA") == dt.date(2026, 10, 7)


def test_backfill_sends_only_the_rest_per_symbol(tmp_path, monkeypatch):
    con = _db(tmp_path, [("AAA", "2026-10-05")])
    monkeypatch.setattr(ingest, "backfill_index", lambda *a, **k: 0)
    monkeypatch.setattr(ingest, "refresh_from_daily_files", lambda c, s, verbose=True: {"AAA"})
    per = []
    monkeypatch.setattr(ingest, "backfill_symbol", lambda c, s, y, verbose=True: per.append(s))
    ingest.backfill(con, ["AAA", "NEW"], verbose=False)
    assert per == ["NEW"]
    per.clear()
    ingest.backfill(con, ["AAA", "NEW"], verbose=False, daily=False)
    assert per == ["AAA", "NEW"]
