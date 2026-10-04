"""Cash constraint, sector caps, decision log, universe floors, freshness.

Each of these closes a gap where the framework produced advice that could not
be acted on, or where it stayed quiet about something that mattered.
"""
import datetime as dt

import pandas as pd
import pytest

import fundamentals as fu
from scoring import timing
from strategy import advisor as adv
from strategy import tax as tx


@pytest.fixture
def cfg():
    return timing.load_config()


def q(score):
    return fu.Quality("X", score, {}, [])


# ------------------------------------------------------------- cash constraint

def make_add(sym, qty, pct=1.0):
    return adv.Advice(symbol=sym, bucket="core", action="ADD", qty=qty,
                      current_pct=pct)


def make_sell(sym, qty, value):
    return adv.Advice(symbol=sym, bucket="core", action="EXIT", qty=qty,
                      value=value)


def test_adds_are_funded_from_sells_best_first():
    """Without a budget the advisor emits every under-weight name and leaves
    the arithmetic to the reader."""
    prices = {"GOOD": 100.0, "OK": 100.0, "SELL": 100.0}
    advices = [make_sell("SELL", 10, 1000.0),      # raises 1,000
               make_add("GOOD", 8), make_add("OK", 8)]
    quality = {"GOOD": q(95.0), "OK": q(60.0)}
    out = adv.apply_cash_constraint(advices, prices, quality)
    assert out["raised"] == pytest.approx(1000.0)
    # The better business is funded first and in full.
    good = next(a for a in advices if a.symbol == "GOOD")
    assert good.action == "ADD" and good.qty == 8


def test_unfundable_adds_become_watch_not_silent():
    prices = {"A": 1000.0, "SELL": 100.0}
    advices = [make_sell("SELL", 1, 100.0), make_add("A", 5)]
    out = adv.apply_cash_constraint(advices, prices, {"A": q(90.0)})
    a = next(x for x in advices if x.symbol == "A")
    assert a.action == "WATCH" and a.qty == 0
    assert any("no cash left" in r for r in a.reasons)
    assert len(out["deferred"]) == 1


def test_partial_funding_beats_skipping():
    """Half a position in the best business beats none of it."""
    prices = {"A": 100.0, "SELL": 100.0}
    advices = [make_sell("SELL", 5, 500.0), make_add("A", 10)]
    adv.apply_cash_constraint(advices, prices, {"A": q(90.0)})
    a = next(x for x in advices if x.symbol == "A")
    assert a.action == "ADD" and 0 < a.qty < 10
    assert any("part-funded" in r for r in a.reasons)


def test_cash_on_hand_extends_the_budget():
    prices = {"A": 100.0}
    advices = [make_add("A", 5)]
    out = adv.apply_cash_constraint(advices, prices, {"A": q(90.0)},
                                    cash_available=500.0)
    assert out["budget"] == pytest.approx(500.0)
    assert next(x for x in advices if x.symbol == "A").action == "ADD"


# ------------------------------------------------------------- sector cap

def test_sector_trim_covers_every_bucket(cfg):
    """The eight financials in a real book were core and legacy, so a rule that
    only trimmed satellites produced no action on the largest risk present."""
    values = {"A": 20000.0, "B": 20000.0, "C": 60000.0}
    sectors = {"A": "Financial Services", "B": "Financial Services", "C": "IT"}
    quality = {"A": q(60.0), "B": q(90.0), "C": q(80.0)}
    trims = adv.plan_sector_trims({}, values, sectors, quality, 100000.0, cfg)
    assert trims, "financials at 40% of book must trigger a trim"
    # The weaker of the two financials is trimmed first.
    assert "A" in trims


def test_sector_trim_never_guts_one_holding(cfg):
    values = {"A": 40000.0, "B": 10000.0, "C": 50000.0}
    sectors = {"A": "Financial Services", "B": "Financial Services", "C": "IT"}
    quality = {"A": q(60.0), "B": q(60.0), "C": q(80.0)}
    trims = adv.plan_sector_trims({}, values, sectors, quality, 100000.0, cfg)
    assert all(f <= 0.5 + 1e-9 for f in trims.values()), \
        "a sector fix taking most of one position is an exit in disguise"


def test_sector_within_cap_produces_no_trim(cfg):
    values = {"A": 10000.0, "B": 90000.0}
    sectors = {"A": "Financial Services", "B": "IT"}
    trims = adv.plan_sector_trims({}, values, sectors, {}, 100000.0, cfg)
    assert "A" not in trims


