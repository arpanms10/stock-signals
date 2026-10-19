"""The Fundamental Analyst, on fixed data (no network)."""
import datetime as dt
import json

from analysis import fundamental as fa


def company(**cur):
    """Current row plus five prior quarters of steady growth."""
    base = {"symbol": "ACME", "quarter": "FY27Q3", "period": "quarterly",
            "revenue": 120.0, "pat": 12.0, "pat_annual": 45.0, "cfo": 50.0,
            "roe": 22.0, "roce": 26.0, "debt_equity": 0.2, "interest_cover": 15.0,
            "promoter_pct": 60.0, "pledge_pct": 0.0, "pe": 25.0,
            "notes": "nse-results(30-Sep-2026); yahoo(INR)"}
    base.update(cur)
    hist = [{"symbol": "ACME", "quarter": q, "period": "quarterly",
             "revenue": rev, "pat": pat, "notes": "nse-history"}
            for q, rev, pat in [("FY27Q1", 115, 11.5), ("FY26Q4", 110, 11),
                                ("FY26Q3", 105, 10.5), ("FY26Q2", 100, 10),
                                ("FY26Q1", 95, 9.5)]]
    return {"symbol": "ACME", "fetched_at": "2026-10-16T10:00:00",
            "rows": [base] + hist,
            "info": {"trailingPE": 25.0, "priceToBook": 4.0, "dividendYield": 0.012,
                     "marketCap": 5e10},
            "promoter": [{"date": "30-SEP-2026", "promoter_pct": 60.0}]}


def test_healthy_company_is_strong_with_reasons():
    r = fa.analyse(company())
    assert r.stance == "Strong" and r.score >= 70
    assert r.red_flags == []
    assert any(s.startswith("Profitability: ROE 22.0%") for s in r.strengths)
    assert r.kind == "company"
    assert r.as_of == "FY27Q2"          # the results' quarter, not the fetch's


def test_quarter_table_relabels_current_and_computes_yoy():
    r = fa.analyse(company())
    q = r.quarters
    assert [x["quarter"] for x in q[:2]] == ["FY27Q2", "FY27Q1"]
    assert round(q[0]["revenue_yoy_pct"]) == 20       # 120 vs 100 (FY26Q2)
    assert round(q[0]["margin_pct"]) == 10
    assert q[-1]["revenue_yoy_pct"] is None           # nothing a year earlier


def test_red_flag_caps_stance():
    clean = fa.analyse(company())
    pledged = fa.analyse(company(pledge_pct=30.0))
    assert any("pledge" in f for f in pledged.red_flags)
    assert fa.STANCES.index(pledged.stance) > fa.STANCES.index(clean.stance)


def test_cash_and_leverage_flags():
    r = fa.analyse(company(cfo=10.0, debt_equity=2.5, interest_cover=1.5))
    text = " ".join(r.red_flags)
    assert "not arriving as cash" in text
    assert "highly leveraged" in text and "Interest cover" in text


def test_nbfc_not_flagged_for_leverage_or_cash():
    r = fa.analyse(company(cfo=-100.0, debt_equity=4.0), sector="Financial Services")
    assert r.kind == "nbfc"
    assert r.red_flags == []
    assert "Debt / equity" not in r.metrics


def test_valuation_peg():
    v, note = fa.valuation({"trailingPE": 30.0, "dividendYield": 0.01},
                           [{"pat_yoy_pct": 20.0}])
    assert v["P/E"] == 30.0 and v["Dividend yield %"] == 1.0
    assert "rich" in note and "PEG of 1.5" in note
    _, note = fa.valuation({"trailingPE": -5}, [])
    assert "No meaningful P/E" in note


def test_no_data():
    r = fa.analyse({"symbol": "SMEX", "rows": [], "info": {}, "promoter": []})
    assert r.stance == "Not enough data" and r.score is None


def test_cache_is_used_within_a_week(tmp_path, monkeypatch):
    monkeypatch.setattr(fa, "CACHE_DIR", tmp_path)
    data = dict(company(), v=fa.CACHE_VERSION)
    (tmp_path / "fundamentals_ACME.json").write_text(json.dumps(data))
    got = fa.fetch("ACME", now=dt.datetime(2026, 10, 20))
    assert got == data           # fresh enough: no network attempted


def test_cache_from_an_older_version_is_refetched(tmp_path, monkeypatch):
    monkeypatch.setattr(fa, "CACHE_DIR", tmp_path)
    (tmp_path / "fundamentals_ACME.json").write_text(json.dumps(company()))
    from scripts import fetch_fundamentals as ff
    calls = []
    monkeypatch.setattr(ff, "fetch_rows",
                        lambda *a, **k: calls.append(1) or ([], {}))
    monkeypatch.setattr("data.sources.nse_fundamentals.shareholding_history",
                        lambda *a, **k: [])
    got = fa.fetch("ACME", now=dt.datetime(2026, 10, 20))
    assert calls and got["v"] == fa.CACHE_VERSION
