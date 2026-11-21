"""Overview arithmetic: Decimal throughout, sleeves, 70:30, caps, sectors."""
import datetime as dt
import sqlite3
from decimal import Decimal as D

import pandas as pd

from book import book_view as bv
from book import marks as mk
from book import research_db as rdb
from book import securities as sec
from strategy import overview as ov

CFG = ov.load_config()


def pos(sym, qty, cost, price, bucket="", sector="IT", cap="large", kind="equity",
        prev=None):
    return ov.Position(sym, kind, D(str(qty)), None if cost is None else D(str(cost)),
                       None if price is None else D(str(price)),
                       None if prev is None else D(str(prev)), "2026-11-20",
                       "test", bucket=bucket, sector=sector, cap_band=cap)


def test_totals_are_exact_decimals():
    o = ov.build([pos("A", 3, "0.10", "0.20"), pos("B", 3, "0.20", "0.10")], CFG)
    assert o.total_value == D("0.90") and o.invested == D("0.90")
    assert o.unrealised == D("0.00") and o.return_pct == D("0.00")


def test_pnl_weights_and_day_change():
    o = ov.build([pos("A", 10, 100, 150, "core", prev=140),
                  pos("B", 10, 100, 50, "core", prev=50)], CFG)
    assert o.total_value == D("2000.00") and o.unrealised == D("0.00")
    a = o.positions[0]
    assert a.pnl == D("500.00") and a.pnl_pct == D("50.00")
    assert a.weight_pct == D("75.00")
    assert o.day_change == D("100.00")


def test_reit_is_its_own_sleeve_and_outside_70_30():
    o = ov.build([pos("TCS", 70, 1, 1, "core"), pos("X", 30, 1, 1, "satellite"),
                  pos("EMBASSY-RR", 100, 1, 1, kind=sec.REIT, sector="", cap="")], CFG)
    assert {s["key"]: s["pct"] for s in o.sleeves} == {
        "operating": D("50.00"), "real_assets": D("50.00")}
    cs = o.core_satellite
    assert cs["core_pct"] == D("70.00") and cs["satellite_pct"] == D("30.00")
    assert cs["triggers"] == []


def test_trade_and_blank_count_as_satellite():
    o = ov.build([pos("A", 50, 1, 1, "core"), pos("B", 25, 1, 1, "trade"),
                  pos("C", 25, 1, 1, "")], CFG)
    cs = o.core_satellite
    assert cs["satellite_pct"] == D("50.00") and cs["trade_value"] == D("25.00")
    assert cs["unset"] == ["C"]
    assert any("below 67%" in t for t in cs["triggers"])
    assert any("above 33%" in t for t in cs["triggers"])


def test_position_guidelines_take_the_tightest():
    ps = [pos("BIG", 9, 1, 1, "core"), pos("SAT", 5, 1, 1, "satellite"),
          pos("SMALLCORE", 3, 1, 1, "core", cap="small")] + \
         [pos(f"F{i}", 83 / 20, 1, 1, "core") for i in range(20)]
    o = ov.build(ps, CFG)
    by = {p.symbol: p for p in o.positions}
    assert by["BIG"].over_limit and by["BIG"].limit_pct == 8
    assert by["SAT"].over_limit and by["SAT"].limit_pct == 4
    assert by["SMALLCORE"].limit_pct == 2 and by["SMALLCORE"].over_limit


def test_sector_guideline_and_unclassified():
    o = ov.build([pos("A", 30, 1, 1, sector="Financial Services"),
                  pos("B", 70, 1, 1, sector="")], CFG)
    s = {r["key"]: r for r in o.sectors}
    assert s["Financial Services"]["over_limit"]
    assert not s["Unclassified"]["over_limit"]
    assert any("No NSE industry" in d for d in o.disclosures)


def test_unpriced_is_listed_not_valued():
    o = ov.build([pos("A", 10, 100, 110), pos("SGB", 2, 5000, None, kind=sec.GOLD_BOND)],
                 CFG)
    assert o.total_value == D("1100.00")
    assert o.invested == D("11000.00") and o.priced_cost == D("1000.00")
    assert o.return_pct == D("10.00")
    assert any(d.startswith("SGB not valued") for d in o.disclosures)


