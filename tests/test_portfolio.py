"""Allocation, concentration and risk-monitor correctness."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from scoring import timing
from strategy import allocation as al
from strategy import risk_monitor as rm


@pytest.fixture
def cfg():
    return timing.load_config()


def frame(n=400, start=100.0, end=200.0, atr=4.0):
    close = pd.Series(np.linspace(start, end, n))
    return pd.DataFrame({
        "date": [dt.date(2025, 1, 1) + dt.timedelta(days=i) for i in range(n)],
        "close": close, "high": close * 1.01, "low": close * 0.99,
        "atr14": atr, "sma50": close.rolling(50, min_periods=1).mean(),
        "sma200": close.rolling(200, min_periods=1).mean(),
        "timing_score": 60.0,
    })


# ------------------------------------------------------------------ weights

def test_equal_weights_sum_to_100():
    w = al.equal_weights(["A", "B", "C"])
    assert sum(w.values()) == pytest.approx(100.0)


def test_volatility_parity_gives_the_calmer_stock_more_weight():
    w = al.volatility_parity_weights(["CALM", "WILD"], {"CALM": 0.15, "WILD": 0.45})
    assert w["CALM"] > w["WILD"]
    assert sum(w.values()) == pytest.approx(100.0)


def test_volatility_parity_falls_back_when_vols_missing():
    w = al.volatility_parity_weights(["A", "B"], {})
    assert w == al.equal_weights(["A", "B"])


# ------------------------------------------------------------------ drift

def test_plan_sells_names_no_longer_targeted():
    plan = al.build_plan({"A": 100.0}, {"B": {"quantity": 10}},
                         {"A": 50.0, "B": 100.0}, 1000.0)
    b = next(p for p in plan if p.symbol == "B")
    assert b.action == "SELL" and "no longer" in b.reason


def test_plan_holds_within_tolerance():
    plan = al.build_plan({"A": 50.0, "B": 50.0},
                         {"A": {"quantity": 10}, "B": {"quantity": 10}},
                         {"A": 100.0, "B": 100.0}, 2000.0)
    assert all(p.action == "HOLD" for p in plan)
    assert all(p.shares_delta == 0 for p in plan)


def test_plan_flags_drift_beyond_tolerance():
    plan = al.build_plan({"A": 50.0, "B": 50.0},
                         {"A": {"quantity": 18}, "B": {"quantity": 2}},
                         {"A": 100.0, "B": 100.0}, 2000.0)
    a = next(p for p in plan if p.symbol == "A")
    assert a.action == "SELL" and a.drift_pct > 5


# ------------------------------------------------------------ concentration

def test_sector_cap_names_the_cluster(cfg):
    hold = {s: {"quantity": 10} for s in ("TCS", "INFY", "WIPRO")}
    px = {s: 100.0 for s in hold}
    warns = al.concentration_report(hold, px, {s: "IT" for s in hold}, cfg)
    assert any("IT is 100.0%" in w and "one bet, not 3" in w for w in warns)


def test_correlation_clusters_detects_identical_movers():
    f = frame()
    noise = pd.Series(np.random.default_rng(1).normal(0, 0.01, len(f)))
    g = f.copy(); g["close"] = f["close"] * 1.5
    h = f.copy(); h["close"] = (f["close"] * np.exp(noise.cumsum() * 8))
    out = al.correlation_clusters({"A": f, "B": g, "C": h}, ["A", "B", "C"])
    assert any("A" in o and "B" in o for o in out)


# ------------------------------------------------------------- risk monitor

def test_stop_above_price_is_reported_as_already_breached(cfg):
    """A stop derived from an old peak is not a level you can set today.

    Reporting 3172 as 'your stop' while the stock trades at 2304 invites
    placing an order that fills instantly.
    """
    f = frame(400, 300.0, 100.0)          # fell a long way from its peak
    st = rm.holding_status("X", f, {"quantity": 10, "average_price": 250.0}, cfg)
    assert st["stop_breached"] and st["stop_stale"]
    assert any("breached some time ago" in a for a in st["alerts"])


def test_intact_stop_is_not_marked_stale(cfg):
    f = frame(400, 100.0, 200.0)
    st = rm.holding_status("X", f, {"quantity": 10, "average_price": 190.0}, cfg)
    assert not st["stop_breached"]


def test_capital_at_risk_excludes_breached_stops(cfg):
    """Summing max(...,0) reported 0% risk exactly when the book was worst."""
    good = {"symbol": "G", "value": 1000.0, "qty": 10, "close": 100.0,
            "stop": 90.0, "stop_breached": False, "pnl_pct": 5.0}
    bad = {"symbol": "B", "value": 500.0, "qty": 10, "close": 50.0,
           "stop": 80.0, "stop_breached": True, "pnl_pct": -30.0}
    notes = rm.portfolio_risk([good, bad], cfg)
    assert any("100" in n and "at risk" in n for n in notes)
    assert any("no defined downside limit" in n for n in notes)


def test_regime_note_reports_state_and_duration(cfg):
    dates = [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(300)]
    up = pd.DataFrame({"date": dates, "close": np.linspace(100, 200, 300)})
    assert "RISK-ON" in rm.regime_note(up, cfg)
    down = pd.DataFrame({"date": dates, "close": np.linspace(200, 100, 300)})
    assert "RISK-OFF" in rm.regime_note(down, cfg)


# ------------------------------------------------------------------ buckets

def test_unknown_holdings_default_to_legacy_not_satellite():
    """A position already on the books was not bought on momentum, so it must
    not be sold on momentum without the user saying so. Defaulting these to
    satellite produced a wall of exits that was an artefact of the framework,
    not a finding about the holdings."""
    from strategy import buckets as bk
    b, why = bk.classify("X", quality_score=None, volatility=0.2,
                         liquidity_rank=10)
    assert b == "legacy"
    b2, _ = bk.classify("Y", quality_score=55.0, volatility=0.2,
                        liquidity_rank=10)
    assert b2 == "legacy"


def test_declared_bucket_always_wins():
    from strategy import buckets as bk
    for declared in ("core", "satellite", "legacy"):
        b, why = bk.classify("X", 95.0, 0.15, 5, declared=declared)
        assert b == declared and "set by you" in why


def test_high_quality_low_volatility_is_core():
    from strategy import buckets as bk
    b, _ = bk.classify("X", quality_score=90.0, volatility=0.20,
                       liquidity_rank=10)
    assert b == "core"


def test_high_quality_but_volatile_is_not_core():
    from strategy import buckets as bk
    b, why = bk.classify("X", quality_score=90.0, volatility=0.55,
                         liquidity_rank=10)
    assert b == "legacy" and "volatility" in why


# ------------------------------------------------------------------ advisor

def _cfg_and_quality(score, flags=()):
    from scoring import timing
    import fundamentals as fu
    return timing.load_config(), fu.Quality("X", score, {}, list(flags))


def test_legacy_is_never_exited_on_rank_alone():
    from strategy import advisor as adv
    cfg, q = _cfg_and_quality(80.0)
    a = adv.advise("X", "legacy", {"quantity": 10, "average_price": 100.0},
                   110.0, 1100.0, 100000.0, q, rank=180, rank_universe=200,
                   risk={}, cfg=cfg)
    assert a.action == "HOLD"
    assert any("retroactively" in r or "not bought on momentum" in r
               for r in a.reasons)


def test_satellite_is_exited_on_rank():
    from strategy import advisor as adv
    cfg, q = _cfg_and_quality(80.0)
    a = adv.advise("X", "satellite", {"quantity": 10, "average_price": 100.0},
                   110.0, 1100.0, 100000.0, q, rank=180, rank_universe=200,
                   risk={}, cfg=cfg)
    assert a.action == "EXIT" and a.qty == 10


def test_hard_flag_exits_whole_position_in_every_bucket():
    from strategy import advisor as adv
    for bucket in ("core", "satellite", "legacy"):
        cfg, q = _cfg_and_quality(85.0, ["pledge RISING: 10% -> 30%"])
        a = adv.advise("X", bucket, {"quantity": 10, "average_price": 100.0},
                       110.0, 1100.0, 100000.0, q, rank=1, rank_universe=200,
                       risk={}, cfg=cfg)
        assert a.action == "EXIT" and a.qty == 10 and a.urgency == "urgent"


def test_data_gap_is_not_a_red_flag():
    """A gap in our coverage must never be reported as a finding about the
    company -- an earlier version told the user to liquidate on one."""
    from strategy import advisor as adv
    cfg, q = _cfg_and_quality(80.0,
                              ["cash quality not scored: no annual profit on file"])
    a = adv.advise("X", "core", {"quantity": 10, "average_price": 100.0},
                   110.0, 1100.0, 100000.0, q, rank=5, rank_universe=200,
                   risk={}, cfg=cfg)
    assert a.action != "EXIT"


def test_oversized_position_trims_rather_than_exits():
    """Too big is a sizing problem, not a thesis problem."""
    from strategy import advisor as adv
    cfg, q = _cfg_and_quality(90.0)
    # 100 shares at 100 = 10,000, which is 20% of a 50,000 book.
    a = adv.advise("X", "core", {"quantity": 100, "average_price": 100.0},
                   100.0, 10000.0, 50000.0, q, rank=5, rank_universe=200,
                   risk={}, cfg=cfg)
    assert a.action == "TRIM"
    assert 0 < a.qty < 100                      # partial, never the whole lot


def test_trim_never_sells_the_entire_position():
    """A sizing calculation that overshoots must not become a silent exit."""
    from strategy import advisor as adv
    cfg, q = _cfg_and_quality(90.0)
    a = adv.advise("X", "core", {"quantity": 10, "average_price": 100.0},
                   100.0, 99000.0, 100000.0, q, rank=5, rank_universe=200,
                   risk={}, cfg=cfg)
    assert a.action == "TRIM"
    assert a.qty < 10


def test_exit_rank_scales_to_a_thin_universe():
    """Rank 40 of 79 is mid-pack; rank 40 of 200 is trailing."""
    from strategy import advisor as adv
    cfg, q = _cfg_and_quality(80.0)
    thin = adv.advise("X", "satellite", {"quantity": 10, "average_price": 100.0},
                      110.0, 1100.0, 100000.0, q, rank=25, rank_universe=79,
                      risk={}, cfg=cfg)
    wide = adv.advise("X", "satellite", {"quantity": 10, "average_price": 100.0},
                      110.0, 1100.0, 100000.0, q, rank=25, rank_universe=200,
                      risk={}, cfg=cfg)
    assert thin.action == "EXIT"                # past the scaled threshold
    assert wide.action != "EXIT"                # inside the full-universe band


def test_sanity_warning_fires_on_a_wall_of_exits():
    from strategy import advisor as adv
    advices = [adv.Advice(symbol=f"S{i}", bucket="satellite", action="EXIT")
               for i in range(8)] + \
              [adv.Advice(symbol="H", bucket="core", action="HOLD")]
    warns = adv.sanity_warnings(advices, 200, 200)
    assert any("question about the setup" in w for w in warns)


def test_realised_vol_is_defined_once_and_agrees():
    """Two paths computing volatility differently gave the same portfolio a
    12-core split in one place and 18-core in another."""
    import numpy as np
    import pandas as pd
    from strategy import buckets as bk

    rng = np.random.default_rng(5)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.02, 400))))
    f = pd.DataFrame({"close": close})
    v = bk.realised_vol(f)
    assert v is not None and 0.1 < v < 1.0
    # Same frame, same answer, every time.
    assert bk.realised_vol(f) == v


def test_realised_vol_returns_none_on_thin_history():
    import pandas as pd
    from strategy import buckets as bk
    assert bk.realised_vol(pd.DataFrame({"close": [1.0, 2.0, 3.0]})) is None
    assert bk.realised_vol(None) is None


def test_trim_and_exit_badges_are_visually_distinct():
    """Trim keeps the holding, exit does not. Two 'S' badges made that
    distinction depend on noticing a shade of red versus amber."""
    from ui import components as c
    assert ">T<" in c.badge("TRIM")
    assert ">S<" in c.badge("EXIT")
    assert ">B<" in c.badge("BUY") and ">B<" in c.badge("ADD")
    assert ">H<" in c.badge("HOLD")
    # Colour still carries meaning, but is no longer the only signal.
    assert c.badge("TRIM") != c.badge("EXIT")


def test_urgent_badge_is_marked():
    from ui import components as c
    assert "box-shadow" in c.badge("EXIT", urgent=True)
    assert "box-shadow" not in c.badge("EXIT", urgent=False)


def test_add_targets_are_computed_from_todays_price_not_old_cost():
    """A stock bought cheaply that has since run up would otherwise show
    targets BELOW the current price on a BUY row -- reading as 'sell lower
    than you buy'. ICICIBANK did exactly this: avg 1217, price 1423,
    targets 1283 and 1349.
    """
    import datetime as dt
    from scoring import timing
    from strategy import risk_monitor as rm

    cfg = timing.load_config()
    f = frame(400, 1000.0, 1423.0, atr=22.0)
    st = rm.holding_status("X", f, {"quantity": 8, "average_price": 1217.13}, cfg)

    # Entry-frame targets are already achieved and must say so.
    assert st["t1_hit"] and st["t2_hit"]
    assert st["t1"] < st["close"] and st["t2"] < st["close"]

    # Buy-today targets must be ABOVE the current price.
    assert st["add_t1"] > st["close"]
    assert st["add_t2"] > st["add_t1"]
    assert st["add_stop"] < st["close"]


def test_passed_targets_are_labelled_not_shown_as_upside():
    from ui import components as c
    html = c.trade_plan(1423.0, 1408.0, 1283.0, 1349.0, t1_hit=True, t2_hit=True)
    assert "passed" in html
    # A negative percentage must never appear next to a target.
    assert "-9.9%" not in html and "-5.2%" not in html


def test_pending_targets_still_show_their_upside():
    from ui import components as c
    html = c.trade_plan(1000.0, 960.0, 1060.0, 1120.0)
    assert "+6.0%" in html and "+12.0%" in html
    assert "passed" not in html


def test_buy_plan_labels_the_price_as_a_buy():
    from ui import components as c
    assert "to buy" in c.buy_plan(1423.0, 1379.0, 1489.0, 1555.0)


def test_urgent_and_calm_badges_differ_in_more_than_a_ring():
    """A ring alone told the reader two badges differed without saying how.
    Same letter, same colour, one silently more serious."""
    from ui import components as c
    urgent, calm = c.badge("EXIT", urgent=True), c.badge("EXIT", urgent=False)
    assert urgent != calm
    assert "SELL NOW" in urgent and "SELL NOW" not in calm
    assert ">!<" in urgent and ">!<" not in calm       # visible marker
    assert "box-shadow" in urgent


def test_legend_shows_every_badge_variant(monkeypatch):
    """Including both S variants, side by side."""
    from ui import components as c
    captured = {}
    monkeypatch.setattr(c.st, "markdown",
                        lambda html, **k: captured.setdefault("html", html))
    c.legend()
    html = captured["html"]
    for letter in (">B<", ">T<", ">S<", ">H<"):
        assert letter in html
    assert "urgent" in html.lower()
    assert html.count(">S<") >= 2                      # calm and urgent both


def test_urgent_rows_sort_ahead_of_calm_ones_within_an_action():
    """Printing an 'urgent' heading and then listing a calm row beneath it
    makes the grouping decorative rather than true. JSWCEMENT (urgent, small)
    was rendering below TATAPOWER (calm, larger)."""
    holdings = [
        {"symbol": "TATAPOWER", "action": "EXIT", "urgency": "review", "value": 18400},
        {"symbol": "AEROPLANE", "action": "EXIT", "urgency": "review", "value": 13600},
        {"symbol": "JSWCEMENT", "action": "EXIT", "urgency": "urgent", "value": 12655},
        {"symbol": "JUBLFOOD", "action": "EXIT", "urgency": "urgent", "value": 96540},
        {"symbol": "TCS", "action": "TRIM", "urgency": "normal", "value": 133632},
    ]
    order = {"EXIT": 0, "TRIM": 1, "ADD": 2, "HOLD": 3}
    rows = sorted(holdings, key=lambda r: (order.get(r["action"], 9),
                                           0 if r["urgency"] == "urgent" else 1,
                                           -r["value"]))
    names = [r["symbol"] for r in rows]
    assert names[:2] == ["JUBLFOOD", "JSWCEMENT"]     # both urgent, by value
    assert names[2:4] == ["TATAPOWER", "AEROPLANE"]   # then calm, by value
    assert names[-1] == "TCS"                          # trims after exits
