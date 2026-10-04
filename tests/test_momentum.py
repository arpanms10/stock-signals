"""Momentum ranking correctness."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from scoring import timing
from strategy import momentum as mom


@pytest.fixture
def cfg():
    return timing.load_config()


def ramp(n=400, start=100.0, end=200.0):
    return pd.Series(np.linspace(start, end, n))


def test_momentum_skips_the_most_recent_month():
    """12-1 must ignore the last 21 bars.

    A stock that rose steadily then crashed this month should still show
    positive 12-1 momentum -- that is the entire point of the skip.
    """
    close = pd.concat([ramp(300, 100, 200),
                       pd.Series(np.linspace(200, 120, 21))], ignore_index=True)
    m = mom.momentum_score(close, 12, 1)
    assert m.iloc[-1] > 0
    # Without the skip the crash dominates and the sign flips.
    assert mom.momentum_score(close, 12, 0).iloc[-1] < m.iloc[-1]


def test_momentum_ranks_stronger_stock_higher():
    weak, strong = ramp(400, 100, 120), ramp(400, 100, 260)
    assert (mom.momentum_score(strong, 12, 1).iloc[-1]
            > mom.momentum_score(weak, 12, 1).iloc[-1])


def test_risk_adjustment_prefers_the_calmer_path():
    """Same destination, different journey -- the calm one must rank higher."""
    n = 400
    calm = ramp(n, 100, 200)
    rng = np.random.default_rng(3)
    noise = pd.Series(rng.normal(0, 0.04, n)).cumsum()
    wild = calm * np.exp(noise - noise.iloc[-1])      # same final level
    assert wild.iloc[-1] == pytest.approx(calm.iloc[-1], rel=1e-6)
    assert (mom.risk_adjusted_momentum(calm).iloc[-1]
            > mom.risk_adjusted_momentum(wild).iloc[-1])


def test_hysteresis_keeps_a_slipping_holding(cfg):
    ranked = pd.DataFrame({"symbol": [f"S{i}" for i in range(40)],
                           "rank": list(range(1, 41))})
    c = dict(cfg); c["momentum_strategy"] = {**cfg["momentum_strategy"], "n_hold": 15,
                                    "enter_rank": 15, "exit_rank": 30}
    # A holding that slipped to rank 25 sits between enter and exit: keep it.
    held = mom.target_holdings(ranked, {"S24"}, c)      # S24 is rank 25
    assert "S24" in held
    # One that slipped past 30 must go.
    assert "S34" not in mom.target_holdings(ranked, {"S34"}, c)


def test_target_holdings_respects_n_hold(cfg):
    ranked = pd.DataFrame({"symbol": [f"S{i}" for i in range(60)],
                           "rank": list(range(1, 61))})
    assert len(mom.target_holdings(ranked, set(), cfg)) == cfg["momentum_strategy"]["n_hold"]


def test_rank_excludes_stocks_below_200dma(cfg):
    dates = [dt.date(2026, 1, 5)] * 3
    panel = pd.DataFrame({
        "date": dates, "symbol": ["UP", "DOWN", "ALSOUP"],
        "ram": [3.0, 9.0, 2.0], "mom": [30.0, 90.0, 20.0],
        "close": [110.0, 90.0, 120.0], "sma200": [100.0, 100.0, 100.0],
    })
    out = mom.rank_on(panel, dates[0], cfg)
    # DOWN has the best raw momentum but trades below its 200 DMA.
    assert "DOWN" not in list(out["symbol"])
    assert list(out["symbol"]) == ["UP", "ALSOUP"]


def test_rebalance_dates_are_roughly_monthly():
    days = pd.bdate_range("2020-01-01", "2021-01-01").date.tolist()
    r = mom.rebalance_dates(days, 21)
    assert 11 <= len(r) <= 14
    gaps = [(b - a).days for a, b in zip(r, r[1:])]
    assert all(25 <= g <= 40 for g in gaps)


def test_trailing_return_ends_today_unlike_12_1():
    """The display figure includes the latest month; the ranking's does not."""
    close = pd.Series([100.0] * 231 + [150.0] * 21 + [130.0])  # 253 bars
    assert mom.trailing_return(close, 12) == pytest.approx(30.0)
    assert mom.trailing_return(close, 1) == pytest.approx(130 / 150 * 100 - 100)
    assert mom.momentum_score(close).iloc[-1] == pytest.approx(50.0)
    assert mom.trailing_return(close.head(100), 12) is None


# ------------------------------------------------------- dual lookback

def test_lookbacks_puts_primary_first(cfg):
    c = mom.with_lookback(cfg, 12)
    c["momentum_strategy"]["compare_lookback_months"] = 9
    assert mom.lookbacks(c) == [12, 9]
    c["momentum_strategy"]["compare_lookback_months"] = None
    assert mom.lookbacks(c) == [12]


def test_with_lookback_does_not_mutate_the_original(cfg):
    before = cfg["momentum_strategy"]["lookback_months"]
    assert mom.with_lookback(cfg, 9)["momentum_strategy"]["lookback_months"] == 9
    assert cfg["momentum_strategy"]["lookback_months"] == before


def test_common_top_is_the_overlap_ordered_by_combined_rank():
    def r(syms):
        return pd.DataFrame({"symbol": syms, "rank": range(1, len(syms) + 1)})
    rankings = {12: r(["A", "B", "C", "D", "E"]), 9: r(["C", "A", "X", "B", "Y"])}
    # Top 3 of each: {A,B,C} and {C,A,X}. B is 4th on 9 months, so out.
    assert mom.common_top(rankings, 3) == ["A", "C"]
    assert mom.common_top(rankings, 4) == ["A", "C", "B"]


def test_common_top_is_empty_when_a_ranking_is_missing():
    full = pd.DataFrame({"symbol": ["A"], "rank": [1]})
    assert mom.common_top({12: full, 9: pd.DataFrame()}, 15) == []
