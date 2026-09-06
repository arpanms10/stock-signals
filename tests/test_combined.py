"""Combined momentum + quality rank."""
import pandas as pd
import pytest

import fundamentals as fu
from scoring import timing
from strategy import combined


@pytest.fixture
def cfg():
    return timing.load_config()


def panel():
    return pd.DataFrame({
        "date": [1] * 4, "symbol": ["A", "B", "C", "D"],
        "ram": [9.0, 8.0, 7.0, 6.0], "mom": [90.0, 80.0, 70.0, 60.0],
        "close": [110.0] * 4, "sma200": [100.0] * 4,
    })


def quality():
    return {
        "A": fu.Quality("A", 80.0, {}, []),
        "B": fu.Quality("B", 20.0, {}, ["pledge RISING: 10% -> 30%"]),
        "C": fu.Quality("C", None, {}, ["no fundamentals on file"]),
    }


def test_low_quality_is_gated_out_despite_strong_momentum(cfg):
    out = combined.combined_rank(panel(), 1, cfg, quality())
    assert "B" not in list(out["symbol"])
    assert list(out["symbol"]) == ["A", "C", "D"]


def test_ranks_are_renumbered_after_gating(cfg):
    out = combined.combined_rank(panel(), 1, cfg, quality())
    assert list(out["rank"]) == [1, 2, 3]


def test_unknown_quality_names_are_surfaced(cfg):
    """NaN, not None, is what survives a pandas column -- the check must
    handle it or unknown risk passes silently as acceptable risk."""
    out = combined.combined_rank(panel(), 1, cfg, quality())
    assert set(combined.unknown_quality_names(out)) == {"C", "D"}


def test_strict_gate_drops_unknowns(cfg):
    c = dict(cfg)
    c["momentum_strategy"] = {**cfg["momentum_strategy"],
                              "allow_unknown_quality": False}
    out = combined.combined_rank(panel(), 1, c, quality())
    assert list(out["symbol"]) == ["A"]


def test_no_quality_data_leaves_momentum_untouched(cfg):
    out = combined.combined_rank(panel(), 1, cfg, None)
    assert list(out["symbol"]) == ["A", "B", "C", "D"]
