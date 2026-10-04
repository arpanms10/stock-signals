"""Kite export -> holdings.csv conversion."""
import import_kite_holdings as kite
import portfolio as pf


def test_kite_web_csv():
    grid = [["Instrument", "Qty.", "Avg. cost", "LTP", "Cur. val", "P&L"],
            ["RELIANCE", "40", "831.76", "1400", "56000", "22729.6"],
            ["TCS", "58", "3,130.61", "3500", "203000", "21424.62"]]
    rows, _ = kite.convert(grid)
    assert [r["symbol"] for r in rows] == ["RELIANCE", "TCS"]
    assert rows[1]["avg_price"] == 3130.61
    assert rows[0]["lt_quantity"] is None


def test_console_xlsx_preamble_pledges_and_lt_split():
    grid = [["Client ID", "AB1234"], [], ["Equity Holdings Statement"], [],
            [None, "Symbol", "ISIN", "Sector", "Quantity Available",
             "Quantity Discrepant", "Quantity Long Term",
             "Quantity Pledged (Margin)", "Quantity Pledged (Loan)",
             "Average Price"],
            [None, "IRCTC", "INE335Y01020", "Travel", 150, 0, 120, 50, 0, 327.46],
            [None, None],
            [None, "Total", None, None, 9999]]
    rows, _ = kite.convert(grid)
    assert len(rows) == 1
    r = rows[0]
    assert r["quantity"] == 200          # pledged shares counted
    assert (r["lt_quantity"], r["st_quantity"]) == (120, 80)


def test_series_suffix_stripped_and_duplicates_merged():
    grid = [["Instrument", "Qty.", "Avg. cost"],
            ["XYZ", "10", "100"], ["XYZ-BE", "30", "200"]]
    (r,), _ = kite.convert(grid)
    assert r["symbol"] == "XYZ" and r["quantity"] == 40
    assert r["avg_price"] == 175


def test_output_is_readable_and_keeps_buckets(tmp_path):
    out = tmp_path / "holdings.csv"
    kite.write_holdings([{"symbol": "TCS", "quantity": 5.0, "avg_price": 3000.0,
                          "bucket": "core", "notes": "mine"}], out)
    (h,) = pf.read_csv_holdings(out)
    assert h["tradingsymbol"] == "TCS" and h["quantity"] == 5
    assert h["bucket"] == "core" and h["notes"] == "mine"


def test_no_header_gives_nothing():
    assert kite.convert([["foo", "bar"], ["1", "2"]]) == ([], [])


def test_non_equity_skipped_and_reported():
    grid = [["Symbol", "ISIN", "Sector", "Quantity Available", "Average Price"],
            ["TCS", "INE467B01029", "IT", 5, 3000],
            ["BAJAJ-AUTO", "INE917I01010", "AUTO", 2, 8000],
            ["10IIFL28A-NE", "INE530B08094", "DEBT", 10, 1000],
            ["EMBASSY-RR", "INE041025011", "REALTY", 10, 380],
            ["EBBETF0433-F", "INF754K01LE1", "", 10, 1100],
            ["SOMEBOND", "INE530B08102", "FINANCE", 1, 1000]]
    rows, skipped = kite.convert(grid)
    assert [r["symbol"] for r in rows] == ["TCS", "BAJAJ-AUTO"]
    assert dict(skipped) == {
        "10IIFL28A-NE": "debt", "EMBASSY-RR": "REIT",
        "EBBETF0433-F": "ETF / fund",
        "SOMEBOND": "not an equity share (ISIN INE530B08102)"}


def test_web_csv_without_isin_still_catches_series():
    grid = [["Instrument", "Qty.", "Avg. cost"],
            ["NXST-RR", "10", "140"], ["SGBJUN31-GB", "2", "5000"],
            ["INFY", "3", "1500"]]
    rows, skipped = kite.convert(grid)
    assert [r["symbol"] for r in rows] == ["INFY"]
    assert [s for s, _ in skipped] == ["NXST-RR", "SGBJUN31-GB"]
