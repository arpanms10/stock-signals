"""Financials with provenance: filings -> rows, Yahoo, cross-check, ratios."""
import datetime as dt
from decimal import Decimal as D

import pandas as pd

from book import research_db as rdb
from data import enrich
from data import financials as fin
from data import market_stats as ms


def q(end, rev, pat, eps, **kw):
    return {"to_date": end, "basis": "consolidated", "kind": "company",
            "xbrl": f"https://nse/{end}.xml", "revenue": rev, "pat_owners": pat,
            "eps": eps, "pbt": pat * 1.3, "finance_costs": 1.0, "depreciation": 5.0,
            "tax": pat * 0.3, **kw}


def year(fy, rev, pat, eps, **march):
    ends = [dt.date(fy - 1, 6, 30), dt.date(fy - 1, 9, 30), dt.date(fy - 1, 12, 31),
            dt.date(fy, 3, 31)]
    rows = [q(e, rev / 4, pat / 4, eps / 4) for e in ends]
    rows[-1].update(march)
    return rows


def by(rows, metric, period=None, end=None, source=None):
    return [r for r in rows if r["metric"] == metric
            and (period is None or r["period"] == period)
            and (end is None or r["period_end"] == end)
            and (source is None or r["source"] == source)]


def test_quarter_rows_carry_source_and_period():
    rows = fin.from_filings("X", [q(dt.date(2026, 6, 30), 100.0, 10.0, 2.0)])
    (rev,) = by(rows, "revenue")
    assert rev["source"] == fin.NSE and rev["ref"].endswith(".xml")
    assert rev["period"] == "quarter" and rev["period_end"] == "2026-06-30"
    assert rev["method"] == "reported" and rev["confidence"] == fin.HIGH
    (ebitda,) = by(rows, "ebitda")
    assert ebitda["method"] == "calculated" and ebitda["value"] == D("19.0")


def test_fy_sum_and_ttm_need_four_quarters():
    filings = year(2026, 400.0, 40.0, 8.0) + year(2025, 300.0, 30.0, 6.0)[1:]
    rows = fin.from_filings("X", filings)
    (fy26,) = by(rows, "revenue", "fy", "2026-03-31")
    assert fy26["value"] == D("400.0") and "four filed quarters" in fy26["note"]
    assert not by(rows, "revenue", "fy", "2025-03-31")         # only three quarters
    (ttm,) = by(rows, "revenue", "ttm")
    assert ttm["value"] == D("400.0")


def test_eps_put_on_todays_share_count():
    filings = year(2026, 400.0, 40.0, 8.0)
    rows = fin.from_filings("X", filings, actions=[(dt.date(2026, 1, 15), 2.0)])
    q1 = by(rows, "eps", "quarter", "2025-06-30")[0]
    assert q1["value"] == D("1.0") and q1["method"] == "calculated"
    (fy,) = by(rows, "eps", "fy")
    assert fy["value"] == D("5.0")                             # 1 + 1 + 1 + 2


def test_balance_sheet_and_cash_flow_from_march_filing():
    filings = year(2026, 400.0, 40.0, 8.0, equity_owners=200.0, borrowings_current=20.0,
                   borrowings_noncurrent=30.0, cash=10.0, cfo_ytd=50.0,
                   capex_ytd=-15.0, ytd_months=12)
    rows = fin.from_filings("X", filings)
    assert by(rows, "debt")[0]["value"] == D("50.0")
    assert by(rows, "net_debt")[0]["value"] == D("40.0")
    assert by(rows, "debt_equity")[0]["value"] == D("0.25")
    (fcf,) = by(rows, "fcf", "fy")
    assert fcf["value"] == D("35.0")


def _yahoo(values: dict, ends):
    return pd.DataFrame({pd.Timestamp(e): {k: v[i] for k, v in values.items()}
                         for i, e in enumerate(ends)})


def test_yahoo_rows_and_cross_check():
    filings = year(2026, 400.0, 40.0, 8.0) + year(2025, 300.0, 30.0, 6.0)
    rows = fin.from_filings("X", filings)
    inc = _yahoo({"Total Revenue": [401e7, 330e7], "Net Income Common Stockholders":
                  [40e7, 30e7]}, ["2026-03-31", "2025-03-31"])
    rows += fin.from_yahoo("X", {"income": inc})
    issues = fin.cross_check(rows)
    y26 = by(rows, "revenue", "fy", "2026-03-31", fin.YAHOO)[0]
    assert y26["value"] == D("401") and y26["confidence"] == fin.HIGH   # agrees
    y25 = by(rows, "revenue", "fy", "2025-03-31", fin.YAHOO)[0]
    assert y25["confidence"] == fin.LOW and "differs" in y25["note"]
    assert len(issues) == 1 and "revenue FY2025" in issues[0]
    assert by(rows, "pat", "fy", "2026-03-31", fin.YAHOO)[0]["confidence"] == fin.HIGH


def test_ratios_same_source_growth_and_lender_na():
    filings = []
    for fy, k in ((2026, 1.21), (2025, 1.1), (2024, 1.0), (2023, 0.9)):
        filings += year(fy, 400.0 * k, 40.0 * k, 8.0 * k, equity_owners=200.0 * k,
                        borrowings=10.0)
    rows = fin.from_filings("X", filings)
    r = fin.ratios("X", rows)
    g = by(r, "revenue_growth_pct", end="2026-03-31")[0]
    assert g["value"] == D("10.0000") and g["confidence"] == fin.HIGH
    (cagr,) = by(r, "revenue_cagr_3y_pct")
    assert abs(cagr["value"] - D("10.37")) < D("0.05")
    assert by(r, "pat_cagr_5y_pct")[0]["status"] == fin.MISSING
    roe = by(r, "roe_pct", end="2026-03-31")[0]
    assert roe["value"] is not None and "average" in roe["note"]
    lender = fin.ratios("X", rows, lender=True)
    assert by(lender, "debt_equity", end="2026-03-31")[0]["status"] == fin.NA
    assert by(lender, "debt_equity", end="2026-03-31")[0]["value"] is None