def test_top_concentration():
    o = ov.build([pos(str(i), i, 1, 1) for i in range(1, 12)], CFG)
    assert o.top5_pct == ov.pct(D(7 + 8 + 9 + 10 + 11), D(66))


# ------------------------------------------------------- marks and snapshot

def _market(tmp_path):
    con = sqlite3.connect(tmp_path / "market.db")
    con.executescript("""
        CREATE TABLE market (date TEXT, symbol TEXT, close REAL, volume REAL,
                             turnover REAL);
        CREATE TABLE market_t2t (date TEXT, symbol TEXT, series TEXT, close REAL,
                                 volume REAL, turnover REAL);
        CREATE TABLE market_days (date TEXT, rows INTEGER, fetched_at TEXT);
        CREATE TABLE instruments (symbol TEXT, isin TEXT);""")
    days = [f"2026-11-{d:02d}" for d in range(1, 21)]
    con.executemany("INSERT INTO market_days VALUES (?,1,'')", [(d,) for d in days])
    con.executemany("INSERT INTO market VALUES (?,?,?,0,0)",
                    [("2026-11-19", "TCS", 3000), ("2026-11-20", "TCS", 3100),
                     ("2026-10-01", "DEAD", 10), ("2026-11-20", "NIFTYBEES", 280)])
    con.executemany("INSERT INTO instruments VALUES (?,?)",
                    [("TCS", "INE467B01029"), ("NIFTYBEES", "INF204KB14I2")])
    return con


def test_marks_with_sources_and_staleness(tmp_path):
    mcon, rcon = _market(tmp_path), rdb.connect(tmp_path / "r.db")
    fetched = []

    def fake_fetch(d, series):
        fetched.append(d)
        return pd.DataFrame([{"symbol": "EMBASSY", "series": "RR",
                              "close": 420.0 + d.day, "isin": "INE041025011"}])

    secs = [sec.classify(s) for s in ("TCS", "DEAD", "EMBASSY-RR", "SGBJUN31-GB")]
    marks, notes = mk.mark_all(secs, mcon, rcon, fetch=fake_fetch)
    assert marks["TCS"].price == D("3100") and marks["TCS"].prev_close == D("3000")
    assert marks["TCS"].date == "2026-11-20" and "bhavcopy" in marks["TCS"].source
    assert "suspended" in marks["DEAD"].note
    assert marks["EMBASSY-RR"].price == D("440.0")
    assert marks["EMBASSY-RR"].prev_close == D("439.0")
    assert marks["SGBJUN31-GB"].price is None
    assert fetched == [dt.date(2026, 11, 20), dt.date(2026, 11, 19)]
    mk.mark_all(secs, mcon, rcon, fetch=fake_fetch)
    assert len(fetched) == 2                                  # cached


def test_plain_etf_symbol_found_via_isin_map_and_snapshot(tmp_path):
    mcon, rcon = _market(tmp_path), rdb.connect(tmp_path / "r.db")
    holdings = [{"tradingsymbol": "NIFTYBEES", "quantity": 10, "average_price": 250},
                {"tradingsymbol": "TCS", "quantity": 1, "average_price": 3000,
                 "bucket": "core"}]
    ref = {"sector": {"TCS": "IT"}, "cap": {"TCS": "large"}, "name": {},
           "cap_known": True}
    ps, _ = bv.positions(holdings, mcon, rcon, ref, refresh=False)
    assert [p.kind for p in ps] == [sec.ETF, sec.EQUITY]
    o = ov.build(ps, CFG)
    sid = bv.snapshot(o, "test", rcon)
    (snap,) = rdb.snapshots(rcon)
    assert snap["id"] == sid and D(snap["total_value"]) == D("5900.00")
    rows = rdb.snapshot_positions(rcon, sid)
    assert {r["symbol"]: r["price_source"] for r in rows}["TCS"].startswith("NSE")
    assert rdb.audit_trail(rcon)[0]["action"] == "snapshot"
