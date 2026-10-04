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
from data.bhavcopy import EQUITY_ISIN_PREFIX, require_equity_symbols
from strategy import momentum as mom

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
                    min_days: int = 40, min_price: float = 0.0,
                    min_turnover_cr: float = 0.0) -> list[str]:
    """Most liquid names as of a date, computed only from prior data."""
    frm = (on_date - dt.timedelta(days=lookback_days)).isoformat()
    # ETFs pass any liquidity filter and, being low-volatility, dominate a
    # risk-adjusted ranking -- a liquid-fund ETF is effectively cash with an
    # infinite Sharpe. Only ISIN-confirmed company equity is eligible, and an
    # unbuilt instrument map is an error, not a reason to skip the filter.
    require_equity_symbols(con)
    cur = con.execute(
        "SELECT m.symbol, AVG(m.turnover) t, COUNT(*) n, AVG(m.close) p "
        "FROM market m JOIN instruments i ON i.symbol = m.symbol "
        "WHERE m.date <= ? AND m.date > ? AND m.turnover > 0 "
        "AND i.isin LIKE ? "
        "GROUP BY m.symbol HAVING n >= ? AND p >= ? AND t >= ? "
        "ORDER BY t DESC LIMIT ?",
        (on_date.isoformat(), frm, EQUITY_ISIN_PREFIX + "%", min_days,
         min_price, min_turnover_cr * 1e7, top_n))
    return [r[0] for r in cur.fetchall()]


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
    mom_matrix = px.shift(sk) / px.shift(lb) - 1
    vol = rets.rolling(vol_w, min_periods=vol_w // 2).std() * np.sqrt(252)
    ram = mom_matrix / vol.replace(0, np.nan)
    # NSE's Nifty200 Momentum 30 scores on BOTH 6- and 12-month return over
    # volatility, z-scored across the eligible names and averaged 50/50.
    # The z-scoring has to happen at rank time, over the candidates only.
    nse_blend = m.get("score", "ram") == "nse_blend"
    if nse_blend:
        ram6 = (px.shift(sk) / px.shift(6 * TRADING_DAYS_MONTH) - 1) / vol.replace(0, np.nan)
    # selection: overlap holds only names in the top band on BOTH the primary
    # and the comparison lookback -- the same agreement the live views show.
    alt_lb = (m.get("compare_lookback_months")
              if m.get("selection", "primary") == "overlap" else None)
    if alt_lb:
        ram_alt = (px.shift(sk) / px.shift(alt_lb * TRADING_DAYS_MONTH) - 1) \
            / vol.replace(0, np.nan)
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
    exposure = 1.0
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
            weight = (portfolio * exposure / len(target)) if target else 0.0
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
            eligible = set(liquid_universe(
                con, today, universe_size,
                min_price=m.get("min_price", 0.0),
                min_turnover_cr=m.get("min_turnover_cr", 0.0)))
            snap = ram.loc[ts]
            max_ext = m.get("max_extension_pct", 0.0)
            cand = []
            for s in snap.index:
                if s not in eligible or pd.isna(snap[s]):
                    continue
                ma = sma200.loc[ts].get(s, np.nan)
                px_s = row.get(s, np.nan)
                if pd.isna(ma) or pd.isna(px_s) or px_s <= ma:
                    continue
                # Reject names that have already run far past their own trend.
                if max_ext and 100 * (px_s / ma - 1) > max_ext:
                    continue
                cand.append(s)
            if nse_blend:
                cand = [s for s in cand if not pd.isna(ram6.loc[ts].get(s, np.nan))]
            if alt_lb:
                cand = [s for s in cand if not pd.isna(ram_alt.loc[ts].get(s, np.nan))]
            if not cand:
                continue
            if nse_blend:
                def z(x: pd.Series) -> pd.Series:
                    sd = x.std()
                    return (x - x.mean()) / sd if sd else x * 0
                score = 0.5 * z(snap[cand]) + 0.5 * z(ram6.loc[ts, cand])
                ranked = sorted(cand, key=lambda s: -score[s])
            else:
                ranked = sorted(cand, key=lambda s: -snap[s])

            risk_on = True
            if not bench_idx.empty:
                hist = bench_idx[bench_idx.index <= today]
                if len(hist) >= regime_n:
                    risk_on = float(hist.iloc[-1]) > float(hist.tail(regime_n).mean())
            n = n_hold if risk_on else max(1, int(n_hold * m.get("risk_off_scale", 0.5)))

            # Volatility targeting scales how much capital is deployed, which
            # is separate from how many names are held. Cutting positions and
            # cutting exposure are different levers: one changes concentration,
            # the other changes total risk.
            eq = pd.Series(dict(equity_rows))
            target_exposure = 1.0
            if len(eq) > 30:
                target_exposure, _ = mom.exposure_for_vol(
                    mom.portfolio_vol(eq.pct_change(),
                                      m.get("vol_lookback", 60)), cfg)

            if rng is not None:
                pending = list(rng.choice(ranked, size=min(n, len(ranked)),
                                          replace=False))
            else:
                # Hysteresis: keep a holding until it falls past exit_rank.
                order = {s: k + 1 for k, s in enumerate(ranked)}
                if alt_lb:
                    # A name's standing is its WORSE rank of the two, so it
                    # enters only when top-band on both and leaves when it
                    # falls past exit_rank on either.
                    alt_snap = ram_alt.loc[ts]
                    order2 = {s: k + 1 for k, s in
                              enumerate(sorted(cand, key=lambda s: -alt_snap[s]))}

                    def standing(s):
                        return (max(order.get(s, 10**6), order2.get(s, 10**6)),
                                order.get(s, 10**6))
                else:
                    def standing(s):
                        return (order.get(s, 10**6), 0)
                keep = sorted([s for s in shares
                               if standing(s)[0] <= m.get("exit_rank", n * 2)],
                              key=standing)
                for s in sorted(ranked, key=standing):
                    if len(keep) >= n:
                        break
                    if s not in keep and standing(s)[0] <= m.get("enter_rank", n):
                        keep.append(s)
                pending = keep[:n]

            # Carried to the execution block at the top of the next session,
            # which is where the weights are actually sized.
            exposure = target_exposure

    equity = pd.Series(dict(equity_rows))
    equity.index = pd.to_datetime(equity.index)
    bcurve = bench_idx.reindex(
        sorted(set(bench_idx.index) & {d.date() for d in dates}))
    if not bcurve.empty:
        bcurve.index = pd.to_datetime(bcurve.index)
    return PortfolioResult(equity, bcurve, turnover, holdings_log,
                           _metrics(equity, bcurve, turnover, total_costs,
                                    len(turnover)), total_costs)
