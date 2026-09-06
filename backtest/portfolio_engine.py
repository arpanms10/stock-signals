"""Backtest for rank-based rotation strategies.

Different shape from backtest/engine.py, which simulates discrete trades with
stops. This one holds a *portfolio* that is fully invested by construction and
rebalanced on a schedule -- so the question it answers is "did rotating by rank
beat holding everything?" rather than "did these entry signals work?".

Same discipline as the trade engine: point-in-time only, ranks computed on the
rebalance date's close and executed at the NEXT session's open, realistic
Indian costs on every trade.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from backtest.costs import CostModel
from strategy import momentum as mom

TRADING_DAYS = 252


@dataclass
class PortfolioResult:
    equity: pd.Series
    benchmark: pd.Series
    turnover: list[float] = field(default_factory=list)
    holdings_log: list[tuple] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    total_costs: float = 0.0


def _metrics(equity: pd.Series, bench: pd.Series, turnover: list[float],
             costs: float, n_rebal: int) -> dict:
    if len(equity) < 2:
        return {}
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9)
    cagr = 100 * ((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1)
    rets = equity.pct_change().dropna()
    sharpe = float(np.sqrt(TRADING_DAYS) * rets.mean() / rets.std()) if rets.std() else 0.0
    dd = 100 * (equity / equity.cummax() - 1).min()

    b_cagr = b_dd = np.nan
    if len(bench) > 1:
        b_years = max((bench.index[-1] - bench.index[0]).days / 365.25, 1e-9)
        b_cagr = 100 * ((bench.iloc[-1] / bench.iloc[0]) ** (1 / b_years) - 1)
        b_dd = 100 * (bench / bench.cummax() - 1).min()
    # Downside deviation only -- Sortino does not punish upside volatility,
    # which matters for momentum where the good years are violently good.
    downside = rets[rets < 0].std()
    sortino = float(np.sqrt(TRADING_DAYS) * rets.mean() / downside) if downside else 0.0
    return {
        "years": round(years, 2),
        "cagr_pct": round(cagr, 2),
        "benchmark_cagr_pct": round(float(b_cagr), 2),
        "excess_cagr_pct": round(cagr - float(b_cagr), 2),
        "max_drawdown_pct": round(dd, 2),
        "benchmark_max_drawdown_pct": round(float(b_dd), 2),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "rebalances": n_rebal,
        "avg_turnover_pct": round(100 * float(np.mean(turnover)), 1) if turnover else 0.0,
        "total_costs": round(costs, 0),
        "cost_drag_pct_yr": round(100 * costs / equity.iloc[0] / years, 2),
    }


def run(frames: dict[str, pd.DataFrame], bench: pd.DataFrame, cfg: dict,
        start_capital: float = 1_000_000, costs: CostModel | None = None,
        shuffle_seed: int | None = None) -> PortfolioResult:
    """Simulate monthly rank-based rotation.

    `shuffle_seed` picks holdings at random from the eligible universe instead
    of by rank, keeping position count, rebalance dates and costs identical.
    If ranking carries no information, the real run cannot beat this.
    """
    costs = costs or CostModel()
    m = cfg.get("momentum_strategy", {})
    panel = mom.build_panel(frames, cfg)
    if panel.empty:
        return PortfolioResult(pd.Series(dtype=float), pd.Series(dtype=float))

    all_dates = sorted(panel["date"].unique())
    rebals = set(mom.rebalance_dates(all_dates, m.get("rebalance_days", 21)))
    price = {s: f.set_index("date")["close"] for s, f in frames.items()}
    open_px = {s: f.set_index("date")["open"] if "open" in f.columns
               else f.set_index("date")["close"] for s, f in frames.items()}

    bench_idx = bench.set_index("date")["close"] if not bench.empty else pd.Series(dtype=float)
    regime_n = cfg["signals"].get("regime_sma", 200)
    rng = np.random.default_rng(shuffle_seed) if shuffle_seed is not None else None

    cash = start_capital
    shares: dict[str, float] = {}
    pending: list[str] | None = None
    equity_rows, turnover, holdings_log = [], [], []
    total_costs = 0.0

    for i, today in enumerate(all_dates):
        # --- execute last rebalance decision at today's open ---------------
        if pending is not None:
            target = pending
            pending = None
            prices_now = {s: float(open_px[s].get(today, np.nan)) for s in
                          set(list(shares) + target)}
            prices_now = {s: p for s, p in prices_now.items() if not np.isnan(p) and p > 0}
            portfolio = cash + sum(q * prices_now.get(s, 0.0) for s, q in shares.items())

            # Sell everything not in target, plus trim/add to equal weight.
            weight = portfolio / len(target) if target else 0.0
            traded_value = 0.0
            for s in list(shares):
                if s not in target and s in prices_now:
                    fill = costs.fill_price(prices_now[s], "sell")
                    c = costs.sell_cost(fill, shares[s])
                    cash += fill * shares[s] - c
                    total_costs += c
                    traded_value += fill * shares[s]
                    del shares[s]
            for s in target:
                if s not in prices_now:
                    continue
                want = weight / prices_now[s]
                have = shares.get(s, 0.0)
                delta = want - have
                if abs(delta) * prices_now[s] < portfolio * 0.005:
                    continue                      # ignore trivial rebalancing
                if delta > 0:
                    fill = costs.fill_price(prices_now[s], "buy")
                    c = costs.buy_cost(fill, delta)
                    need = fill * delta + c
                    if need > cash:
                        delta = max((cash - c) / fill, 0.0)
                        need = fill * delta + c
                    if delta <= 0:
                        continue
                    cash -= need
                    total_costs += c
                    shares[s] = have + delta
                    traded_value += fill * delta
                else:
                    fill = costs.fill_price(prices_now[s], "sell")
                    c = costs.sell_cost(fill, -delta)
                    cash += fill * (-delta) - c
                    total_costs += c
                    shares[s] = have + delta
                    traded_value += fill * (-delta)
            if portfolio > 0:
                turnover.append(traded_value / portfolio)
            holdings_log.append((today, sorted(shares)))

        # --- mark to market ------------------------------------------------
        value = cash + sum(q * float(price[s].get(today, np.nan) or 0)
                           for s, q in shares.items()
                           if not np.isnan(price[s].get(today, np.nan)))
        equity_rows.append((today, value))

        # --- decide on rebalance dates, act next session -------------------
        if today in rebals and i + 1 < len(all_dates):
            ranked = mom.rank_on(panel, today, cfg)
            if ranked.empty:
                continue
            # Regime overlay: momentum crashes hardest coming out of sharp
            # reversals, so cut the number of positions rather than the whole
            # book -- staying partially invested is the point of this design.
            risk_on = True
            if not bench_idx.empty:
                hist = bench_idx[bench_idx.index <= today]
                if len(hist) >= regime_n:
                    risk_on = float(hist.iloc[-1]) > float(hist.tail(regime_n).mean())
            n_hold = m.get("n_hold", 15)
            if not risk_on:
                n_hold = max(1, int(n_hold * m.get("risk_off_scale", 0.5)))
            sub = dict(cfg)
            sub["momentum_strategy"] = {**m, "n_hold": n_hold}
            if rng is not None:
                pool = list(ranked["symbol"])
                pick = list(rng.choice(pool, size=min(n_hold, len(pool)), replace=False))
                pending = pick
            else:
                pending = mom.target_holdings(ranked, set(shares), sub)

    equity = pd.Series(dict(equity_rows))
    equity.index = pd.to_datetime(equity.index)
    bcurve = bench_idx.reindex(sorted(set(bench_idx.index) & set(all_dates)))
    if not bcurve.empty:
        bcurve.index = pd.to_datetime(bcurve.index)
    return PortfolioResult(equity, bcurve, turnover, holdings_log,
                           _metrics(equity, bcurve, turnover, total_costs, len(turnover)),
                           total_costs)