# ------------------------------------------------------------- LTCG exemption

def test_exemption_covers_a_small_plan(cfg):
    sells = [{"symbol": "A", "lt_gain": 50000, "estimated_tax": 6250}]
    out = tx.apply_ltcg_exemption(sells, cfg)
    assert out["fully_covered"] is True
    assert out["tax_after_exemption"] == pytest.approx(0.0)


def test_exemption_runs_out_on_a_large_plan(cfg):
    sells = [{"symbol": "A", "lt_gain": 400000, "estimated_tax": 50000}]
    out = tx.apply_ltcg_exemption(sells, cfg)
    assert out["fully_covered"] is False
    assert out["tax_after_exemption"] > 0
    assert out["saved_by_exemption"] == pytest.approx(125000 * 0.125)


def test_exemption_is_shared_across_the_plan_not_per_holding(cfg):
    """The allowance belongs to the year. Applying it per sale would exempt
    four times as much as the law allows."""
    sells = [{"symbol": s, "lt_gain": 60000, "estimated_tax": 7500}
             for s in "ABCD"]
    out = tx.apply_ltcg_exemption(sells, cfg)
    assert out["exemption_used"] == pytest.approx(125000)
    assert out["long_term_gain"] == pytest.approx(240000)


# ------------------------------------------------------------- decision log

def test_decision_log_records_and_scores(tmp_path):
    import decision_log as dl
    con = dl.connect(tmp_path / "log.db")
    old = dt.date.today() - dt.timedelta(days=40)
    dl.record(con, [
        {"symbol": "UP", "action": "ADD", "price": 100.0, "reasons": []},
        {"symbol": "DOWN", "action": "EXIT", "price": 100.0, "reasons": []},
        {"symbol": "IGNORED", "action": "HOLD", "price": 100.0, "reasons": []},
    ], old)
    scored = dl.score_past_advice(con, {"UP": 120.0, "DOWN": 80.0})
    assert len(scored) == 2, "HOLD is not a decision worth reviewing"
    # Positive always means the advice was right, whichever direction it called.
    assert scored[scored.symbol == "UP"].outcome_pct.iloc[0] > 0
    assert scored[scored.symbol == "DOWN"].outcome_pct.iloc[0] > 0


def test_recent_advice_is_not_scored(tmp_path):
    """Judging a call made three days ago measures noise, not the rule."""
    import decision_log as dl
    con = dl.connect(tmp_path / "log.db")
    dl.record(con, [{"symbol": "A", "action": "ADD", "price": 100.0,
                     "reasons": []}], dt.date.today())
    assert dl.score_past_advice(con, {"A": 120.0}).empty


def test_trim_is_scored_as_sizing_not_a_directional_call(tmp_path):
    """A trim is a sizing decision. Averaging it into a hit rate would report
    a rising market as a failure of judgement."""
    import decision_log as dl
    con = dl.connect(tmp_path / "log.db")
    old = dt.date.today() - dt.timedelta(days=40)
    dl.record(con, [{"symbol": "T", "action": "TRIM", "price": 100.0, "reasons": []},
                    {"symbol": "A", "action": "ADD", "price": 100.0, "reasons": []}], old)
    scored = dl.score_past_advice(con, {"T": 120.0, "A": 120.0})
    kinds = dict(zip(scored.symbol, scored.kind))
    assert kinds["T"] == "sizing" and kinds["A"] == "directional"
    assert dl.summary(scored)["_overall"]["n"] == 1


# ------------------------------------------------------------- freshness

def test_freshness_flags_stale_data(tmp_path):
    import pandas as pd
    from data import bhavcopy as bc
    from data import freshness as fr

    con = bc.connect(tmp_path / "m.db")
    old = dt.date(2026, 1, 5)
    bc.save_day(con, old, pd.DataFrame(
        [("A", 100.0, 1e6, 1e9)],
        columns=["symbol", "close", "volume", "turnover"]))
    f = fr.assess(con, today=dt.date(2026, 3, 1))
    assert f.is_stale
    assert "refresh before making any decision" in f.message


def test_freshness_accepts_yesterdays_close(tmp_path):
    """Monday reading Friday's close is current; staleness counts sessions."""
    import pandas as pd
    from data import bhavcopy as bc
    from data import freshness as fr

    con = bc.connect(tmp_path / "m.db")
    bc.save_day(con, dt.date(2026, 9, 3), pd.DataFrame(
        [("A", 100.0, 1e6, 1e9)],
        columns=["symbol", "close", "volume", "turnover"]))
    assert fr.assess(con, today=dt.date(2026, 9, 4)).label == "fresh"


