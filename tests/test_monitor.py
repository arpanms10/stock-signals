"""Phase 8: monitoring events, the event store and review diffs."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal as D

from book import monitoring as bm
from book import research_db as rdb
from strategy import monitor as mon

CFG = mon.load_config()


def kind(category, text=""):
    r = mon.classify(category, text, CFG["announcements"])
    return r["kind"], r["severity"]


def test_classification_separates_red_flags_from_routine():
    assert kind("Resignation of Statutory Auditors",
                "X has informed the Exchange about resignation of M/s ABC as statutory "
                "auditors") == ("auditor", mon.THESIS)
    assert kind("Change in Auditors", "X has informed the Exchange regarding Change in "
                "Auditors of the company.") == ("auditor_change", mon.WATCH)
    assert kind("Updates", "Report of auditors on financial statements for the quarter "
                "with UDIN")[0] == "routine"
    assert kind("Cessation", "Cessation of Ms A as Managing Director & CEO") == \
        ("leadership", mon.THESIS)
    assert kind("Credit Rating", "ICRA has downgraded the long-term rating to AA-") == \
        ("rating", mon.THESIS)
    assert kind("Credit Rating", "CRISIL has reaffirmed its rating at AAA/Stable")[0] == "routine"
    assert kind("Disclosure", "Disclosure of encumbrance of shares by promoter")[0] == "pledge"
    assert kind("Scheme of Arrangement", "order passed by the Hon'ble NCLT approving the "
                "Scheme")[0] == "restructuring"
    assert kind("News Verification", "The Exchange has sought clarification ... may step "
                "down as chairman")[0] == "news"
    assert kind("Copy of Newspaper Publication", "unaudited financial results")[0] == "routine"


def test_announcement_events_carry_link_and_stable_key():
    ev = mon.announcement_events([{"seq_id": "9", "symbol": "AAA", "at": "2026-11-06 15:23:48",
                                   "category": "Cessation",
                                   "text": "Cessation of Mr B as Chief Financial Officer",
                                   "link": "https://nsearchives.nseindia.in/x.pdf"}], CFG)
    assert ev[0].key == "ann:9" and ev[0].date == "2026-11-06"
    assert ev[0].link.endswith("x.pdf") and ev[0].severity == mon.THESIS


def m(metric, period, end, value, note=""):
    return {"metric": metric, "period": period, "period_end": end,
            "value": None if value is None else D(str(value)), "status": "ok",
            "note": note, "ref": f"https://nse/{metric}/{end}"}


def test_shareholding_events():
    rows = [m("promoter_pct", "instant", "2026-09-30", 48.0),
            m("promoter_qoq_change_pct", "instant", "2026-09-30", -1.5),
            m("promoter_change_pct", "instant", "2026-09-30", -4.0, "change since 2024-12-31"),
            m("pledge_pct", "instant", "2026-09-30", 12.0),
            m("pledge_change_pct", "instant", "2026-09-30", 3.0)]
    ev = {e.key.split(":")[1]: e for e in mon.shareholding_events("AAA", rows, CFG)}
    assert set(ev) == {"promoter_qoq", "promoter_2y", "pledge", "pledge_rise"}
    assert ev["promoter_qoq"].severity == mon.THESIS and "48.00%" in ev["promoter_qoq"].detail
    assert ev["promoter_2y"].severity == mon.WATCH
    calm = [m("promoter_pct", "instant", "2026-09-30", 60.0),
            m("promoter_qoq_change_pct", "instant", "2026-09-30", -0.2),
            m("pledge_pct", "instant", "2026-09-30", 0.0)]
    assert mon.shareholding_events("AAA", calm, CFG) == []


def test_results_compare_year_on_year_not_sequentially():
    rows = [m("pat", "quarter", "2025-09-30", 100), m("pat", "quarter", "2026-06-30", 40),
            m("pat", "quarter", "2026-09-30", 70),
            m("revenue", "quarter", "2025-09-30", 1000),
            m("revenue", "quarter", "2026-09-30", 1100)]
    ev = mon.results_events("AAA", rows, CFG, since="2026-07-01")
    assert len(ev) == 1 and ev[0].key == "res:AAA:2026-09-30"
    assert ev[0].severity == mon.THESIS and ev[0].facts["pat_yoy_pct"] == -30.0
    assert ev[0].link == "https://nse/pat/2026-09-30"
    ok = mon.results_events("AAA", rows[:1] + [m("pat", "quarter", "2026-09-30", 95)]
                            + rows[3:], CFG, since="2026-07-01")
    assert ok[0].severity == mon.WATCH and ok[0].title == "Results filed"


def test_results_lender_npa_and_loss():
    rows = [m("pat", "quarter", "2025-09-30", 50), m("pat", "quarter", "2026-09-30", -5),
            m("gnpa_pct", "quarter", "2025-09-30", 2.0),
            m("gnpa_pct", "quarter", "2026-09-30", 2.7)]
    e = mon.results_events("BNK", rows, CFG, since="2026-07-01")[0]
    assert "a loss" in e.title and "gross NPA up 0.70" in e.title


def snap(as_of, **h):
    return {"as_of": as_of, "holdings": h}


def test_diff_lists_scores_roles_flags_and_plan():
    a = snap("2026-08-21",
             AAA={"score": 70.0, "tier": "core", "role": "core", "bucket": None,
                  "flags": [], "weight_pct": 10.0, "pledge_pct": 0.0,
                  "plan": {"your_buckets": {"buy": 0, "trim": 0},
                           "suggested_roles": {"buy": 0, "trim": 20000}}},
             OLD={"score": 50.0})
    b = snap("2026-11-21",
             AAA={"score": 62.0, "tier": "quality_satellite", "role": "satellite",
                  "bucket": "core", "flags": ["Promoter pledge 12.0% of promoter holding"],
                  "weight_pct": 10.4, "pledge_pct": 12.0,
                  "plan": {"your_buckets": {"buy": 0, "trim": 0},
                           "suggested_roles": {"buy": 0, "trim": 22000}}},
             NEW={"score": 80.0})
    whats = {(c["symbol"], c["what"]) for c in mon.diff(a, b, CFG)}
    assert ("AAA", "score") in whats and ("AAA", "quality tier") in whats
    assert ("AAA", "suggested role") in whats and ("AAA", "your bucket") in whats
    assert ("AAA", "governance flag") in whats and ("AAA", "pledge %") in whats
    assert ("AAA", "weight") not in whats                 # 0.4 pts: under the threshold
    assert ("AAA", "plan (suggested roles)") not in whats  # ₹2k: under the threshold
    assert ("OLD", "holding") in whats and ("NEW", "holding") in whats
    assert mon.diff(None, b, CFG) == []


def test_store_keeps_review_and_note(tmp_path, monkeypatch):
    con = rdb.connect(tmp_path / "r.db")
    con.executescript(bm.SCHEMA)
    fetched = []

    def fake(sym, start, end):
        fetched.append((sym, start))
        return [{"seq_id": "1", "symbol": sym, "at": "2026-11-06 10:00:00",
                 "category": "Cessation", "text": "Cessation of Mr B as CEO", "link": ""}]

    today = dt.date(2026, 11, 21)
    new, probs = bm.refresh_announcements(con, ["AAA"], today, CFG, fetch=fake)
    assert new == 1 and not probs and fetched[0][1] == today - dt.timedelta(days=CFG["lookback_days"])
    new, _ = bm.refresh_announcements(con, ["AAA"], today, CFG, fetch=fake)
    assert new == 0 and fetched[1][1] == today - dt.timedelta(days=CFG["refetch_overlap_days"])
    evs = bm.detect(con, ["AAA"], today, CFG)
    assert bm.record(con, evs) == 1
    bm.set_status(con, "ann:1", "reviewed", "Read it; successor named; thesis intact.")
    assert bm.record(con, evs) == 0
    got = bm.events(con, ["AAA"])[0]
    assert got["status"] == "reviewed" and got["note"].startswith("Read it")

    def boom(sym, start, end):
        raise TimeoutError("nse")
    _, probs = bm.refresh_announcements(con, ["BBB"], today, CFG, fetch=boom)
    assert probs and "BBB" in probs[0]


def test_report_sections():
    s = {"as_of": "2026-11-21", "prices_as_of": "2026-11-20", "holdings": {"AAA": {}}}
    ev = [{"symbol": "AAA", "date": "2026-11-06", "severity": mon.THESIS, "status": "open",
           "title": "Top management change", "detail": "Cessation of CEO", "link": "u",
           "note": ""}]
    txt = bm.report(s, None, ev, [], [])
    assert "Thesis checks open (1)" in txt and "[filing](u)" in txt
    assert "first review" in txt and "not trade signals" in txt
