"""Strategy lab: registry, pluggable rankings, harness, ledger, tax, forward."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from backtest import pit_engine as pit
from data import bhavcopy as bc
from lab import registry
from lab import runner
from lab.strategy import Strategy

START = dt.date(2024, 1, 1)
# Daily drift per symbol: AAA strongest, EEE weakest.
DRIFT = {"AAA": 0.003, "BBB": 0.002, "CCC": 0.001, "DDD": 0.0005, "EEE": -0.0005}


def _days(n):
    d = START
    while n:
        if d.weekday() < 5:
            yield d
            n -= 1
        d += dt.timedelta(days=1)


@pytest.fixture
def market(tmp_path):
    con = bc.connect(tmp_path / "m.db")
    px = {s: 100.0 for s in DRIFT}
    for i, d in enumerate(_days(420)):
        for k, (s, g) in enumerate(DRIFT.items()):
            px[s] *= 1 + g + 0.004 * np.sin(i * (k + 1) / 7)
        bc.save_day(con, d, pd.DataFrame(
            [(s, px[s], 1e6, 1e9, f"INE{k:03d}A01011") for k, s in enumerate(DRIFT)],
            columns=["symbol", "close", "volume", "turnover", "isin"]))
    return con


BASE = {"signals": {"regime_sma": 200},
        "momentum_strategy": {"n_hold": 2, "enter_rank": 2, "exit_rank": 2,
                              "lookback_months": 3, "skip_months": 0,
                              "vol_window": 20, "rebalance_days": 21,
                              "require_above_200dma": False}}
BENCH = pd.DataFrame(columns=["date", "close"])
WINDOW = (dt.date(2024, 9, 2), dt.date(2025, 7, 31))


def _held(res):
    return {s for _, h in res.holdings_log for s in h}


# --- phase 1: registry and pluggable rankings -----------------------------

def test_registry_has_the_tested_strategies_under_permanent_names():
    names = {s.name for s in registry.all_strategies()}
    assert {"momentum_9_1", "momentum_12_1", "high52", "lowvol"} <= names
    assert registry.get("momentum_9_1").family == "momentum"
    with pytest.raises(KeyError):
        registry.get("no_such_strategy")


def test_a_name_cannot_be_registered_twice():
    s = registry.get("lowvol")
    clone = Strategy(s.name, s.family, s.summary, s.score)
    with pytest.raises(ValueError):
        registry.register(clone)


def test_settings_override_without_touching_the_base():
    s = Strategy("t", "t", "", pit.score_ram, {"n_hold": 7})
    cfg = s.config(BASE)
    assert cfg["momentum_strategy"]["n_hold"] == 7
    assert BASE["momentum_strategy"]["n_hold"] == 2


def test_a_custom_score_decides_what_is_held(market):
    best = Strategy("t_best", "t", "", pit.score_ram)
    # Rank by the negative of momentum: the weakest names are bought.
    worst = Strategy("t_worst", "t", "", lambda x: -pit.score_ram(x))
    held_best = _held(runner.run(best, market, BASE, *WINDOW, BENCH, universe_size=5))
    held_worst = _held(runner.run(worst, market, BASE, *WINDOW, BENCH, universe_size=5))
    assert "AAA" in held_best and "EEE" not in held_best
    assert "EEE" in held_worst and "AAA" not in held_worst


def test_a_one_part_blend_ranks_like_the_plain_score(market):
    plain = Strategy("t_plain", "t", "", pit.score_ram)
    blend = Strategy("t_blend", "t", "", lambda x: [(pit.score_ram(x), 1.0),
                                                     (pit.score_ram(x), 1.0)])
    a = runner.run(plain, market, BASE, *WINDOW, BENCH, universe_size=5)
    b = runner.run(blend, market, BASE, *WINDOW, BENCH, universe_size=5)
    assert [h for _, h in a.holdings_log] == [h for _, h in b.holdings_log]


def test_the_settings_score_still_selects_a_builtin(market):
    via_cfg = pit.run(market, {**BASE, "momentum_strategy": {**BASE["momentum_strategy"],
                                                             "score": "lowvol"}},
                      *WINDOW, BENCH, universe_size=5)
    via_fn = runner.run(Strategy("t_lv", "t", "", pit.score_lowvol),
                        market, BASE, *WINDOW, BENCH, universe_size=5)
    assert via_cfg.equity.equals(via_fn.equity)
    assert via_cfg.holdings_log == via_fn.holdings_log


# --- phase 2: pass rules and the harness ----------------------------------

from lab import harness, results  # noqa: E402
from lab.rules import C, PassRule  # noqa: E402

RULE = PassRule("2026-10-29", train=(C("cagr_pct", ">=", 5.0), C("sharpe", ">", 0.0)),
                holdout=(C("cagr_pct", ">", 0.0),))


def test_a_rule_passes_only_when_every_check_does():
    assert RULE.judge("train", {"cagr_pct": 6.0, "sharpe": 0.4})["passed"]
    v = RULE.judge("train", {"cagr_pct": 6.0, "sharpe": -0.1})
    assert not v["passed"] and v["checks"] == {"cagr_pct >= 5": True, "sharpe > 0": False}


def test_a_missing_or_nan_metric_fails_its_check():
    assert not RULE.judge("holdout", {})["passed"]
    assert not RULE.judge("holdout", {"cagr_pct": float("nan")})["passed"]


def test_the_fingerprint_changes_with_any_part_of_the_rule():
    same = PassRule("2026-10-29", train=(C("cagr_pct", ">=", 5.0), C("sharpe", ">", 0.0)),
                    holdout=(C("cagr_pct", ">", 0.0),))
    looser = PassRule("2026-10-29", train=(C("cagr_pct", ">=", 4.0), C("sharpe", ">", 0.0)),
                      holdout=(C("cagr_pct", ">", 0.0),))
    assert RULE.fingerprint() == same.fingerprint() != looser.fingerprint()


def test_every_rule_checks_metrics_the_harness_produces():
    from lab.rules import RULES
    for name, rule in RULES.items():
        registry.get(name)
        for c in (*rule.train, *rule.holdout):
            assert c.metric in harness.METRICS, (name, c.metric)


def test_controls_keep_the_eligible_set_but_lose_the_order(market):
    s = Strategy("t_h", "t", "", pit.score_ram)
    r = harness.evaluate(s, market, BASE, *WINDOW, BENCH, seeds=3, universe_size=5)
    m = r["metrics"]
    assert len(r["controls"]["cagr_pct"]) == 3
    # Holding the two strongest of five drifting names beats random pairs
    # and the equal-weighted five.
    assert m["edge_vs_controls_pct"] > 0
    assert m["excess_vs_universe_pct"] > 0
    assert set(m) >= {k for k in harness.METRICS}


def test_results_keep_month_end_curves(market, tmp_path):
    s = Strategy("t_r", "t", "", pit.score_ram)
    r = harness.evaluate(s, market, BASE, *WINDOW, BENCH, seeds=1, universe_size=5)
    path = tmp_path / "lab_results.json"
    results.save("t_r", "train", r, path)
    saved = results.load(path)["t_r"]["train"]
    assert "_curves" not in saved
    assert 8 <= len(saved["curves"]["strategy"]) <= 12


# --- phase 3: the holdout ledger -------------------------------------------

from lab import ledger  # noqa: E402

FAM = Strategy("t_a", "fam", "", pit.score_ram)
SIB = Strategy("t_b", "fam", "", pit.score_ram)


def _result(passed, cagr=10.0):
    return {"start": "2018-07-01", "end": "2022-12-31", "metrics": {"cagr_pct": cagr},
            "verdict": {"passed": passed}}


def _fresh():
    return {"families": {}, "strategies": {}}


def test_holdout_needs_a_passed_train_under_the_same_rule():
    d = _fresh()
    with pytest.raises(ledger.Refused, match="no pass rule"):
        ledger.check(d, FAM, "train", None)
    ledger.check(d, FAM, "train", "r1")
    with pytest.raises(ledger.Refused, match="train .* first"):
        ledger.check(d, FAM, "holdout", "r1")
    ledger.record(d, FAM, "train", "r1", _result(False))
    with pytest.raises(ledger.Refused, match="failed train"):
        ledger.check(d, FAM, "holdout", "r1")
    ledger.record(d, FAM, "train", "r1", _result(True))
    ledger.check(d, FAM, "holdout", "r1")


def test_a_rule_edited_after_train_locks_the_holdout():
    d = _fresh()
    ledger.record(d, FAM, "train", "r1", _result(False))
    ledger.record(d, FAM, "train", "r2", _result(True))
    with pytest.raises(ledger.Refused, match="changed after"):
        ledger.check(d, FAM, "holdout", "r2")


def test_a_holdout_runs_once_and_spends_the_whole_family():
    d = _fresh()
    for s in (FAM, SIB):
        ledger.record(d, s, "train", "r", _result(True))
    ledger.check(d, FAM, "holdout", "r")
    ledger.record(d, FAM, "holdout", "r", _result(True))
    with pytest.raises(ledger.Refused, match="spent"):
        ledger.check(d, FAM, "holdout", "r")
    with pytest.raises(ledger.Refused, match="Forward-track t_b"):
        ledger.check(d, SIB, "holdout", "r")
    assert ledger.status(d, SIB) == "train passed, family holdout spent: forward only"
    # Train itself stays open: re-measuring on new data is allowed.
    ledger.check(d, SIB, "train", "r")


def test_trials_count_strategies_not_reruns_and_robustness_is_noted():
    d = _fresh()
    ledger.record(d, FAM, "train", "r", _result(False))
    ledger.record(d, FAM, "train", "r", _result(False))
    ledger.record(d, SIB, "train", "r", _result(False))
    assert d["families"]["fam"]["trials"] == 2
    ledger.record(d, FAM, "robustness", None,
                  {"universe": "liquidity ranks 201-500", "start": "a", "end": "b"})
    assert d["families"]["fam"]["seen"] == ["liquidity ranks 201-500, a .. b"]


def test_the_committed_ledger_covers_every_registered_family(tmp_path):
    data = ledger.load()
    for s in registry.all_strategies():
        assert s.family in data["families"], s.family
    assert data["families"]["momentum"]["holdout"]
    ledger.save(data, tmp_path / "l.json")
    assert ledger.load(tmp_path / "l.json") == data
