"""Tax lots for one holding, rebuilt from broker tradebooks.

A tradebook starts on some date; anything you held before then is an
OPENING lot. Its size is whatever makes the replay end at the quantity your
holdings file shows today, and its cost whatever makes the total cost match
your average cost. FIFO means sales during the tradebook period consumed the
opening shares first.

Corporate actions from market.db:
  split   every lot held before the ex-date: quantity × factor, cost ÷ factor,
          date kept
  bonus   a new lot of quantity × (factor − 1) at nil cost, dated the
          ex-date (cost of bonus shares is nil; holding runs from allotment)

Reconciliation against the broker's long/short split: undated opening shares
are long-term unless the broker counts more short-term shares than the dated
lots explain (an IPO allotment, a transfer in, a demerger), in which case
that many opening shares are marked short-term with the date unknown.

Pure: no I/O.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from strategy.capgains import Lot, Sale, sell_fifo


@dataclass
class Trade:
    symbol: str
    date: dt.date
    side: str            # buy | sell
    qty: float
    price: float
    trade_id: str = ""
    isin: str = ""


@dataclass
class LotBook:
    symbol: str
    lots: list[Lot]
    sales: list[Sale]                     # sales replayed from the tradebook
    status: str                           # matched | opening | fallback | no_trades
    notes: list[str] = field(default_factory=list)
    opening_qty: float = 0.0
    lt_qty_today: float = 0.0
    st_qty_today: float = 0.0

    @property
    def qty(self) -> float:
        return sum(l.qty for l in self.lots)

    @property
    def cost(self) -> float:
        return sum(l.qty * l.cost for l in self.lots)


def _replay(symbol: str, opening: float, opening_cost: float, trades: list[Trade],
            actions: list[tuple[dt.date, float, str]], cfg: dict,
            record: bool = False) -> tuple[list[Lot], list[Sale], float]:
    """Replay trades and actions in date order. Returns lots left, sales, and
    shares sold that no lot covered."""
    lots = [Lot(symbol, opening, opening_cost, None, "opening", True)] if opening > 0 else []
    sales, short = [], 0.0
    ev = ([(t.date, 1, t) for t in trades]
          + [(d, 0, (d, f, k)) for d, f, k in actions])    # actions before same-day trades
    for d, kind, x in sorted(ev, key=lambda e: (e[0], e[1])):
        if kind == 0:
            _, f, k = x
            if k == "bonus":
                extra = sum(l.qty for l in lots) * (f - 1)
                if extra > 0:
                    lots.append(Lot(symbol, extra, 0.0, d, "bonus"))
            else:
                for l in lots:
                    l.qty *= f
                    l.cost /= f
            continue
        t = x
        if t.side == "buy":
            lots.append(Lot(symbol, t.qty, t.price, t.date, "tradebook"))
        else:
            s, lots = sell_fifo(lots, t.qty, t.price, t.date, cfg, consume=True)
            short += s.unmatched_qty
            if record:
                sales.append(s)
    return lots, sales, short


def build(symbol: str, trades: list[Trade], actions: list[tuple[dt.date, float, str]],
          qty: float, avg_cost: float | None, lt_qty: float | None, st_qty: float | None,
          today: dt.date, cfg: dict) -> LotBook:
    days = cfg["long_term_after_days"]
    trades = [t for t in trades if t.symbol == symbol]
    acts = [a for a in actions if not trades or a[0] >= min(t.date for t in trades)] \
        if trades else []
    if not trades:
        return _fallback(symbol, qty, avg_cost, lt_qty, st_qty, today, days, "no_trades",
                         "not in the tradebooks: lots from the broker's long/short split")

    # Opening size: the replayed final quantity rises with it; bisect.
    def final(o: float) -> float:
        lots, _, short = _replay(symbol, o, 0.0, trades, acts, cfg)
        return sum(l.qty for l in lots) - short
    lo, hi = 0.0, max(qty, 1.0)
    while final(hi) < qty and hi < 1e9:
        hi *= 2
    if final(lo) > qty + 0.5:
        return _fallback(symbol, qty, avg_cost, lt_qty, st_qty, today, days, "fallback",
                         f"tradebooks show more shares than you hold ({final(0):g} vs "
                         f"{qty:g}): a sale is missing; lots from the broker split")
    for _ in range(80):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if final(mid) < qty else (lo, mid)
    opening = round(hi, 3)
    if opening < 0.01:
        opening = 0.0

    lots, sales, _ = _replay(symbol, opening, 0.0, trades, acts, cfg, record=True)
    notes = []
    if opening:
        # Opening cost: what makes total cost match quantity × average cost.
        o_lots = [l for l in lots if l.source == "opening"]
        known = sum(l.qty * l.cost for l in lots if l.source != "opening")
        o_qty = sum(l.qty for l in o_lots)
        c = ((qty * avg_cost - known) / o_qty) if (avg_cost and o_qty) else None
        if c is None or c < 0 or (avg_cost and c > 5 * avg_cost):
            c = avg_cost or 0.0
            notes.append("opening cost could not be reconciled with the average cost: "
                         "the average is used")
        lots, sales, _ = _replay(symbol, opening, c, trades, acts, cfg, record=True)
        notes.append(f"{opening:g} shares held before the first tradebook "
                     f"({min(t.date for t in trades)}): cost derived from your average")
    lt_now = sum(l.qty for l in lots if l.is_long(today, days))
    if lt_qty is not None and lt_now - lt_qty > 0.5:
        # The broker sees more short-term shares than the dated lots explain.
        move = lt_now - lt_qty
        for l in lots:
            if move <= 0:
                break
            if l.date is None and l.long_term:
                if l.qty <= move + 1e-9:
                    l.long_term = False
                    move -= l.qty
                else:
                    lots.append(Lot(symbol, move, l.cost, None, l.source, False))
                    l.qty -= move
                    move = 0
        lots.sort(key=lambda l: (l.date is not None, not l.long_term, l.date or dt.date.min))
        # Opening shares the broker shows as recent were likely recent when
        # sold, too: count those sales short-term.
        for sale in sales:
            for pc in sale.pieces:
                if pc["source"] == "opening" and pc["long_term"]:
                    pc["long_term"] = False
                    sale.lt_gain -= pc["gain"]
                    sale.st_gain += pc["gain"]
        if move > 0.5:
            notes.append(f"broker counts {lt_qty:g} long-term shares; the tradebooks "
                         f"date {lt_now - move:g}: check for a missing trade")
        else:
            notes.append("some undated shares are short-term per the broker (an IPO, "
                         "transfer or demerger): date unknown, treated as short-term")
    for l in lots:
        if l.date is None and l.long_term is None:
            l.long_term = True
    lt_now = sum(l.qty for l in lots if l.is_long(today, days))
    if lt_qty is not None and abs(lt_now - lt_qty) > 0.5:
        notes.append(f"long-term shares: lots {lt_now:g}, broker {lt_qty:g}")
    status = "opening" if opening else "matched"
    return LotBook(symbol, lots, sales, status, notes, opening, lt_now,
                   sum(l.qty for l in lots) - lt_now)


def _fallback(symbol, qty, avg_cost, lt_qty, st_qty, today, days, status, note) -> LotBook:
    """No usable trades: two undated lots from the broker's long/short split
    (long-term first, as FIFO would deliver them)."""
    lt = lt_qty if lt_qty is not None else 0.0
    st = st_qty if st_qty is not None else max(qty - lt, 0.0)
    if lt_qty is None and st_qty is None:
        lt, st = 0.0, qty
        note += "; no split on file, so all short-term (the cautious case)"
    lots = [Lot(symbol, lt, avg_cost or 0.0, None, "opening", True)] if lt > 0 else []
    if st > 0:
        lots.append(Lot(symbol, st, avg_cost or 0.0, None, "opening", False))
    return LotBook(symbol, lots, [], status, [note], qty, lt, st)
