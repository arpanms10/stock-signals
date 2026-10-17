"""Quarterly results engine: period-aware parsing and the scorecard.

Network-free. The XBRL below is reduced from real NSE integrated filings
(TCS, HDFCBANK, BAJFINANCE, Oct 2026), keeping the structure that matters:
the same tag repeated across quarter and year-to-date contexts.
"""
import datetime as dt

import pytest
import yaml

from results import filings as rf
from results import scorecard as sc

CFG = yaml.safe_load(open("config/scoring.yaml"))["results"]
NS = ('xmlns:xbrli="http://www.xbrl.org/2003/instance" '
      'xmlns:xbrldi="http://xbrl.org/2006/xbrldi" '
      'xmlns:in-capmkt="http://www.sebi.gov.in/xbrl/2025-05-31/in-capmkt"')


def ctx(cid, start, end, dim=False):
    member = ('<xbrli:segment><xbrldi:explicitMember dimension="x">'
              'in-capmkt:OtherExpenses1Member</xbrldi:explicitMember></xbrli:segment>'
              if dim else "")
    return (f'<xbrli:context id="{cid}"><xbrli:entity>{member}</xbrli:entity>'
            f'<xbrli:period><xbrli:startDate>{start}</xbrli:startDate>'
            f'<xbrli:endDate>{end}</xbrli:endDate></xbrli:period></xbrli:context>')


def fact(tag, cid, v):
    return f'<in-capmkt:{tag} contextRef="{cid}">{v}</in-capmkt:{tag}>'


def filing(facts_ytd_first=False):
    contexts = (ctx("FourD", "2026-04-01", "2026-09-30")
                + ctx("OneD", "2026-07-01", "2026-09-30")
                + ctx("OneExpenses1D", "2026-07-01", "2026-09-30", dim=True))
    ytd = fact("RevenueFromOperations", "FourD", "1454630000000")
    q = fact("RevenueFromOperations", "OneD", "731880000000")
    body = (ytd + q) if facts_ytd_first else (q + ytd)
    body += fact("OtherIncome", "OneExpenses1D", "999") \
        + fact("OtherIncome", "OneD", "13370000000") \
        + fact("BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
               "OneD", "38.37")
    return f"<xbrli:xbrl {NS}>{contexts}{body}</xbrli:xbrl>"


def test_reads_the_quarter_not_the_year_to_date():
    """First-occurrence parsing returns the half-year when it comes first."""
    nums = rf.parse_quarter(filing(facts_ytd_first=True), dt.date(2026, 9, 30))
    assert nums["revenue"] == pytest.approx(73188.0)


def test_skips_dimensioned_contexts_and_keeps_ratios_unscaled():
    nums = rf.parse_quarter(filing(), dt.date(2026, 9, 30))
    assert nums["other_income"] == pytest.approx(1337.0)
    assert nums["eps"] == pytest.approx(38.37)


def test_q4_filing_yields_the_march_quarter_not_the_full_year():
    xml = (f"<xbrli:xbrl {NS}>" + ctx("FourD", "2025-04-01", "2026-03-31")
           + ctx("OneD", "2026-01-01", "2026-03-31")
           + fact("RevenueFromOperations", "FourD", "2670210000000")
           + fact("RevenueFromOperations", "OneD", "706980000000") + "</xbrli:xbrl>")
    assert rf.parse_quarter(xml, dt.date(2026, 3, 31))["revenue"] == pytest.approx(70698.0)


def test_kind_detection():
    assert rf.kind_of({"interest_expended": 1, "ppop": 1}) == "bank"
    assert rf.kind_of({"interest_earned": 80, "revenue": 100}) == "nbfc"
    assert rf.kind_of({"revenue": 100}) == "company"


def test_quarter_back_crosses_years():
    assert sc.quarter_back(dt.date(2026, 9, 30), 4) == dt.date(2025, 9, 30)
    assert sc.quarter_back(dt.date(2026, 3, 31), 1) == dt.date(2025, 12, 31)
    assert sc.quarter_back(dt.date(2026, 6, 30), 3) == dt.date(2025, 9, 30)


def test_growth_from_a_loss_is_not_a_number():
    assert sc.growth(10, -5) is None
    assert sc.growth(110, 100) == 10.0


def q(end, revenue, pbt=None, other=0.0, exc=0.0, fin=10.0, dep=20.0, pat=None,
      kind="company", **kw):
    pbt = revenue * 0.2 if pbt is None else pbt
    return {"symbol": "X", "to_date": end, "quarter": "Q", "kind": kind,
            "basis": "consolidated", "revenue": revenue, "pbt": pbt,
            "other_income": other, "exceptional": exc, "finance_costs": fin,
            "depreciation": dep, "pat": pbt * 0.75 if pat is None else pat, **kw}


