"""Walk-forward backtest.

Two rules this file exists to enforce:

1. It runs the same functions as the live engine (signals.engine). If the two
   diverged, the backtest would stop being evidence about what you actually run.
2. Strict point-in-time. A signal computed on bar i's close is executed at bar
   i+1's *open*, never at bar i's close. Filling at the close of the bar that
   generated the signal is the most common way a backtest quietly cheats, and
   it flatters results by exactly the amount that matters.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from backtest.costs import CostModel
from scoring import timing
from signals import engine
from signals.state import Position

TRADING_DAYS = 252


@dataclass
class Trade:
    symbol: str
    entry_date: dt.date
    entry_price: float
    exit_date: dt.date | None = None
    exit_price: float | None = None
    qty: float = 0
    entry_rule: str = ""
    exit_rule: str = ""
    costs: float = 0.0
    scale_out: bool = False   # a partial book-out, not a separate position

    @property
    def pnl(self) -> float:
        if self.exit_price is None:
            return 0.0
        return (self.exit_price - self.entry_price) * self.qty - self.costs

    @property
    def return_pct(self) -> float:
        if self.exit_price is None or self.entry_price == 0:
            return 0.0
        gross = self.entry_price * self.qty
        return 100 * self.pnl / gross if gross else 0.0


@dataclass
class Result:
    equity: pd.Series
    trades: list[Trade]
    benchmark: pd.Series
    metrics: dict = field(default_factory=dict)


def _metrics(equity: pd.Series, trades: list[Trade], bench: pd.Series,
             exposure: pd.Series) -> dict:
    if equity.empty or len(equity) < 2:
        return {}
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9)
    total = equity.iloc[-1] / equity.iloc[0]
    cagr = 100 * (total ** (1 / years) - 1)

    rets = equity.pct_change().dropna()
    sharpe = (np.sqrt(TRADING_DAYS) * rets.mean() / rets.std()) if rets.std() else 0.0
    dd = 100 * (equity / equity.cummax() - 1).min()

    closed = [t for t in trades if t.exit_price is not None]
    wins = [t for t in closed if t.pnl > 0]
    losses = [t for t in closed if t.pnl <= 0]

    bench_cagr = np.nan
    if not bench.empty and len(bench) > 1:
        bt = bench.iloc[-1] / bench.iloc[0]
        bench_cagr = 100 * (bt ** (1 / years) - 1)
        bench_dd = 100 * (bench / bench.cummax() - 1).min()
    else:
        bench_dd = np.nan

    holding = [((t.exit_date or equity.index[-1].date()) - t.entry_date).days
               for t in closed]
    return {
        "years": round(years, 2),
        "cagr_pct": round(cagr, 2),
        "benchmark_cagr_pct": round(bench_cagr, 2),
        "excess_cagr_pct": round(cagr - bench_cagr, 2),
        "max_drawdown_pct": round(dd, 2),
        "benchmark_max_drawdown_pct": round(bench_dd, 2),
        "sharpe": round(float(sharpe), 2),
        "trades": len(closed),
        "hit_rate_pct": round(100 * len(wins) / len(closed), 1) if closed else 0.0,
        "avg_win_pct": round(float(np.mean([t.return_pct for t in wins])), 2) if wins else 0.0,
        "avg_loss_pct": round(float(np.mean([t.return_pct for t in losses])), 2) if losses else 0.0,
        "avg_holding_days": round(float(np.mean(holding)), 1) if holding else 0.0,
        "time_in_market_pct": round(100 * float(exposure.mean()), 1) if len(exposure) else 0.0,
        "total_costs": round(sum(t.costs for t in closed), 0),
    }


def run(frames: dict[str, pd.DataFrame], bench: pd.DataFrame, cfg: dict,
        start_capital: float = 1_000_000, costs: CostModel | None = None,
        shuffle_seed: int | None = None, use_targets: bool | None = None) -> Result:
    """Simulate the strategy over pre-scored frames.

    `frames` maps symbol -> the output of scoring.pipeline.scored_frame.
    `shuffle_seed` randomises entry *timing* while keeping every other rule
    intact: a strategy with real edge must fall apart under it.
    """
    costs = costs or CostModel()
    risk_cfg = cfg["risk"]
    tcfg = cfg.get("targets", {})
    if use_targets is None:
        use_targets = bool(tcfg.get("enabled"))

    all_dates = sorted({d for f in frames.values() for d in f["date"]})
    bench_idx = bench.set_index("date")["close"] if not bench.empty else pd.Series(dtype=float)

    by_symbol = {s: f.set_index("date", drop=False) for s, f in frames.items()}
    rng = np.random.default_rng(shuffle_seed) if shuffle_seed is not None else None

    cash = start_capital
    positions: dict[str, Position] = {}
    open_trades: dict[str, Trade] = {}
    trades: list[Trade] = []
    pending: list[tuple[str, str, str]] = []   # (symbol, action, rule) for next open
    last_signal: dict[str, dt.date] = {}
    equity_rows, exposure_rows = [], []

    for i, today in enumerate(all_dates):
        # --- 1. Execute yesterday's decisions at today's open -------------
        for symbol, action, rule in pending:
            f = by_symbol.get(symbol)
            if f is None or today not in f.index:
                continue
            row = f.loc[today]
            open_px = row.get("open") or row.get("close")
            if pd.isna(open_px) or open_px <= 0:
                continue

            if action == "BUY" and symbol not in positions:
                portfolio = cash + sum(
                    p.qty * float(by_symbol[s].loc[today]["close"])
                    for s, p in positions.items() if today in by_symbol[s].index)
                stop = engine.initial_stop(float(open_px), float(row.get("atr14") or 0), cfg)
                qty, _ = engine.position_size(float(open_px), stop, portfolio, cfg)
                fill = costs.fill_price(float(open_px), "buy")
                cost = costs.buy_cost(fill, qty)
                if qty > 0 and cash >= fill * qty + cost:
                    cash -= fill * qty + cost
                    t1, t2 = engine.targets(fill, stop, cfg) if use_targets else (0.0, 0.0)
                    positions[symbol] = Position(symbol, qty, fill, today,
                                                 highest_close=fill, stop_price=stop,
                                                 t1=t1, t2=t2)
                    t = Trade(symbol, today, fill, qty=qty, entry_rule=rule, costs=cost)
                    open_trades[symbol] = t
                    trades.append(t)
                    last_signal[symbol] = today

            elif action == "BOOK_T1" and symbol in positions:
                p = positions[symbol]
                book_qty = int(p.qty * tcfg.get("t1_book_pct", 50.0) / 100)
                if book_qty > 0 and not p.t1_booked:
                    fill = costs.fill_price(float(open_px), "sell")
                    cost = costs.sell_cost(fill, book_qty)
                    cash += fill * book_qty - cost
                    leg = Trade(symbol, p.entry_date, p.entry_price, today, fill,
                                book_qty, p.entry_rule if hasattr(p, "entry_rule") else "",
                                "target_t1", cost, scale_out=True)
                    trades.append(leg)
                    p.qty -= book_qty
                    p.t1_booked = True
                    # Risk is now off the table: the remainder rides on a
                    # breakeven stop, so the worst case is a scratch.
                    if tcfg.get("breakeven_after_t1", True):
                        p.stop_price = max(p.stop_price, p.entry_price)
                    t = open_trades.get(symbol)
                    if t:
                        t.qty = p.qty

            elif action == "SELL" and symbol in positions:
                p = positions.pop(symbol)
                fill = costs.fill_price(float(open_px), "sell")
                cost = costs.sell_cost(fill, p.qty)
                cash += fill * p.qty - cost
                t = open_trades.pop(symbol, None)
                if t:
                    t.exit_date, t.exit_price, t.exit_rule = today, fill, rule
                    t.costs += cost
                last_signal[symbol] = today
        pending = []

        # --- 2. Mark to market -------------------------------------------
        holdings_value = 0.0
        for s, p in positions.items():
            f = by_symbol[s]
            if today in f.index:
                holdings_value += p.qty * float(f.loc[today]["close"])
            else:
                holdings_value += p.qty * p.entry_price
        equity_rows.append((today, cash + holdings_value))
        exposure_rows.append((today, 1.0 if positions else 0.0))

        if i + 1 >= len(all_dates):
            continue

        # --- 3. Evaluate rules on today's close, to act at tomorrow's open --
        regime = engine.regime_ok(bench, today, cfg) if not bench.empty else True

        for symbol, f in by_symbol.items():
            if today not in f.index:
                continue
            pos_idx = f.index.get_loc(today)
            if pos_idx == 0:
                continue
            row, prev = f.iloc[pos_idx], f.iloc[pos_idx - 1]

            if symbol in positions:
                pos = engine.update_trailing(positions[symbol], row, cfg)
                positions[symbol] = pos
                hit = engine.check_exit(row, prev, pos, cfg)
                if hit:
                    pending.append((symbol, "SELL", hit[0]))
                    continue
                if use_targets and pos.t2 and pos.t1_booked and row["close"] >= pos.t2:
                    pending.append((symbol, "SELL", "target_t2"))
                elif use_targets and pos.t1 and not pos.t1_booked and row["close"] >= pos.t1:
                    pending.append((symbol, "BOOK_T1", "target_t1"))
                continue

            if len(positions) + sum(1 for p in pending if p[1] == "BUY") >= risk_cfg["max_positions"]:
                continue
            if not regime:
                continue
            if engine.in_cooldown(last_signal.get(symbol), today, cfg):
                continue

            fired = engine.check_entry(row, prev, cfg)
            if rng is not None:
                # Shuffle control: same number of chances, random timing.
                fired = ("shuffled", "randomised entry") if rng.random() < 0.004 else None
            if fired:
                pending.append((symbol, "BUY", fired[0]))

    equity = pd.Series(dict(equity_rows)); equity.index = pd.to_datetime(equity.index)
    exposure = pd.Series(dict(exposure_rows))
    bench_curve = bench_idx.reindex(sorted(bench_idx.index.intersection(all_dates))) \
        if not bench_idx.empty else pd.Series(dtype=float)
    if not bench_curve.empty:
        bench_curve.index = pd.to_datetime(bench_curve.index)

    return Result(equity=equity, trades=trades, benchmark=bench_curve,
                  metrics=_metrics(equity, trades, bench_curve, exposure))
