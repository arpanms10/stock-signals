"""Import preview: validation, diff against the current file, audited commit."""
import datetime as dt

import pytest

from book import holdings_import as hi
from book import portfolio as pf
from book import research_db as rdb

GRID = [["Symbol", "ISIN", "Quantity Available", "Average Price", "Invested"],
        ["TCS", "INE467B01029", 10, 3000, 30000],
        ["INFY", "INE009A01021", 5, 1500, 9000],          # 7,500 implied
        ["OLDNAME", "INE111A01011", 2, 100, 200],
        ["GONE", "INE222A01011", 1, 50, 50],
        ["HDFCBANK", "INE040A01034", 0, 1600, 0],          # no quantity
        ["NOCOST", "INE333A01011", 4, "", ""],
        ["EMBASSY-RR", "INE041025011", 10, 380, 3800]]

CTX = hi.Context(
    known={"TCS", "INFY", "NEWNAME", "GONE", "NOCOST"},
    isin_symbol={"INE111A01011": "NEWNAME"},
    last_traded={"TCS": "2026-11-20", "INFY": "2026-11-20", "NEWNAME": "2026-11-20",
                 "GONE": "2026-09-01", "NOCOST": "2026-11-20"},
    stale_before="2026-11-06",
    actions={"TCS": [("2026-11-10", "Bonus 1:1")]},
    today=dt.date(2026, 11, 21))


def codes(p, sym):
    return {i.code for i in p.issues if i.symbol == sym}


def test_validation_findings():
    p = hi.preview(GRID, [], CTX, source="broker.xlsx")
    assert "no_quantity" in codes(p, "HDFCBANK")
    assert "invested_mismatch" in codes(p, "INFY")
    assert "invested_mismatch" not in codes(p, "TCS")
    assert "symbol_changed" in codes(p, "OLDNAME")
    assert "not_traded" in codes(p, "GONE")
    assert "no_cost" in codes(p, "NOCOST")
    assert "corporate_action" in codes(p, "TCS")
    assert "non_equity" in codes(p, "EMBASSY-RR")
    assert "unknown_symbol" not in codes(p, "EMBASSY-RR")   # not priced from EQ
    assert p.ok                                             # warnings only


def test_diff_keeps_your_columns():
    current = [{"tradingsymbol": "TCS", "quantity": 8, "average_price": 3000,
                "lt_quantity": None, "bucket": "core", "notes": "mine",
                "purchase_date": ""},
               {"tradingsymbol": "WIPRO", "quantity": 3, "average_price": 400,
                "lt_quantity": None, "bucket": "satellite", "notes": "",
                "purchase_date": ""}]
    p = hi.preview(GRID, current, CTX)
    tcs = next(r for r in p.rows if r["symbol"] == "TCS")
    assert tcs["bucket"] == "core" and tcs["notes"] == "mine"
    assert p.removed == ["WIPRO"] and "removed" in codes(p, "WIPRO")
    assert p.changed == [{"symbol": "TCS", "quantity": (8, 10.0)}]
    assert "EMBASSY-RR" in p.new


def test_lt_above_quantity_is_an_error_and_blocks_commit(tmp_path):
    grid = [["Symbol", "Qty", "Avg cost", "Long term qty"], ["TCS", 5, 3000, 9]]
    p = hi.preview(grid, [], hi.Context())
    assert not p.ok
    with pytest.raises(ValueError):
        hi.commit(p, tmp_path / "holdings.csv")


def test_commit_backs_up_and_audits(tmp_path):
    out = tmp_path / "holdings.csv"
    hi.write_holdings([{"symbol": "TCS", "quantity": 1.0, "avg_price": 1.0,
                        "bucket": "core"}], out)
    p = hi.preview(GRID, pf.read_csv_holdings(out), CTX, source="broker.xlsx")
    con = rdb.connect(tmp_path / "research.db")
    backup = hi.commit(p, out, con)
    assert backup.exists()
    held = {h["tradingsymbol"]: h for h in pf.read_csv_holdings(out)}
    assert held["TCS"]["bucket"] == "core" and held["TCS"]["quantity"] == 10
    assert held["EMBASSY-RR"]["isin"] == "INE041025011"
    assert held["NOCOST"]["average_price"] == 0.0
    (entry,) = rdb.audit_trail(con)
    assert entry["action"] == "import_holdings"
    assert entry["detail"]["source"] == "broker.xlsx"
    assert "EMBASSY-RR" in entry["detail"]["new"]


def test_no_header_is_an_error():
    p = hi.preview([["foo"], ["bar"]], [], hi.Context())
    assert not p.ok and p.errors[0].code == "no_header"


def test_report_lists_everything():
    text = "\n".join(hi.report(hi.preview(GRID, [], CTX, source="b.xlsx")))
    assert "b.xlsx: 6 positions" in text and "warnings" in text


def test_lt_split_kept_when_file_has_none():
    grid = [["Instrument", "Qty.", "Avg. cost"], ["TCS", "10", "3000"], ["INFY", "6", "1500"]]
    current = [{"tradingsymbol": "TCS", "quantity": 10, "average_price": 3000,
                "lt_quantity": 8.0, "st_quantity": 2.0},
               {"tradingsymbol": "INFY", "quantity": 5, "average_price": 1500,
                "lt_quantity": 5.0, "st_quantity": 0.0}]
    p = hi.preview(grid, current, hi.Context())
    tcs = next(r for r in p.rows if r["symbol"] == "TCS")
    assert (tcs["lt_quantity"], tcs["st_quantity"]) == (8.0, 2.0)
    assert p.unchanged == ["TCS"]
    assert "lt_split_lost" in codes(p, "INFY")