# ------------------------------------------------------------- universe floor

def test_universe_floor_excludes_penny_stocks(tmp_path):
    """Risk-adjusted momentum ranks a pumped penny stock above real businesses."""
    import pandas as pd
    from data import bhavcopy as bc

    con = bc.connect(tmp_path / "m.db")
    for i in range(60):
        d = dt.date(2026, 1, 1) + dt.timedelta(days=i)
        if d.weekday() >= 5:
            continue
        bc.save_day(con, d, pd.DataFrame(
            [("REAL", 500.0, 1e6, 5e9, "INE001A01001"),
             ("PENNY", 7.0, 1e8, 7e8, "INE002A01001")],
            columns=["symbol", "close", "volume", "turnover", "isin"]))
    assert "PENNY" in bc.universe_on(con, dt.date(2026, 2, 20), 10, min_days=10)
    kept = bc.universe_on(con, dt.date(2026, 2, 20), 10, min_days=10,
                          min_price=20.0)
    assert kept == ["REAL"]


# ------------------------------------------------------------- vol targeting

def test_volatility_targeting_scales_exposure(cfg):
    from strategy import momentum as mom
    c = dict(cfg)
    c["momentum_strategy"] = {**cfg["momentum_strategy"], "vol_target": True}
    calm, _ = mom.exposure_for_vol(0.10, c)
    wild, _ = mom.exposure_for_vol(0.45, c)
    assert calm == 1.0, "never above fully invested -- this does not borrow"
    assert wild < 0.5


def test_volatility_targeting_floors_exposure(cfg):
    """A vol spike is not a reason to hold only cash -- the rebound usually
    follows it."""
    from strategy import momentum as mom
    c = dict(cfg)
    c["momentum_strategy"] = {**cfg["momentum_strategy"], "vol_target": True}
    e, note = mom.exposure_for_vol(2.0, c)
    assert e == pytest.approx(cfg["momentum_strategy"]["vol_min_exposure"])
    assert "floored" in note


def test_volatility_targeting_is_off_by_default(cfg):
    """It failed its out-of-sample test: -7pp of return for -6pp of drawdown."""
    from strategy import momentum as mom
    assert cfg["momentum_strategy"]["vol_target"] is False
    assert mom.exposure_for_vol(0.45, cfg)[0] == 1.0


# ------------------------------------------------------------------ watching

def test_watching_inherits_baseline_from_the_decision_log(tmp_path, monkeypatch):
    """A stock added a fortnight after it was flagged must still be measured
    from the call. Resetting the baseline to the day you noticed flatters every
    late addition."""
    import decision_log as dl
    import watching as wg

    logdb = tmp_path / "log.db"
    con = dl.connect(logdb)
    flagged = dt.date.today() - dt.timedelta(days=14)
    dl.record(con, [{"symbol": "CUPID", "action": "ADD", "price": 283.15,
                     "reasons": []}], flagged)
    monkeypatch.setattr(dl, "connect", lambda *a, **k: dl.sqlite3.connect(logdb))

    path = tmp_path / "watching.csv"
    ok, msg = wg.add("CUPID", source="rank", path=path)
    assert ok and "decision log" in msg
    row = wg.load(path)[0]
    assert row.baseline_price == pytest.approx(283.15)
    assert row.baseline_date == flagged.isoformat()


def test_watching_falls_back_to_today_when_unlogged(tmp_path, monkeypatch):
    import decision_log as dl
    import watching as wg
    monkeypatch.setattr(wg, "_baseline_from_log", lambda s: None)
    path = tmp_path / "w.csv"
    wg.add("NEWCO", price=500.0, path=path)
    row = wg.load(path)[0]
    assert row.baseline_price == pytest.approx(500.0)
    assert row.baseline_date == dt.date.today().isoformat()


def test_watching_rejects_duplicates(tmp_path, monkeypatch):
    import watching as wg
    monkeypatch.setattr(wg, "_baseline_from_log", lambda s: None)
    path = tmp_path / "w.csv"
    wg.add("A", price=10.0, path=path)
    ok, msg = wg.add("A", price=99.0, path=path)
    assert not ok and "already" in msg
    assert wg.load(path)[0].baseline_price == pytest.approx(10.0), \
        "re-adding must not silently reset the baseline"


