"""Backtest simple option rules on the F&O bhavcopy history.

The question this answers is not "will this contract make money?" -- the
validation found nothing in the chain that predicts direction -- but "what
has a repeatable rule like this done, after costs?": buy the put one straddle
below spot 10 sessions before every monthly expiry, say, and hold to expiry.

A rule:
    action    buy | sell
    side      CE | PE
    strike    atm            strike nearest spot
              moves:+1.0     spot + 1.0 ATM straddles (negative = below)
              pct:-3         spot - 3%
              put_wall | call_wall   the largest OI strike on that side
    sessions  entry this many trading sessions before expiry (the history
              stores chains at 20/10/5 before monthly, 4/2/1 before weekly)
    cycle     monthly | weekly
    universe  indices | stocks | all | a list of symbols

Execution, and what is assumed:
  * Entry at the day's CLOSE for that strike, and only if the strike traded
    that day (an untraded close is a carried or theoretical price). Slippage
    and every charge in backtest.costs.OptionCostModel are applied.
  * Held to expiry, valued at intrinsic against the underlying's expiry-day
    close. How it is closed differs (see settle()):
      - index options are cash-settled: an in-the-money long pays exercise
        STT on the intrinsic value; a short pays nothing more;
      - stock options are PHYSICALLY settled (since 2019) -- holding an
        in-the-money one into expiry means delivering or taking the shares.
        Traders square off instead, so an in-the-money stock option is
        closed on expiry day at its intrinsic value: a normal sell (long)
        or buy-back (short), with charges and slippage.
  * One lot per trade. P&L is per lot in rupees; also per unit, and as a %
    of premium. Before 2024 (old-format index history) the lot size is not
    in the file: the earliest known lot for that index is used for rupee
    figures, so treat pre-2024 rupee amounts as approximate.
  * Cycles with a split or bonus inside are dropped (as in the validation).

Nothing here chooses a strategy. It reports what a stated rule would have
done, with the trade log to check it against.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from backtest.costs import OptionCostModel
from strategy import fno

MAX_CYCLE_RATIO = 2.0
INDICES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}


@dataclass
class Rule:
    action: str = "buy"
    side: str = "PE"
    strike: str = "atm"
    sessions: int = 10
    cycle: str = "monthly"
    universe: object = "indices"

    @property
    def name(self) -> str:
        u = self.universe if isinstance(self.universe, str) else "+".join(self.universe)
        return f"{self.action} {self.side} {self.strike} @{self.sessions}s {self.cycle} {u}"

    def validate(self) -> "Rule":
        if self.action not in ("buy", "sell"):
            raise ValueError("action must be buy or sell")
        if self.side not in ("CE", "PE"):
            raise ValueError("side must be CE or PE")
        if self.cycle not in ("monthly", "weekly"):
            raise ValueError("cycle must be monthly or weekly")
        target_kind(self.strike)   # raises on a bad strike rule
        return self


def target_kind(rule: str) -> tuple[str, float]:
    rule = rule.strip().lower()
    if rule in ("atm", "put_wall", "call_wall"):
        return rule, 0.0
    for prefix in ("moves:", "pct:"):
        if rule.startswith(prefix):
            try:
                return prefix[:-1], float(rule[len(prefix):])
            except ValueError:
                raise ValueError(f"strike rule {rule!r}: expected a number after "
                                 f"'{prefix}', e.g. {prefix}-1") from None
    raise ValueError(f"unknown strike rule {rule!r}: atm | moves:+1 | pct:-3 | "
                     "put_wall | call_wall")


@dataclass
class Result:
    rule: Rule
    trades: pd.DataFrame
    skipped: dict = field(default_factory=dict)


def _in_universe(symbol: str, kind: str, universe) -> bool:
    if isinstance(universe, str):
        if universe == "all":
            return True
        if universe == "indices":
            return kind == "index"
        if universe == "stocks":
            return kind == "equity"
        return symbol == universe.upper()
    return symbol in {u.upper() for u in universe}


def pick_strike(chain: pd.DataFrame, spot: float, rule: Rule, cfg: dict | None = None):
    """(strike, target level) for the rule, choosing among strikes that traded
    on the option's side that day; None when nothing suitable traded."""
    side = rule.side.lower()
    traded = chain[chain[f"{side}_ltp"].notna() & (chain[f"{side}_vol"] > 0)]
    if traded.empty:
        return None, "no traded strike"
    kind, x = target_kind(rule.strike)
    strad = fno.straddle(chain, spot)
    if kind == "atm":
        target = spot
    elif kind == "moves":
        if not strad:
            return None, "no straddle"
        target = spot + x * strad
    elif kind == "pct":
        target = spot * (1 + x / 100)
    else:
        s = fno.settings(cfg)
        walls = fno.walls(chain, spot, "pe" if kind == "put_wall" else "ce", s, n=1)
        if not walls:
            return None, "no wall"
        target = walls[0].strike
    k = traded["strike"].to_numpy(float)
    best = float(k[int(np.argmin(np.abs(k - target)))])
    # Too far from what the rule asked for is a different trade, not this one.
    tol = max(0.5 * (strad or 0), 0.01 * spot)
    if abs(best - target) > tol:
        return None, "nearest traded strike too far from target"
    return best, target


