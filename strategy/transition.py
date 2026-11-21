"""When to make each trim, so the transition costs the least tax.

FIFO fixes WHICH shares a sale delivers; the only lever is WHEN. Within a
horizon (3, 6 or 12 months) each trim may be made:

  now                        the default
  when a lot turns long-term the shares FIFO would deliver cross 12 months
                             and their gain drops from 20% to 12.5%
  in the next financial year from 1 April a fresh ₹1.25 lakh LTCG exemption,
                             and a fresh year to set losses against

Every combination of dates is tried (a handful of trims, a handful of dates
each) and the cheapest kept, counting this year's gains already realised.
A trim is delayed only when waiting saves at least `min_saving_to_wait`:
a trim corrects drift, and drift left alone is a cost too.

Prices are held at today's: the plan times tax, it doesn't predict prices.
Pure: no I/O. Estimates, not tax advice.
"""
from __future__ import annotations

import datetime as dt
import itertools
from dataclasses import dataclass, field

from strategy import capgains as cg
from strategy.capgains import Lot


@dataclass
class Trim:
    symbol: str
    qty: float
    price: float
    reason: str = ""


@dataclass
class ScheduledTrim:
    symbol: str
    qty: float
    price: float
    date: dt.date
    st_gain: float
    lt_gain: float
    tax_if_now: float          # this trim alone, sold today, after this year's realised
    costs: float
    why: str
    reason: str = ""
    unmatched_qty: float = 0.0

    @property
    def value(self) -> float:
        return self.qty * self.price


@dataclass
class Schedule:
    horizon_months: int
    rows: list[ScheduledTrim]
    tax_plan: float            # incremental tax over what this year's realised gains already owe
    tax_all_now: float
    costs: float
    by_fy: list[dict]
    notes: list[str] = field(default_factory=list)

    @property
    def saved(self) -> float:
        return max(self.tax_all_now - self.tax_plan, 0.0)


def add_months(d: dt.date, m: int) -> dt.date:
    y, mo = divmod(d.month - 1 + m, 12)
    y += d.year
    mo += 1
    import calendar
    return dt.date(y, mo, min(d.day, calendar.monthrange(y, mo)[1]))


def candidates(lots: list[Lot], qty: float, today: dt.date, end: dt.date,
               cfg: dict) -> list[dt.date]:
    days = cfg["long_term_after_days"]
    out = {today}
    sale, _ = cg.sell_fifo(lots, qty, 0.0, today, cfg)
    for p in sale.pieces:
        if not p["long_term"] and p["date"] is not None:
            d = p["date"] + dt.timedelta(days=days + 1)
            if today < d <= end:
                out.add(d)
    nfy = cg.fy_start(cg.fy_of(today))
    nfy = dt.date(nfy.year + 1, 4, 1)
    if nfy <= end:
        out.add(nfy)
    return sorted(out)


def _tax(sales: list[cg.Sale], realised: dict[str, tuple[float, float]], cfg: dict,
         fys: set[str] | None = None) -> tuple[float, list[dict]]:
    by: dict[str, list[float]] = {}
    last: dict[str, dt.date] = {}
    for fy, (st, lt) in realised.items():
        by.setdefault(fy, [0.0, 0.0])
        by[fy][0] += st
        by[fy][1] += lt
        last[fy] = cg.fy_start(fy)
    for s in sales:
        fy = cg.fy_of(s.date)
        by.setdefault(fy, [0.0, 0.0])
        by[fy][0] += s.st_gain
        by[fy][1] += s.lt_gain
        last[fy] = max(last.get(fy, s.date), s.date)
    total, rows = 0.0, []
    for fy in sorted(by):
        if fys is not None and fy not in fys:
            continue
        st, lt = by[fy]
        t = cg.fy_tax(st, lt, last[fy], cfg)
        base = realised.get(fy, (0.0, 0.0))
        t0 = cg.fy_tax(base[0], base[1], last[fy], cfg)["tax"]
        rows.append({"fy": fy, "st_gain": st, "lt_gain": lt, "realised_st": base[0],
                     "realised_lt": base[1], "exemption_used": t["exemption_used"],
                     "exemption_left": t["exemption_left"], "tax": t["tax"] - t0,
                     "carry_st_loss": t["carry_st_loss"], "carry_lt_loss": t["carry_lt_loss"]})
        total += t["tax"] - t0
    return total, rows


