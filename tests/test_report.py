"""Backtest reporting statistics, by hand."""
import pandas as pd
import pytest

from backtest import report as r


def test_trade_stats_by_hand():
    pnl = pd.Series([100, -50, -50, 200, -100, 50])
    s = r.trade_stats(pnl)
    assert s["trades"] == 6 and s["win_rate_pct"] == 50.0
    assert s["avg_win"] == pytest.approx(350 / 3, abs=0.01)
    assert s["avg_loss"] == pytest.approx(-200 / 3, abs=0.01)
    assert s["profit_factor"] == pytest.approx(350 / 200)
    assert s["total"] == 150 and s["expectancy"] == 25
    # cumulative: 100, 50, 0, 200, 100, 150 -> worst fall 100 from 100 to 0
    assert s["max_drawdown"] == 100
    assert s["return_to_max_dd"] == 1.5
    assert s["longest_win_streak"] == 1 and s["longest_loss_streak"] == 2


def test_trade_stats_empty_and_no_losses():
    assert r.trade_stats(pd.Series([], dtype=float)) == {"trades": 0}
    s = r.trade_stats(pd.Series([1.0, 2.0]))
    assert s["profit_factor"] is None and s["max_drawdown"] == 0


def test_equity_stats():
    e = pd.Series([100, 110, 99, 105, 121, 120],
                  index=pd.date_range("2024-01-01", periods=6, freq="MS"))
    s = r.equity_stats(e)
    assert s["max_drawdown_pct"] == pytest.approx(-10.0)
    assert s["time_in_drawdown_pct"] == pytest.approx(50.0)
    assert s["longest_drawdown_days"] == 90         # Feb 1 -> May 1 2024 (29+31+30)


def test_month_table_sum_and_compound():
    pnl = pd.Series([10, 20, -5], index=pd.to_datetime(["2024-01-05", "2024-01-20", "2025-03-01"]))
    t = r.month_table(pnl)
    assert t.loc[2024, "Jan"] == 30 and t.loc[2024, "Total"] == 30
    assert t.loc[2025, "Mar"] == -5 and pd.isna(t.loc[2025, "Jan"])
    eq = pd.Series([100, 110, 121], index=pd.to_datetime(["2024-01-01", "2024-01-31", "2024-02-29"]))
    c = r.month_table(eq, "compound")
    assert c.loc[2024, "Jan"] == pytest.approx(10.0)
    assert c.loc[2024, "Feb"] == pytest.approx(10.0)
    assert c.loc[2024, "Total"] == pytest.approx(21.0)


def test_split_stats_by_side():
    t = pd.DataFrame({"side": ["CE", "PE", "CE"], "pnl": [10, -5, -2],
                      "exit_date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"])})
    s = r.split_stats(t, "side").set_index("side")
    assert s.loc["CE", "trades"] == 2 and s.loc["CE", "total"] == 8
    assert s.loc["PE", "win_rate_pct"] == 0


def test_export(tmp_path):
    t = pd.DataFrame({"a": [1, 2]})
    p = r.export(tmp_path / "x.xlsx", {"Trades": t, "Empty": pd.DataFrame()}, trades=t)
    assert p.exists() and (tmp_path / "x.trades.csv").exists()
