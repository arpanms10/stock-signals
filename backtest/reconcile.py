"""Check a backtest's trades against the source data before trusting a metric.

Every number a backtest reports is built from its fills. If a fill is wrong
-- a date off by one, the wrong contract, a stale close -- every statistic on
top is wrong too, and nothing in the statistics says so. So before the
metrics, a sample of trades is rebuilt from source and compared.

  options(trades)      re-downloads NSE's bhavcopy for each sampled trade's
                       entry and expiry dates (and, before 2024, the index's
                       own close) and checks the option's close, that it
                       traded, the expiry-day close, and that the P&L follows
                       from them. Independent of fo_history.db: it goes back
                       to the files.
  equity_trades(...)   the trade engine fills at the next session's open with
                       slippage; checks each sampled fill against that open in
                       the price frames, and that entry and exit fall on
                       trading days.

Each returns one row per check with ok True/False; a backtest prints them and
says loudly if any failed.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

TOL = 0.011     # prices are stored to 2 dp; allow rounding


def _sample(trades: pd.DataFrame, k: int, seed: int) -> pd.DataFrame:
    if len(trades) <= k:
        return trades
    # Spread the sample across eras, so old-format and UDiFF are both checked.
    if "src" in trades and trades["src"].nunique() > 1:
        per = max(1, k // trades["src"].nunique())
        parts = [g.sample(min(per, len(g)), random_state=seed)
                 for _, g in trades.groupby("src")]
        out = pd.concat(parts)
        if len(out) < k:
            rest = trades.drop(out.index)
            out = pd.concat([out, rest.sample(k - len(out), random_state=seed)])
        return out
    return trades.sample(k, random_state=seed)


def _day_files(day: dt.date, src: str, symbol: str):
    """(option rows normalised, underlying close) for one day, from source."""
    from data import fo_bhavcopy as fb
    if src == "old":
        from jugaad_data.nse import index_df
        name = fb.INDEX_NAMES.get(symbol)
        spot = np.nan
        if name:
            try:
                df = index_df(symbol=name, from_date=day, to_date=day)
                if len(df):
                    spot = float(df["CLOSE"].iloc[0])
            except Exception:
                pass
        return fb.fetch_day_old(day, {symbol: spot}), spot
    norm = fb.fetch_day(day)
    if norm.empty:
        return norm, np.nan
    s = norm.loc[norm["symbol"] == symbol, "spot"]
    return norm, float(s.median()) if len(s) else np.nan


def options(trades: pd.DataFrame, k: int = 5, seed: int = 7, costs=None) -> pd.DataFrame:
    from backtest.costs import OptionCostModel
    costs = costs or OptionCostModel()
    rows = []

    def check(t, what, stored, source, ok=None):
        if ok is None:
            ok = (pd.notna(stored) and pd.notna(source)
                  and abs(float(stored) - float(source)) <= TOL * max(1, abs(float(stored))) / 100 + 0.006)
        rows.append({"symbol": t.symbol, "entry_date": t.entry_date.date(),
                     "expiry": t.exit_date.date(), "strike": t.strike, "side": t.side,
                     "check": what, "stored": stored, "source": source, "ok": bool(ok)})

    for t in _sample(trades, k, seed).itertuples():
        src = t.src or "udiff"
        entry_norm, _ = _day_files(t.entry_date.date(), src, t.symbol)
        o = entry_norm[(entry_norm["symbol"] == t.symbol) & (entry_norm["instr"] == "opt")
                       & (entry_norm["expiry"] == t.exit_date.date())
                       & (entry_norm["strike"] == t.strike) & (entry_norm["opt"] == t.side)]
        if o.empty:
            check(t, "contract found in entry-day file", True, False, ok=False)
            continue
        check(t, "option close on entry day", t.close_premium, float(o["close"].iloc[0]))
        check(t, "traded on entry day", True, float(o["vol"].iloc[0]) > 0,
              ok=float(o["vol"].iloc[0]) > 0)
        _, spot_exp = _day_files(t.exit_date.date(), src, t.symbol)
        check(t, "underlying close on expiry", t.expiry_close, spot_exp)
        # Rebuild the P&L from the source numbers alone.
        prem = float(o["close"].iloc[0])
        intr = (max(spot_exp - t.strike, 0) if t.side == "CE" else max(t.strike - spot_exp, 0)) \
            if pd.notna(spot_exp) else np.nan
        if t.action == "buy":
            fill = costs.fill(prem, "buy")
            pnl = (intr - fill) * t.lot - costs.buy_cost(fill, t.lot) - costs.exercise_cost(intr, t.lot)
        else:
            fill = costs.fill(prem, "sell")
            pnl = (fill - intr) * t.lot - costs.sell_cost(fill, t.lot)
        check(t, "P&L rebuilt from source", t.pnl, round(pnl, 2),
              ok=pd.notna(pnl) and abs(t.pnl - pnl) <= 0.05 * t.lot + 0.5)
    return pd.DataFrame(rows)


def equity_trades(trades, frames: dict, costs, k: int = 5, seed: int = 7) -> pd.DataFrame:
    """Trade-engine fills: next-session open +/- slippage, on real sessions."""
    rows = []
    closed = [t for t in trades if t.exit_price is not None and not t.scale_out]
    rng = np.random.default_rng(seed)
    pick = rng.choice(len(closed), size=min(k, len(closed)), replace=False) if closed else []
    for i in pick:
        t = closed[int(i)]
        f = frames.get(t.symbol)
        if f is None:
            rows.append({"symbol": t.symbol, "check": "price frame", "ok": False})
            continue
        fx = f.set_index("date") if "date" in f.columns else f
        for when, d, px, side in (("entry", t.entry_date, t.entry_price, "buy"),
                                  ("exit", t.exit_date, t.exit_price, "sell")):
            on_day = d in fx.index
            rows.append({"symbol": t.symbol, "check": f"{when} on a trading day",
                         "date": d, "stored": True, "source": on_day, "ok": on_day})
            if on_day:
                op = fx.loc[d].get("open")
                op = fx.loc[d]["close"] if op is None or pd.isna(op) else op
                exp = costs.fill_price(float(op), side)
                rows.append({"symbol": t.symbol, "check": f"{when} fill = open +/- slippage",
                             "date": d, "stored": round(px, 4), "source": round(exp, 4),
                             "ok": abs(px - exp) <= 1e-6 * max(1, exp) + 1e-6})
    return pd.DataFrame(rows)


def summary(checks: pd.DataFrame) -> str:
    if checks.empty:
        return "Reconciliation: nothing to check."
    bad = checks[~checks["ok"]]
    head = (f"Reconciliation: {len(checks) - len(bad)}/{len(checks)} checks passed"
            + ("" if bad.empty else " -- FAILURES below; do not trust the metrics until explained"))
    if bad.empty:
        return head
    lines = [head] + [f"  FAIL {r.symbol} {getattr(r, 'entry_date', getattr(r, 'date', ''))}: "
                      f"{r.check} stored={r.stored} source={r.source}" for r in bad.itertuples()]
    return "\n".join(lines)