def test_watching_report_measures_from_the_baseline(tmp_path, monkeypatch):
    import watching as wg
    monkeypatch.setattr(wg, "_baseline_from_log", lambda s: None)
    path = tmp_path / "w.csv"
    wg.add("UP", price=100.0, path=path)
    wg.add("DOWN", price=100.0, path=path)
    rep = wg.report({"UP": 120.0, "DOWN": 80.0}, path)
    moves = {r["symbol"]: r["move_pct"] for r in rep}
    assert moves["UP"] == pytest.approx(20.0)
    assert moves["DOWN"] == pytest.approx(-20.0)
    assert [r["symbol"] for r in rep] == ["UP", "DOWN"], "best first"


def test_missing_baseline_is_filled_once(tmp_path, monkeypatch):
    import watching as wg
    monkeypatch.setattr(wg, "_baseline_from_log", lambda s: None)
    path = tmp_path / "w.csv"
    wg.add("X", path=path)                       # no price available
    assert wg.load(path)[0].baseline_price == 0
    assert wg.fill_missing_baselines({"X": 250.0}, path) == 1
    assert wg.load(path)[0].baseline_price == pytest.approx(250.0)
    # Second call must not overwrite an established baseline.
    assert wg.fill_missing_baselines({"X": 999.0}, path) == 0


def test_baseline_uses_the_price_session_not_the_run_date(tmp_path, monkeypatch):
    """A weekend run quotes Friday's close. Dating that price to the Sunday
    implies a session that never traded, which is exactly the kind of quiet
    imprecision that becomes confusing months later."""
    import decision_log as dl
    import watching as wg

    logdb = tmp_path / "log.db"
    con = dl.connect(logdb)
    sunday = dt.date(2026, 9, 6)
    friday = dt.date(2026, 9, 4)
    dl.record(con, [{"symbol": "CUPID", "action": "ADD", "price": 283.15,
                     "reasons": []}], run_date=sunday, price_date=friday)
    monkeypatch.setattr(dl, "connect", lambda *a, **k: dl.sqlite3.connect(logdb))

    path = tmp_path / "w.csv"
    wg.add("CUPID", source="rank", path=path)
    row = wg.load(path)[0]
    assert row.baseline_date == friday.isoformat()
    assert row.baseline_price == pytest.approx(283.15)


def test_price_date_falls_back_to_run_date_for_old_rows(tmp_path, monkeypatch):
    """Rows written before the column existed have no price date; they must
    still resolve rather than returning null."""
    import decision_log as dl
    import watching as wg

    logdb = tmp_path / "log.db"
    con = dl.connect(logdb)
    con.execute("INSERT INTO decision_log (run_date, symbol, action, price)"
                " VALUES (?,?,?,?)", ("2026-01-05", "OLD", "ADD", 100.0))
    con.commit()
    monkeypatch.setattr(dl, "connect", lambda *a, **k: dl.sqlite3.connect(logdb))
    path = tmp_path / "w.csv"
    wg.add("OLD", path=path)
    assert wg.load(path)[0].baseline_date == "2026-01-05"