def test_market_stats():
    dates = pd.bdate_range("2021-01-01", "2026-11-20")
    px = pd.DataFrame({"close": [100 + i * 0.1 for i in range(len(dates))],
                       "high": [101 + i * 0.1 for i in range(len(dates))],
                       "low": [99 + i * 0.1 for i in range(len(dates))],
                       "volume": 1000.0}, index=dates)
    idx = pd.DataFrame({"close": [1000 + i for i in range(len(dates))]}, index=dates)
    rows = ms.from_prices("X", px, idx)
    names = {r["metric"] for r in rows}
    assert {"high_52w", "low_52w", "avg_volume_20d", "beta_1y", "volatility_1y_pct",
            "return_1y_pct", "return_3y_cagr_pct", "return_5y_cagr_pct"} <= names
    assert all(r["period_end"] == "2026-11-20" for r in rows)
    mc = ms.market_cap("X", 250.0, "2026-11-20",
                       {"value": D("10"), "period_end": "2026-09-30"})
    assert mc[0]["value"] == D("2500.0")
    assert ms.market_cap("X", None, None, None)[0]["status"] == fin.MISSING
    ff = ms.free_float("X", [{"date": "30-SEP-2026", "promoter_pct": 60.0},
                             {"date": "30-JUN-2026", "promoter_pct": 62.0}])
    assert ff[0]["period_end"] == "2026-09-30"
    assert ff[1]["value"] == D("40.0") and ff[1]["confidence"] == fin.LOW
    assert ff[2]["value"] == D("-2.0")


def test_store_keeps_missing_distinct_from_zero(tmp_path):
    con = rdb.connect(tmp_path / "r.db")
    rdb.save_metrics(con, [fin.row("X", "a", "fy", "2026-03-31", 0, "x", "s"),
                           fin.row("X", "b", "fy", "2026-03-31", None, "x", "s")])
    got = {r["metric"]: r for r in rdb.metrics(con, "X")}
    assert got["a"]["value"] == D("0") and got["a"]["status"] == "ok"
    assert got["b"]["value"] is None and got["b"]["status"] == "missing"


def test_refresh_keeps_old_rows_when_a_source_fails(tmp_path, monkeypatch):
    import sqlite3
    rcon = rdb.connect(tmp_path / "r.db")
    mcon = sqlite3.connect(":memory:")
    mcon.executescript("CREATE TABLE market_actions (symbol, ex_date, factor);"
                       "CREATE TABLE market (date, symbol, close);")
    scon = sqlite3.connect(":memory:")
    scon.executescript("CREATE TABLE prices (symbol, date, open, high, low, close, "
                       "prev_close, vwap, volume, trades, delivery_qty, delivery_pct);"
                       "CREATE TABLE index_prices (symbol, date, open, high, low, close);"
                       "CREATE TABLE corporate_actions (symbol, ex_date, factor, kind, "
                       "subject);")
    from data import store
    monkeypatch.setattr(store, "load_actions", lambda con, s: pd.DataFrame())
    good = year(2026, 400.0, 40.0, 8.0)
    enrich.refresh_equity("X", rcon, mcon, scon, log=lambda m: None,
                          fetch_nse=lambda s: good, fetch_yahoo=lambda s: None,
                          fetch_promoter=lambda s: [])

    def boom(s):
        raise RuntimeError("NSE down")
    enrich.refresh_equity("X", rcon, mcon, scon, log=lambda m: None,
                          fetch_nse=boom, fetch_yahoo=lambda s: None,
                          fetch_promoter=lambda s: [])
    assert by(rdb.metrics(rcon, "X"), "revenue", "fy")[0]["value"] == D("400.0")
    last = {(r["source"], r["dataset"]): r for r in rdb.last_retrievals(rcon, "X")}
    assert last[(fin.NSE, "quarterly results")]["status"] == "failed"
    assert "NSE down" in last[(fin.NSE, "quarterly results")]["message"]


def test_zero_owner_profit_tag_does_not_hide_profit():
    f = q(dt.date(2026, 6, 30), 100.0, 0.0, 2.0)
    f["pat"] = 12.0
    rows = fin.from_filings("X", [f])
    assert by(rows, "pat")[0]["value"] == D("12.0")


def test_foreign_currency_statements_kept_apart():
    inc = _yahoo({"Total Revenue": [20e9]}, ["2026-03-31"])
    rows = fin.from_yahoo("INFY", {"income": inc}, currency="USD")
    assert rows[0]["unit"] == "USD cr" and rows[0]["confidence"] == fin.LOW
    rows += fin.from_filings("INFY", year(2026, 178000.0, 29000.0, 70.0))
    assert fin.cross_check(rows) == []
    assert fin.annual_view(rows)["2026-03-31"]["revenue"]["source"] == fin.NSE


def test_payout_ttm_judged_by_retrieval_not_period():
    import datetime as dt
    today = dt.date(2026, 11, 21)
    r = fin.row("X", "dps_ttm", "ttm", "2026-05-15", 10, "₹", fin.YAHOO)
    r["retrieved_at"] = "2026-11-21T10:00"
    assert fin.freshness(r, today, "2026-11-20")[0] == "fresh"
    r["retrieved_at"] = "2026-10-01T10:00"
    assert fin.freshness(r, today, "2026-11-20")[0] == "stale"
