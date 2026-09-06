"""Holding period, capital-gains estimates, and trim timing.

Two different questions get conflated when a framework says "trim":

  HOW MUCH   is arithmetic -- the excess over the position cap.
  WHEN       is judgement, and depends on tax, on how far the position has
             drifted, and on whether anything is actually wrong.

The distinction that matters most: a trim is a DRIFT CORRECTION, not an
emergency. Nothing is wrong with the company. So there is no reason to execute
it into weakness, on the day it triggers, or three weeks before a position
turns long-term and its tax rate falls by a third.

An exit is the opposite -- the thesis is broken, and tax is a smaller
consideration than the reason you are leaving.

Estimates only. Rates live in config/scoring.yaml because Budgets change them.
This is not tax advice.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass
class TaxView:
    holding_days: int | None
    is_long_term: bool | None
    gain_per_share: float
    total_gain: float
    estimated_tax: float
    rate_pct: float
    days_to_ltcg: int | None
    note: str
    lt_shares_sold: float = 0.0
    st_shares_sold: float = 0.0
    tax_saved_vs_worst: float = 0.0


def holding_period(purchase_date: str | None,
                   today: dt.date | None = None) -> int | None:
    if not purchase_date:
        return None
    today = today or dt.date.today()
    try:
        bought = dt.date.fromisoformat(str(purchase_date)[:10])
    except ValueError:
        return None
    return (today - bought).days


def assess_split(price: float, avg_cost: float, qty: float, cfg: dict,
                 lt_qty: float, st_qty: float) -> TaxView:
    """Tax when the holding's long/short split is known.

    Sells the LONG-TERM shares first. Those are taxed at 12.5% against 20% for
    short-term, so given a choice of which shares to deliver, delivering the
    long-term ones is strictly cheaper -- and a position accumulated over years
    almost always offers that choice. A framework that ignores the split leaves
    that saving on the table on every single trim.
    """
    t = cfg.get("tax", {})
    lt_rate = t.get("ltcg_rate_pct", 12.5)
    st_rate = t.get("stcg_rate_pct", 20.0)
    gain_ps = price - avg_cost
    total = gain_ps * qty

    from_lt = min(qty, max(lt_qty, 0.0))
    from_st = max(qty - from_lt, 0.0)

    if total <= 0:
        note = (f"a loss: no tax due, and it offsets gains elsewhere this year. "
                f"Delivering {from_lt:,.0f} long-term and {from_st:,.0f} "
                f"short-term shares")
        return TaxView(None, None, gain_ps, total, 0.0, 0.0, None, note,
                       from_lt, from_st, 0.0)

    tax = (gain_ps * from_lt * lt_rate + gain_ps * from_st * st_rate) / 100
    worst = total * st_rate / 100
    blended = 100 * tax / total if total else 0.0

    if from_st == 0:
        note = (f"all {from_lt:,.0f} shares delivered from the long-term pool "
                f"at {lt_rate:.1f}%")
    elif from_lt == 0:
        note = (f"only short-term shares available -- {from_st:,.0f} at "
                f"{st_rate:.1f}%")
    else:
        note = (f"{from_lt:,.0f} long-term at {lt_rate:.1f}% + {from_st:,.0f} "
                f"short-term at {st_rate:.1f}% (blended {blended:.1f}%)")
    return TaxView(None, from_st == 0, gain_ps, total, tax, blended, None,
                   note, from_lt, from_st, max(worst - tax, 0.0))


def assess(price: float, avg_cost: float, qty: float, cfg: dict,
           purchase_date: str | None = None,
           today: dt.date | None = None,
           lt_qty: float | None = None,
           st_qty: float | None = None) -> TaxView:
    """Gain and estimated tax on selling `qty` shares.

    Prefers an explicit long/short split when the broker statement provides it;
    falls back to a single purchase date, then to assuming the worse rate.
    """
    if lt_qty is not None or st_qty is not None:
        return assess_split(price, avg_cost, qty, cfg,
                            lt_qty or 0.0, st_qty or 0.0)
    t = cfg.get("tax", {})
    threshold = t.get("ltcg_threshold_days", 365)
    gain_ps = price - avg_cost
    total = gain_ps * qty
    days = holding_period(purchase_date, today)

    if days is None:
        # Unknown holding period. Assume the worse rate rather than the better
        # one -- an underestimate of tax is the error that costs money.
        rate = t.get("stcg_rate_pct", 20.0)
        note = ("purchase date unknown, so short-term rate assumed. Add "
                "purchase_date in config/holdings.csv for a real figure")
        return TaxView(None, None, gain_ps, total,
                       max(total, 0) * rate / 100, rate, None, note)

    long_term = days > threshold
    rate = (t.get("ltcg_rate_pct", 12.5) if long_term
            else t.get("stcg_rate_pct", 20.0))
    to_ltcg = None if long_term else threshold - days + 1

    if total <= 0:
        note = ("a loss: no tax due, and it can offset gains elsewhere this "
                "year")
    elif long_term:
        note = (f"long term ({days} days) -- taxed at {rate:.1f}% above the "
                f"{t.get('ltcg_exemption', 125000):,.0f} annual exemption")
    elif to_ltcg is not None and to_ltcg <= t.get("near_ltcg_days", 45):
        note = (f"SHORT TERM but only {to_ltcg} days from long term. Waiting "
                f"cuts the rate from {t.get('stcg_rate_pct', 20.0):.0f}% to "
                f"{t.get('ltcg_rate_pct', 12.5):.1f}%")
    else:
        note = f"short term ({days} days) -- taxed at {rate:.1f}%"

    return TaxView(days, long_term, gain_ps, total,
                   max(total, 0) * rate / 100, rate, to_ltcg, note)


def trim_timing(tax: TaxView, price: float, sma50: float | None,
                sma20: float | None, drift_pct: float, cfg: dict) -> tuple[str, list[str]]:
    """Return (timing, reasons) for a trim. Never for an exit."""
    tr = cfg.get("trimming", {})
    t = cfg.get("tax", {})
    reasons: list[str] = []

    # 0. If the whole trim can be delivered from the long-term pool there is
    #    nothing to wait for -- the lower rate already applies.
    if tax.lt_shares_sold and not tax.st_shares_sold:
        reasons.append("entirely from the long-term pool, so the lower rate "
                       "already applies -- no tax reason to delay")

    # 1. Nearly long-term dominates everything else: waiting a few weeks to cut
    #    the tax rate by a third is worth more than a marginally faster
    #    rebalance, and the position is not broken.
    if (tax.days_to_ltcg is not None and tax.total_gain > 0
            and tax.days_to_ltcg <= t.get("near_ltcg_days", 45)):
        saving = tax.total_gain * (t.get("stcg_rate_pct", 20.0)
                                   - t.get("ltcg_rate_pct", 12.5)) / 100
        reasons.append(f"WAIT {tax.days_to_ltcg} days for long-term treatment "
                       f"-- saves about {saving:,.0f} in tax")
        return "wait_for_ltcg", reasons

    # 2. Severe concentration outranks a better entry price. A position at
    #    several times its cap is most dangerous precisely when it is falling,
    #    which is exactly when the "wait for strength" rule would hold it.
    if drift_pct > 100:
        reasons.append("more than double the cap -- concentration this far out "
                       "is worth correcting now, not at a better print")
        return "now", reasons

    # 3. Otherwise, do not trim into weakness. A drift correction can afford to
    #    be patient about the price; an exit cannot.
    if tr.get("prefer_strength", True) and sma20 and price < sma20:
        reasons.append(f"price {price:.2f} is below its 20 DMA ({sma20:.2f}) -- "
                       f"no need to trim into weakness; this is a sizing "
                       f"correction, not an escape")
        return "wait_for_strength", reasons

    reasons.append("no tax or timing reason to wait")
    return "now", reasons


def tranche_plan(qty: float, price: float, cfg: dict) -> list[float]:
    """Split a trim into pieces.

    One large market order moves the price against you and forces the whole
    decision onto a single day's print. Below the minimum value the brokerage
    and spread on each slice outweigh that, so it stays a single order.
    """
    tr = cfg.get("trimming", {})
    n = max(int(tr.get("tranches", 3)), 1)
    if qty * price < tr.get("tranche_min_value", 15000) or n == 1:
        return [qty]
    base = int(qty // n)
    if base <= 0:
        return [qty]
    parts = [float(base)] * n
    parts[-1] += qty - base * n
    return parts