def test_decision_log_migrates_an_existing_database(tmp_path):
    """price_date was added after the first release; CREATE TABLE IF NOT EXISTS
    would leave an older database without it."""
    import sqlite3
    import decision_log as dl

    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE decision_log (
            run_date TEXT NOT NULL, symbol TEXT NOT NULL, action TEXT NOT NULL,
            bucket TEXT, urgency TEXT, price REAL NOT NULL, qty REAL,
            pct_of_book REAL, pnl_pct REAL, quality REAL, rank INTEGER,
            timing_score REAL, stop REAL, reason TEXT, acted TEXT DEFAULT '',
            PRIMARY KEY (run_date, symbol, action));
    """)
    con.commit(); con.close()

    con = dl.connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(decision_log)")}
    assert "price_date" in cols


# ------------------------------------------------------------------ horizon

def test_horizon_comes_from_measured_percentiles(cfg):
    """A volatility formula was tested and correlated -0.03 with actual time
    to target, so the horizon is observed history, not a model."""
    from strategy import horizon as hz
    h = hz.for_target("t1", cfg, dt.date(2026, 9, 4))
    assert h.median_sessions == 17
    assert h.typical_sessions > h.median_sessions
    assert h.slow_sessions > h.typical_sessions
    assert 0 < h.never_pct < 50
    # Dates skip weekends.
    assert h.median_date.weekday() < 5


def test_horizon_status_bands(cfg):
    from strategy import horizon as hz
    h = hz.for_target("t1", cfg, dt.date(2026, 9, 4))
    assert h.status(5, False)[0] == "early"
    assert h.status(20, False)[0] == "due"
    assert h.status(60, False)[0] == "late"
    assert h.status(200, False)[0] == "stalled"
    assert h.status(10, True)[0] == "fast"
    assert h.status(70, True)[0] == "slow"


def test_sessions_between_skips_weekends():
    from strategy import horizon as hz
    # Friday to the following Monday is one session, not three days.
    assert hz.sessions_between(dt.date(2026, 9, 4), dt.date(2026, 9, 7)) == 1


def test_target_hit_is_recorded_once_and_never_revised(tmp_path, monkeypatch):
    """The date a target was first met is a fact about the past."""
    import watching as wg
    monkeypatch.setattr(wg, "_baseline_from_log", lambda s: None)
    path = tmp_path / "w.csv"
    wg.add("X", price=100.0, atr_hint=3.0, path=path)
    row = wg.load(path)[0]
    assert row.t1 > 100.0

    first = dt.date(2026, 9, 10)
    hits = wg.mark_target_hits({"X": row.t1 + 1}, first, path)
    assert hits and "T1" in hits[0]
    assert wg.load(path)[0].t1_hit_on == first.isoformat()

    # A later, higher price must not move the recorded date.
    wg.mark_target_hits({"X": row.t1 + 50}, dt.date(2026, 10, 1), path)
    assert wg.load(path)[0].t1_hit_on == first.isoformat()


def test_target_not_marked_below_the_level(tmp_path, monkeypatch):
    import watching as wg
    monkeypatch.setattr(wg, "_baseline_from_log", lambda s: None)
    path = tmp_path / "w.csv"
    wg.add("X", price=100.0, atr_hint=3.0, path=path)
    t1 = wg.load(path)[0].t1
    assert wg.mark_target_hits({"X": t1 - 0.01}, dt.date(2026, 9, 10), path) == []
    assert wg.load(path)[0].t1_hit_on == ""


def _book(values, scores):
    """advise_book inputs at a flat price of 100, book of 100,000."""
    inputs = {s: dict(symbol=s, bucket="legacy",
                      holding={"quantity": v / 100, "average_price": 100.0,
                               "lt_quantity": v / 100, "st_quantity": 0.0},
                      price=100.0, value=v, book_value=100000.0,
                      quality=fu.Quality(s, scores[s], {}, []), rank=None,
                      rank_universe=200, risk={}, cfg=None)
              for s, v in values.items()}
    return inputs, {s: fu.Quality(s, scores[s], {}, []) for s in values}


def _sold(advice):
    return {s: a.qty * 100 for s, a in advice.items() if a.is_sell}


def test_sector_trim_counts_the_sizing_trims_already_planned(cfg):
    """Trimming the oversized bank already brings the sector under the cap.
    Planning the sector on pre-trim values used to trim the small names too."""
    # BIG is 12% (cap 5%); the others sit under the 6% trim trigger.
    values = {"BIG": 12000.0, "S1": 6000.0, "S2": 6000.0, "S3": 5000.0}
    inputs, quality = _book(values, {"BIG": 90, "S1": 60, "S2": 70, "S3": 80})
    for kw in inputs.values():
        kw["cfg"] = cfg
    advice = adv.advise_book(inputs, values, dict.fromkeys(values, "Fin"),
                             quality, 100000.0, cfg)
    assert set(_sold(advice)) == {"BIG"}
    assert _sold(advice)["BIG"] == pytest.approx(7000.0)


def test_oversized_stock_also_carries_its_sector_trim(cfg):
    """When the sector plan picks a stock that is also oversized, its extra
    must be added to the sizing trim -- returning early used to drop it and
    leave the sector over cap."""
    # 34% financials; BIG's sizing trim alone leaves 29%, still over 25%.
    values = {"BIG": 10000.0, "S1": 6000.0, "S2": 6000.0, "S3": 6000.0,
              "S4": 6000.0}
    inputs, quality = _book(values, {"BIG": 55, "S1": 60, "S2": 70, "S3": 80,
                                     "S4": 85})
    for kw in inputs.values():
        kw["cfg"] = cfg
    advice = adv.advise_book(inputs, values, dict.fromkeys(values, "Fin"),
                             quality, 100000.0, cfg)
    sold = _sold(advice)
    assert sold["BIG"] > 5000.0                      # more than sizing alone
    assert any("sector is also over" in r for r in advice["BIG"].reasons)
    left = sum(values.values()) - sum(sold.values())
    assert 100 * left / 100000.0 <= cfg["risk"]["max_sector_pct"] + 1e-6
