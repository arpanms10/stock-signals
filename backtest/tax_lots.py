"""Capital-gains tax on a backtest's trades, lot by lot.

Indian listed equity, delivery:

  short term  held 12 months or less    20%   (15% on sales before 23 Jul 2024)
  long term   held over 12 months       12.5% above Rs 1.25 lakh a year
                                        (10% above Rs 1 lakh before 23 Jul 2024;
                                        exempt on sales before 1 Apr 2018)
  plus 4% health and education cess on the tax.

Lots are sold first-in-first-out, as the depository delivers them. Within a
financial year, long-term losses offset only long-term gains; short-term
losses offset either, the higher-taxed first. What is left is carried
forward for 8 years under the same rule. The exemption comes off the
long-term gains last. STT is not deductible, so it is left out of both
the cost and the proceeds; brokerage, exchange fees, stamp duty and
slippage are in.

`regime="current"` applies today's rates to every sale, which is what a
strategy run from here would pay. `regime="historical"` uses the rates in
force on each sale date. Neither applies the January 2018 grandfathering,
which only touches lots bought before February 2018.

Estimates for comparing strategies, not tax advice: no surcharge, no other
income, and the exemption is assumed unused by anything else unless
use_exemption is off.
"""
from __future__ import annotations

import copy
import datetime as dt
from collections import deque
from dataclasses import dataclass

NEW_RATES = dt.date(2024, 7, 23)
LTCG_TAXED = dt.date(2018, 4, 1)


def fy_of(d: dt.date) -> int:
    """The financial year a date falls in, by its starting year (FY 2024-25 -> 2024)."""
    return d.year if d.month >= 4 else d.year - 1


def fy_end(fy: int) -> dt.date:
    return dt.date(fy + 1, 3, 31)


@dataclass(frozen=True)
class TaxRules:
    regime: str = "current"           # current | historical
    stcg_pct: float = 20.0
    ltcg_pct: float = 12.5
    exemption: float = 125_000
    lt_days: int = 365
    cess_pct: float = 4.0
    use_exemption: bool = True
    carry_years: int = 8

    @classmethod
    def from_config(cls, cfg: dict, **override) -> "TaxRules":
        t = cfg.get("tax", {})
        return cls(stcg_pct=t.get("stcg_rate_pct", 20.0),
                   ltcg_pct=t.get("ltcg_rate_pct", 12.5),
                   exemption=t.get("ltcg_exemption", 125_000),
                   lt_days=t.get("ltcg_threshold_days", 365), **override)

    def rates(self, sold: dt.date) -> tuple[float, float]:
        """(short-term %, long-term %) on a sale made that day."""
        if self.regime == "current" or sold >= NEW_RATES:
            return self.stcg_pct, self.ltcg_pct
        return 15.0, (10.0 if sold >= LTCG_TAXED else 0.0)

    def exemption_for(self, fy: int) -> float:
        if not self.use_exemption:
            return 0.0
        if self.regime == "current" or fy_end(fy) >= NEW_RATES:
            return self.exemption
        return 100_000.0


class TaxBook:
    """Lots held, gains realised per financial year, losses carried."""

    def __init__(self, rules: TaxRules):
        self.rules = rules
        self.lots: dict[str, deque] = {}
        self.gains: dict[int, list[tuple[str, float, float]]] = {}   # fy -> (term, rate, gain)
        self.carry: list[tuple[int, str, float]] = []                 # (fy, term, loss)
        self.settled: set[int] = set()
        self.paid: dict[int, float] = {}

    def buy(self, sym: str, qty: float, cost: float, on: dt.date) -> None:
        """cost: the rupees paid for these shares, fees included, STT not."""
        if qty > 0:
            self.lots.setdefault(sym, deque()).append([qty, cost / qty, on])

    def sell(self, sym: str, qty: float, proceeds: float, on: dt.date) -> None:
        """proceeds: rupees received, fees deducted, STT not."""
        lots = self.lots.get(sym)
        if not lots or qty <= 0:
            return
        per_share = proceeds / qty
        st_rate, lt_rate = self.rules.rates(on)
        left = qty
        while left > 1e-9 and lots:
            lot = lots[0]
            take = min(left, lot[0])
            gain = take * (per_share - lot[1])
            long_term = (on - lot[2]).days > self.rules.lt_days
            self.gains.setdefault(fy_of(on), []).append(
                ("lt", lt_rate, gain) if long_term else ("st", st_rate, gain))
            lot[0] -= take
            left -= take
            if lot[0] <= 1e-9:
                lots.popleft()
        if not lots:
            self.lots.pop(sym, None)

    def settle(self, fy: int) -> float:
        """Tax for one financial year, losses set off and carried. Idempotent."""
        if fy in self.settled:
            return 0.0
        self.settled.add(fy)
        rows = self.gains.get(fy, [])
        st = sorted([[r, g] for t, r, g in rows if t == "st" and g > 0], key=lambda x: -x[0])
        lt = sorted([[r, g] for t, r, g in rows if t == "lt" and g > 0], key=lambda x: -x[0])
        st_loss = -sum(g for t, _, g in rows if t == "st" and g < 0)
        lt_loss = -sum(g for t, _, g in rows if t == "lt" and g < 0)

        def offset(loss: float, buckets: list[list[float]]) -> float:
            for b in buckets:
                used = min(loss, b[1])
                b[1] -= used
                loss -= used
            return loss

        lt_loss = offset(lt_loss, lt)
        st_loss = offset(offset(st_loss, st), lt)
        live = []
        for lfy, term, amount in self.carry:
            if fy - lfy > self.rules.carry_years:
                continue
            amount = offset(amount, st) if term == "st" else amount
            amount = offset(amount, lt)
            if amount > 1e-9:
                live.append((lfy, term, amount))
        self.carry = live
        if st_loss > 1e-9:
            self.carry.append((fy, "st", st_loss))
        if lt_loss > 1e-9:
            self.carry.append((fy, "lt", lt_loss))
        offset(self.rules.exemption_for(fy), lt)
        tax = sum(r / 100 * g for r, g in st + lt) * (1 + self.rules.cess_pct / 100)
        self.paid[fy] = tax
        return tax

    def due(self, today: dt.date) -> float:
        """Tax for every financial year that has ended and is not yet settled."""
        return sum(self.settle(fy) for fy in sorted(self.gains)
                   if fy_end(fy) < today and fy not in self.settled)

    def liquidation_tax(self, marks: dict[str, float], on: dt.date,
                        sell_fraction: float = 1.0) -> float:
        """Tax still owed if everything were sold at `marks` on `on`: the open
        years' realised gains plus the unrealised ones, on a copy."""
        book = copy.deepcopy(self)
        for sym, lots in list(book.lots.items()):
            qty = sum(lot[0] for lot in lots)
            if sym in marks and qty > 0:
                book.sell(sym, qty, qty * marks[sym] * sell_fraction, on)
        return sum(book.settle(fy) for fy in sorted(book.gains) if fy not in book.settled)

    def open_year_tax(self) -> float:
        """Tax owed on realised gains of years not yet settled, on a copy."""
        book = copy.deepcopy(self)
        return sum(book.settle(fy) for fy in sorted(book.gains) if fy not in book.settled)
