"""Point-in-time momentum backtest, free of survivorship bias.

The difference from portfolio_engine.py is the universe. That one takes a fixed
set of frames -- necessarily today's index members -- and ranks within it. This
one rebuilds the eligible universe at every rebalance from the stocks that were
actually trading and liquid on that date, using full-market bhavcopy data.

Why it matters: a company that was a NIFTY 200 member in 2016 and collapsed out
of the index by 2019 is invisible to a fixed-universe backtest. Momentum would
have bought it on the way up and been hurt on the way down, and neither event
appears. Removing the survivors-only lens usually costs several points of CAGR,
and that cost is the honest part of the number.

Prices come from the same bhavcopy table, so a delisted stock simply stops
having rows -- which the engine treats as a forced exit at its last price.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from backtest.costs import CostModel
from backtest.portfolio_engine import PortfolioResult, _metrics

TRADING_DAYS_MONTH = 21


def load_wide(con, start: dt.date, end: dt.date) -> pd.DataFrame:
    """Close prices as a date x symbol matrix. NaN means "not trading"."""
    df = pd.read_sql_query(
        "SELECT date, symbol, close FROM market WHERE date >= ? AND date <= ?",
        con, params=(start.isoformat(), end.isoformat()))
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df.pivot_table(index="date", columns="symbol", values="close",
                          aggfunc="last").sort_index()


def liquid_universe(con, on_date: dt.date, top_n: int, lookback_days: int = 90,
                    min_days: int = 40) -> list[str]:
    """Most liquid names as of a date, computed only from prior data."""
    frm = (on_date - dt.timedelta(days=lookback_days)).isoformat()
    cur = con.execute(
        "SELECT symbol, AVG(turnover) t, COUNT(*) n FROM market "
        "WHERE date <= ? AND date > ? AND turnover > 0 "
        "GROUP BY symbol HAVING n >= ? ORDER BY t DESC LIMIT ?",
        (on_date.isoformat(), frm, min_days, top_n * 2))
    syms = [r[0] for r in cur.fetchall()]
    # ETFs pass any liquidity filter and, being low-volatility, dominate a
    # risk-adjusted ranking -- a liquid-fund ETF is effectively cash with an
    # infinite Sharpe. Exclude anything that is not company equity.
    from data.bhavcopy import equity_symbols
    equities = equity_symbols(con)
    if equities:
        syms = [s for s in syms if s in equities]
    return syms[:top_n]


def run(con, cfg: dict, start: dt.date, end: dt.date, bench: pd.DataFrame,
        start_capital: float = 1_000_000, costs: CostModel | None = None,
        universe_size: int = 200, shuffle_seed: int | None = None) -> PortfolioResult:
    costs = costs or CostModel()
    m = cfg.get("momentum_strategy", {})
    n_hold = m.get("n_hold", 15)
    lb = m.get("lookback_months", 12) * TRADING_DAYS_MONTH
    sk = m.get("skip_months", 1) * TRADING_DAYS_MONTH
    vol_w = m.get("vol_window", 252)
    rebal_days = m.get("rebalance_days", 21)

    # Warm-up: momentum needs lookback + skip bars before it can rank anything.
    px = load_wide(con, start - dt.timedelta(days=int((lb + vol_w) * 1.6)), end)
    if px.empty:
        return PortfolioResult(pd.Series(dtype=float), pd.Series(dtype=float))

    rets = px.pct_change()
    mom = px.shift(sk) / px.shift(lb) - 1
    vol = rets.rolling(vol_w, min_periods=vol_w // 2).std() * np.sqrt(252)
    ram = mom / vol.replace(0, np.nan)
    sma200 = px.rolling(200, min_periods=200).mean()

    dates = [d for d in px.index if d.date() >= start]
    if not dates:
        return PortfolioResult(pd.Series(dtype=float), pd.Series(dtype=float))

    bench_idx = bench.set_index("date")["close"] if not bench.empty else pd.Series(dtype=float)
    regime_n = cfg["signals"].get("regime_sma", 200)
    rng = np.random.default_rng(shuffle_seed) if shuffle_seed is not None else None

    cash = start_capital
    shares: dict[str, float] = {}
    pending: list[str] | None = None
    equity_rows, turnover, holdings_log = [], [], []
    total_costs = 0.0
    last_rebal: dt.date | None = None

    for i, ts in enumerate(dates):
        today = ts.date()
        row = px.loc[ts]

        # --- execute the previous decision at today's prices ---------------
        if pending is not None:
            target = [s for s in pending if s in row.index and not pd.isna(row[s])]
            pending = None
            prices_now = {s: float(row[s]) for s in set(list(shares) + target)
                          if s in row.index and not pd.isna(row[s]) and row[s] > 0}
            portfolio = cash + sum(q * prices_now.get(s, 0.0) for s, q in shares.items())
            weight = portfolio / len(target) if target else 0.0
            traded = 0.0

            for s in list(shares):
                if s not in target and s in prices_now:
                    fill = costs.fill_price(prices_now[s], "sell")
                    c = costs.sell_cost(fill, shares[s])
                    cash += fill * shares[s] - c
                    total_costs += c
                    traded += fill * shares[s]
                    del shares[s]
            for s in target:
                want = weight / prices_now[s]
                have = shares.get(s, 0.0)
                delta = want - have
                if abs(delta) * prices_now[s] < portfolio * 0.005:
                    continue
                if delta > 0:
                    fill = costs.fill_price(prices_now[s], "buy")
                    c = costs.buy_cost(fill, delta)
                    if fill * delta + c > cash:
                        delta = max((cash - c) / fill, 0.0)
                    if delta <= 0:
                        continue
                    c = costs.buy_cost(fill, delta)
                    cash -= fill * delta + c
                    total_costs += c
                    shares[s] = have + delta
                    traded += fill * delta
                else:
                    fill = costs.fill_price(prices_now[s], "sell")
                    c = costs.sell_cost(fill, -delta)
                    cash += fill * (-delta) - c
                    total_costs += c
                    shares[s] = have + delta
                    traded += fill * (-delta)
            if portfolio > 0:
                turnover.append(traded / portfolio)
            holdings_log.append((today, sorted(shares)))

        # --- a holding that stops trading is a delisting, not a free ride ---
        for s in list(shares):
            if s not in row.index or pd.isna(row[s]):
                last = px[s].loc[:ts].dropna()
                if not last.empty and (ts - last.index[-1]).days > 10:
                    cash += float(last.iloc[-1]) * shares[s] * 0.95   # haircut
                    del shares[s]

        value = cash + sum(q * float(row[s]) for s, q in shares.items()
                           if s in row.index and not pd.isna(row[s]))
        equity_rows.append((today, value))

        # --- rebalance -----------------------------------------------------
        due = last_rebal is None or (today - last_rebal).days >= rebal_days * 7 / 5
        if due and i + 1 < len(dates):
            last_rebal = today
            eligible = set(liquid_universe(con, today, universe_size))
            snap = ram.loc[ts]
            cand = [s for s in snap.index
                    if s in eligible and not pd.isna(snap[s])
                    and not pd.isna(sma200.loc[ts].get(s, np.nan))
                    and row.get(s, np.nan) > sma200.loc[ts][s]]
            if not cand:
                continue
            ranked = sorted(cand, key=lambda s: -snap[s])

            risk_on = True
            if not bench_idx.empty:
                hist = bench_idx[bench_idx.index <= today]
                if len(hist) >= regime_n:
                    risk_on = float(hist.iloc[-1]) > float(hist.tail(regime_n).mean())
            n = n_hold if risk_on else max(1, int(n_hold * m.get("risk_off_scale", 0.5)))

            if rng is not None:
                pending = list(rng.choice(ranked, size=min(n, len(ranked)),
                                          replace=False))
            else:
                # Hysteresis: keep a holding until it falls past exit_rank.
                order = {s: k + 1 for k, s in enumerate(ranked)}
                keep = sorted([s for s in shares
                               if order.get(s, 10**6) <= m.get("exit_rank", n * 2)],
                              key=lambda s: order[s])
                for s in ranked:
                    if len(keep) >= n:
                        break
                    if s not in keep and order[s] <= m.get("enter_rank", n):
                        keep.append(s)
                pending = keep[:n]

    equity = pd.Series(dict(equity_rows))
    equity.index = pd.to_datetime(equity.index)
    bcurve = bench_idx.reindex(
        sorted(set(bench_idx.index) & {d.date() for d in dates}))
    if not bcurve.empty:
        bcurve.index = pd.to_datetime(bcurve.index)
    return PortfolioResult(equity, bcurve, turnover, holdings_log,
                           _metrics(equity, bcurve, turnover, total_costs,
                                    len(turnover)), total_costs)