def schedule(trims: list[Trim], lots: dict[str, list[Lot]], today: dt.date, horizon: int,
             realised: dict[str, tuple[float, float]], cfg: dict) -> Schedule:
    """Time each trim within `horizon` months to minimise tax."""
    tc = cfg["transition"]
    end = add_months(today, horizon)
    trims = [t for t in trims if t.qty > 0]
    opts = [candidates(lots.get(t.symbol, []), t.qty, today, end, cfg) for t in trims]

    def sales_for(dates):
        return [cg.sell_fifo(lots.get(t.symbol, []), t.qty, t.price, d, cfg)[0]
                for t, d in zip(trims, dates)]

    def cost(dates):
        return _tax(sales_for(dates), realised, cfg)[0]

    now = [today] * len(trims)
    tax_now = cost(now)
    combos = 1
    for o in opts:
        combos *= len(o)
    if len(trims) <= tc["max_brute_force_trims"] and combos <= 50_000:
        best = min(itertools.product(*opts), key=lambda ds: (round(cost(ds), 2), ds))
        best = list(best)
    else:                                       # one holding at a time
        best = list(now)
        for i, o in enumerate(opts):
            best[i] = min(o, key=lambda d: (round(cost(best[:i] + [d] + best[i + 1:]), 2), d))
    # Delay only where it pays: pull each trim back to today if waiting saves too little.
    small: dict[int, float] = {}
    for i in range(len(trims)):
        if best[i] != today:
            trial = best[:i] + [today] + best[i + 1:]
            gap = cost(trial) - cost(best)
            if gap < tc["min_saving_to_wait"]:
                best = trial
                small[i] = gap
    sales = sales_for(best)
    tax_plan, by_fy = _tax(sales, realised, cfg)
    rows = []
    for i, (t, d, s) in enumerate(zip(trims, best, sales)):
        alone_now = cg.sell_fifo(lots.get(t.symbol, []), t.qty, t.price, today, cfg)[0]
        tn = _tax([alone_now], realised, cfg)[0]
        if d == today and small.get(i, 0) > 0.5:
            why = (f"now: waiting would save only ₹{small[i]:,.0f} "
                   f"(under ₹{tc['min_saving_to_wait']:,})")
        elif d == today:
            why = "now: no date in the horizon is cheaper"
        elif d.month == 4 and d.day == 1:
            why = "from 1 April: a new year's ₹1.25 lakh exemption and set-off"
        else:
            why = "when the shares FIFO delivers turn long-term"
        if s.unmatched_qty:
            why += f"; {s.unmatched_qty:g} shares not covered by any lot"
        rows.append(ScheduledTrim(t.symbol, t.qty, t.price, d, s.st_gain, s.lt_gain, tn,
                                  cg.sale_costs(t.qty * t.price, cfg), why, t.reason,
                                  s.unmatched_qty))
    notes = ["Prices held at today's; the gain on a later date will differ."]
    return Schedule(horizon, rows, tax_plan, tax_now, sum(r.costs for r in rows), by_fy, notes)


def harvest_gains(lots: dict[str, list[Lot]], prices: dict[str, float], today: dt.date,
                  exemption_left: float, cfg: dict) -> list[dict]:
    """Long-term gains that could be booked tax-free this year (sell and buy
    back): fills the unused exemption, oldest lots first, largest gains first.
    Saves tax later at the long-term rate; costs a round trip, and the shares
    bought back start a new 12 months."""
    days = cfg["long_term_after_days"]
    r = cg.regime(today, cfg)
    mult = (1 + cfg.get("surcharge_pct", 0) / 100) * (1 + cfg.get("cess_pct", 0) / 100)
    cands = []
    for sym, ls in lots.items():
        px = prices.get(sym)
        if not px:
            continue
        q = g = 0.0
        for l in ls:                       # FIFO prefix of long-term lots
            if not l.is_long(today, days):
                break
            q += l.qty
            g += (px - l.cost) * l.qty
        if q and g > 0:
            cands.append({"symbol": sym, "lt_qty": q, "gain": g, "price": px,
                          "per_share": g / q})
    out, left = [], exemption_left
    for c in sorted(cands, key=lambda c: -c["gain"]):
        if left <= 0:
            break
        take_g = min(c["gain"], left)
        qty = int(c["lt_qty"] * take_g / c["gain"])
        if qty < 1:
            continue
        gain = qty * c["per_share"]
        value = qty * c["price"]
        rt = cg.sale_costs(value, cfg) + cg.buy_costs(value, cfg)
        out.append({"symbol": c["symbol"], "qty": qty, "gain": gain,
                    "future_tax_saved": gain * r["ltcg_pct"] / 100 * mult,
                    "round_trip_cost": rt})
        left -= gain
    return out


def unrealised_losses(lots: dict[str, list[Lot]], prices: dict[str, float], today: dt.date,
                      cfg: dict) -> list[dict]:
    days = cfg["long_term_after_days"]
    out = []
    for sym, ls in lots.items():
        px = prices.get(sym)
        if not px:
            continue
        st = sum((px - l.cost) * l.qty for l in ls if not l.is_long(today, days))
        lt = sum((px - l.cost) * l.qty for l in ls if l.is_long(today, days))
        if st < 0 or lt < 0:
            out.append({"symbol": sym, "st_loss": min(st, 0.0), "lt_loss": min(lt, 0.0)})
    return sorted(out, key=lambda x: x["st_loss"] + x["lt_loss"])
