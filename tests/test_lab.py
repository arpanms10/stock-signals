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