def series(revenues, **latest):
    """Quarterly revenues oldest -> newest, ending Sep 2026; returns newest first."""
    ends = [sc.quarter_back(dt.date(2026, 9, 30), n) for n in range(len(revenues))][::-1]
    rows = [q(e, r) for e, r in zip(ends, revenues)]
    rows[-1] = q(ends[-1], revenues[-1], **latest)
    return rows[::-1]


def test_accelerating_grower_with_better_margins_is_strong():
    # ~10% YoY for three quarters, then 30%, with PBT margin up.
    s = series([100, 100, 100, 100, 110, 110, 110, 130], pbt=40.0)
    card = sc.score(s, CFG)
    assert card["revenue_yoy"] == 30.0
    assert card["revenue_yoy_trend"] == 10.0
    assert card["revenue_accel_pp"] == 20.0
    assert card["verdict"] == "strong"


def test_shrinking_revenue_and_margins_is_weak():
    s = series([100, 100, 100, 100, 100, 100, 100, 85], pbt=5.0)
    card = sc.score(s, CFG)
    assert card["revenue_yoy"] == -15.0
    assert card["margin_yoy_bps"] < -150
    assert card["verdict"] == "weak"


def test_profit_from_other_income_is_flagged_and_costs_a_point():
    s = series([100] * 8, pbt=30.0, other=15.0)
    card = sc.score(s, CFG)
    assert "other income is 50% of PBT" in card["flags"]
    assert "quality-1" in card["points_detail"]


def test_missing_base_quarter_blanks_yoy_instead_of_guessing():
    s = series([100, 110])         # two quarters: no year-ago filing
    card = sc.score(s, CFG)
    assert card["revenue_yoy"] is None
    assert "YoY blank" in card["flags"]
    assert card["revenue_qoq"] == 10.0


def test_bank_card_uses_nii_and_gnpa_movement():
    def bank(end, ie, ix, gnpa, pat):
        return {"symbol": "B", "to_date": end, "quarter": "Q", "kind": "bank",
                "interest_earned": ie, "interest_expended": ix, "ppop": 100.0,
                "provisions": 10.0, "pat_bank": pat, "gnpa_pct": gnpa}
    ends = [sc.quarter_back(dt.date(2026, 9, 30), n) for n in range(5)]
    rows = [bank(ends[0], 300, 100, 0.0111, 120)] + \
           [bank(e, 270, 100, 0.0117, 100) for e in ends[1:]]
    card = sc.score(rows, CFG)
    assert card["nii_yoy"] == pytest.approx(17.6)
    assert card["gnpa"] == 1.11 and card["gnpa_qoq_bps"] == -6
    assert card["verdict"] == "strong"         # nii +1, pat +1


def test_zero_attributable_profit_beside_a_profit_is_not_filed():
    """APLAPOLLO files profit attributable to owners as 0.00 every quarter."""
    assert sc.owners_pat(0.0, 263.1) == 263.1
    assert sc.owners_pat(250.0, 263.1) == 250.0
    assert sc.owners_pat(None, 263.1) == 263.1


def test_zero_gnpa_is_treated_as_not_filed():
    """Consolidated bank filings carry GNPA as 0.00 -- not a perfect book."""
    assert sc.lender_lines({"kind": "bank", "gnpa_pct": 0.0})["gnpa"] is None


def test_history_keeps_one_basis_and_reads_banks_standalone(monkeypatch):
    def rec(end, cons):
        return {"toDate": end, "consolidated": "Consolidated" if cons else
                "Non-Consolidated", "xbrl": f"{end}-{cons}"}
    filings = [rec("30-Sep-2026", True), rec("30-Sep-2026", False),
               rec("30-Jun-2026", False)]          # June: standalone only

    def fake(url, end):
        bank = {"interest_expended": 1.0, "ppop": 1.0, "interest_earned": 2.0}
        return dict(bank, gnpa_pct=0.0 if url.endswith("True") else 0.01)
    monkeypatch.setattr(rf, "fetch_quarter", fake)
    rows = rf.history("B", 8, filings=filings)
    assert [r["basis"] for r in rows] == ["standalone", "standalone"]

    monkeypatch.setattr(rf, "fetch_quarter", lambda url, end: {"revenue": 1.0})
    rows = rf.history("C", 8, filings=filings)
    # Standalone covers both quarters, consolidated only one: standalone,
    # for the whole series -- never a consolidated quarter beside it.
    assert [(r["to_date"], r["basis"]) for r in rows] == \
        [(dt.date(2026, 9, 30), "standalone"), (dt.date(2026, 6, 30), "standalone")]
    filings.append(rec("30-Jun-2026", True))
    rows = rf.history("C", 8, filings=filings)
    assert {r["basis"] for r in rows} == {"consolidated"}     # tie: consolidated


# --- reaction ---------------------------------------------------------------

from results import engine, reaction as rx, store  # noqa: E402

import pandas as pd  # noqa: E402