def settle(action: str, kind: str, entry: float, intrinsic: float, lot: int,
           costs: OptionCostModel) -> tuple[float, float, str]:
    """(P&L per unit, total costs, how it closed) for one lot held to expiry.

    `entry` is the entry fill (slippage already in). Entry charges are
    included here too, so this is the whole round trip."""
    if action == "buy":
        cost = costs.buy_cost(entry, lot)
        if intrinsic <= 0:
            return -entry, cost, "expired worthless"
        if kind == "index":
            return intrinsic - entry, cost + costs.exercise_cost(intrinsic, lot), "cash-settled"
        out = costs.fill(intrinsic, "sell")
        return out - entry, cost + costs.sell_cost(out, lot), "squared off"
    cost = costs.sell_cost(entry, lot)
    if intrinsic <= 0:
        return entry, cost, "expired worthless"
    if kind == "index":
        # Assigned and cash-settled; exercise STT falls on the buyer.
        return entry - intrinsic, cost, "cash-settled"
    back = costs.fill(intrinsic, "buy")
    return entry - back, cost + costs.buy_cost(back, lot), "squared off"


def run(con, rule: Rule, costs: OptionCostModel | None = None,
        start: str | None = None, end: str | None = None,
        cfg: dict | None = None) -> Result:
    rule.validate()
    costs = costs or OptionCostModel()
    q = "SELECT * FROM fo_chain WHERE cycle = ? AND sessions = ?"
    params: list = [rule.cycle, rule.sessions]
    if start:
        q += " AND date >= ?"
        params.append(start)
    if end:
        q += " AND date <= ?"
        params.append(end)
    chains = pd.read_sql_query(q, con, params=params)
    spot = pd.read_sql_query("SELECT date, symbol, kind, spot, lot, src FROM fo_spot", con)
    by_sym = {s: g.set_index("date") for s, g in spot.groupby("symbol")}
    first_lot = (spot.dropna(subset=["lot"]).sort_values("date")
                 .groupby("symbol")["lot"].first().to_dict())
    for side in ("ce", "pe"):
        for f in ("iv", "bid", "ask"):
            chains[f"{side}_{f}"] = np.nan

    rows, skipped = [], {}

    def skip(why):
        skipped[why] = skipped.get(why, 0) + 1

    for (date, sym, expiry), g in chains.groupby(["date", "symbol", "expiry"], sort=True):
        hist = by_sym.get(sym)
        if hist is None or date not in hist.index:
            skip("no spot history")
            continue
        kind = hist.loc[date, "kind"]
        if not _in_universe(sym, kind, rule.universe):
            continue
        if expiry not in hist.index:
            skip("expiry not reached yet")
            continue
        s0 = float(g["spot"].iloc[0])
        close = float(hist.loc[expiry, "spot"])
        path = hist.loc[(hist.index > date) & (hist.index <= expiry)]
        lot0 = hist.loc[date, "lot"]
        if ((pd.notna(lot0) and (path["lot"].dropna() != lot0).any())
                or not (1 / MAX_CYCLE_RATIO < close / s0 < MAX_CYCLE_RATIO)):
            skip("split/bonus inside the cycle")
            continue
        chain = g.sort_values("strike").reset_index(drop=True)
        strike, target = pick_strike(chain, s0, rule, cfg)
        if strike is None:
            skip(target)
            continue
        side = rule.side.lower()
        row = chain[chain["strike"] == strike].iloc[0]
        premium = float(row[f"{side}_ltp"])
        lot = lot0 if pd.notna(lot0) else first_lot.get(sym)
        if not lot or pd.isna(lot):
            skip("no lot size")
            continue
        lot = int(lot)
        intrinsic = max(close - strike, 0.0) if rule.side == "CE" else max(strike - close, 0.0)
        entry = costs.fill(premium, rule.action)
        per_unit, cost, how = settle(rule.action, kind, entry, intrinsic, lot, costs)
        pnl = per_unit * lot - cost
        rows.append({
            "symbol": sym, "kind": kind, "cycle": rule.cycle, "src": hist.loc[date, "src"],
            "entry_date": date, "exit_date": expiry, "sessions": rule.sessions,
            "spot": s0, "target": round(target, 2), "strike": strike,
            "side": rule.side, "action": rule.action,
            "close_premium": premium, "entry_fill": round(entry, 2),
            "expiry_close": close, "intrinsic": round(intrinsic, 2),
            "lot": lot, "costs": round(cost, 2), "closed": how,
            "pnl_per_unit": round(per_unit, 2), "pnl": round(pnl, 2),
            "return_on_premium_pct": round(100 * pnl / (entry * lot), 1) if entry else None,
            "lot_estimated": bool(pd.isna(lot0)),
        })
    if not rows and not skipped:
        known = set(spot["symbol"])
        if not isinstance(rule.universe, str) or rule.universe not in ("indices", "stocks", "all"):
            names = rule.universe if not isinstance(rule.universe, str) else [rule.universe]
            missing = [n for n in names if n.upper() not in known]
            if missing:
                skipped["not in the F&O history: " + ", ".join(m.upper() for m in missing)] = 1
        if rule.cycle == "weekly" and not skipped:
            skipped["weekly expiries exist for indices only (stocks are monthly)"] = 1
        if not skipped:
            skipped[f"no stored chains {rule.sessions} sessions before a {rule.cycle} "
                    "expiry (stored: 20/10/5 monthly, 4/2/1 weekly)"] = 1
    trades = pd.DataFrame(rows)
    if not trades.empty:
        trades["entry_date"] = pd.to_datetime(trades["entry_date"])
        trades["exit_date"] = pd.to_datetime(trades["exit_date"])
        trades = trades.sort_values(["exit_date", "symbol"]).reset_index(drop=True)
    return Result(rule, trades, skipped)