DAYS = [dt.date(2026, 10, d) for d in (12, 13, 14, 15, 16, 19, 20, 21, 22, 23)]


def test_after_hours_filing_reacts_next_session():
    assert rx.reaction_day(dt.datetime(2026, 10, 14, 21, 28), DAYS) == dt.date(2026, 10, 15)


def test_intraday_filing_reacts_same_session():
    assert rx.reaction_day(dt.datetime(2026, 10, 14, 15, 20), DAYS) == dt.date(2026, 10, 14)


def test_weekend_filing_reacts_on_monday():
    assert rx.reaction_day(dt.datetime(2026, 10, 17, 17, 46), DAYS) == dt.date(2026, 10, 19)


def bars(closes, start=dt.date(2026, 8, 1)):
    dates = list(pd.bdate_range(start, periods=len(closes)).date)
    return pd.DataFrame({"date": dates, "open": closes, "high": [c * 1.01 for c in closes],
                         "low": [c * 0.99 for c in closes], "close": closes})


def test_measure_reports_move_atr_and_excess():
    p = bars([100.0] * 30 + [104.0, 105.0, 105.0, 106.0, 108.0])
    idx = p.assign(close=[1000.0] * 30 + [1010.0] * 5)
    broadcast = dt.datetime.combine(p["date"].iloc[29], dt.time(20, 0))
    m = rx.measure(p, broadcast, idx)
    assert m["reaction_date"] == p["date"].iloc[30]
    assert m["day_pct"] == 4.0
    assert m["day_excess_pct"] == 3.0
    assert m["d5_pct"] == 8.0
    assert m["day_atr"] == pytest.approx(2.0, abs=0.1)     # ATR ~2% of price
    assert m["reaction"] == "rallied"


def test_measure_waits_for_prices():
    p = bars([100.0] * 30)
    late = dt.datetime.combine(p["date"].iloc[-1], dt.time(20, 0))
    assert rx.measure(p, late) == {}


def test_expected_quarter():
    assert engine.expected_quarter(dt.date(2026, 10, 17)) == dt.date(2026, 9, 30)
    assert engine.expected_quarter(dt.date(2027, 2, 1)) == dt.date(2026, 12, 31)
    assert engine.expected_quarter(dt.date(2026, 4, 1)) == dt.date(2026, 3, 31)


# --- storage round trip -----------------------------------------------------

def test_cards_survive_a_csv_round_trip(tmp_path):
    """NaN is truthy: read back from CSV, `exceptional or 0.0` kept NaN and
    blanked every derived line. _clean restores None."""
    rows = series([100, 100, 100, 100, 110, 110, 110, 130], pbt=40.0)
    for r in rows:
        r["exceptional"] = None
    path = tmp_path / "q.csv"
    store.save_quarters(rows, path)
    cards = engine.cards_from_quarters(store.quarters(path), CFG)
    newest = max(cards, key=lambda c: c["to_date"])
    assert newest["revenue_yoy"] == 30.0 and newest["verdict"] == "strong"
    # Older quarters get a card only when their year-ago quarter is on file.
    assert len(cards) == 4


def test_revised_filing_replaces_the_original(tmp_path):
    path = tmp_path / "c.csv"
    end = dt.date(2026, 9, 30)
    store.save_cards([{"symbol": "X", "to_date": end, "xbrl": "a", "verdict": "weak"}], path)
    df = store.save_cards([{"symbol": "X", "to_date": end, "xbrl": "b",
                            "verdict": "strong"}], path)
    assert len(df) == 1 and df.iloc[0]["verdict"] == "strong"
    assert store.scored(store.cards(path), "X", end, "b")
    assert not store.scored(store.cards(path), "X", end, "a")


# --- backtest helpers -------------------------------------------------------

import run_results_backtest as bt  # noqa: E402


def test_forward_returns_from_the_reaction_close():
    p = bars([100.0] * 10 + [110.0] * 70)
    idx = p.set_index("date")["close"] * 0 + 1000.0
    d0 = p["date"].iloc[5]
    f = bt.forward(p, idx, d0)
    assert f["ret_20"] == 10.0 and f["excess_20"] == 10.0
    assert "ret_60" in f
    assert bt.forward(p, idx, p["date"].iloc[-5]) == {}     # horizon not reached


def test_spread_is_taken_within_each_quarter():
    """A quarter where everything rose must not count as the verdict working."""
    rows = []
    for q, base in ((dt.date(2025, 6, 30), 10.0), (dt.date(2025, 9, 30), -10.0)):
        rows += [{"to_date": q, "verdict": "strong", "excess_60": base + 1}] * 3
        rows += [{"to_date": q, "verdict": "weak", "excess_60": base}] * 3
    s = bt.spread_by_quarter(pd.DataFrame(rows), "excess_60")
    assert list(s.round(6)) == [1.0, 1.0]
