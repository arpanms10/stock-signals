"""The book at a glance: value, P/L, sleeves, Core-Satellite, caps, sectors.

Pure and deterministic: positions and prices in, numbers out, every one in
Decimal so rupees add up to the paisa. Nothing here fetches, ranks or
recommends; crossing a guideline produces a review note, not an action.

What counts where:

  sleeves     operating equity | listed real assets (REIT, InvIT) | ETFs and
              funds | bonds and gold bonds | other. Weights are of the whole
              priced book.
  70:30       operating equity only. Your bucket is the role: core is core;
              satellite, trade and blank are satellite.
  sectors     operating equity only, as % of operating equity.
  positions   % of the whole priced book, against the tightest guideline
              that applies (core 8%, satellite 4%, small-cap 2%).

A holding with no price is not valued at zero and not valued at cost: it is
left out of the weights and listed, with its cost, under "not valued".
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml

from book import securities as sec

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "core_satellite.yaml"
ZERO = Decimal(0)
PAISA, BP = Decimal("0.01"), Decimal("0.01")


def load_config(path: Path | str = CONFIG_PATH) -> dict:
    with open(path) as fh:
        return yaml.safe_load(fh)


def money(v: Decimal | None) -> Decimal | None:
    return None if v is None else v.quantize(PAISA, ROUND_HALF_UP)


def pct(part: Decimal, whole: Decimal) -> Decimal | None:
    if not whole:
        return None
    return (Decimal(100) * part / whole).quantize(BP, ROUND_HALF_UP)


def role_of(bucket: str) -> str:
    return "core" if (bucket or "").strip().lower() == "core" else "satellite"


@dataclass
class Position:
    symbol: str
    kind: str
    quantity: Decimal
    avg_cost: Decimal | None = None
    price: Decimal | None = None
    prev_close: Decimal | None = None
    price_date: str | None = None
    price_source: str = ""
    price_note: str = ""
    bucket: str = ""
    sector: str = ""
    cap_band: str = ""
    name: str = ""
    review: str = ""
    market_cap_cr: Decimal | None = None   # from research.db (Phase 2), if refreshed
    data_state: str = ""                   # fresh | stale | none
    # filled by build()
    market_value: Decimal | None = None
    invested: Decimal | None = None
    pnl: Decimal | None = None
    pnl_pct: Decimal | None = None
    weight_pct: Decimal | None = None
    limit_pct: Decimal | None = None
    over_limit: bool = False

    @property
    def sleeve(self) -> str:
        return sec.SLEEVE_OF[self.kind]

    @property
    def role(self) -> str:
        return role_of(self.bucket) if self.sleeve == sec.OPERATING else ""


@dataclass
class Overview:
    positions: list[Position]
    total_value: Decimal
    invested: Decimal
    priced_cost: Decimal
    unrealised: Decimal
    return_pct: Decimal | None
    day_change: Decimal | None
    price_date: str | None
    sleeves: list[dict]
    core_satellite: dict
    caps: list[dict]
    sectors: list[dict]
    top5_pct: Decimal | None
    top10_pct: Decimal | None
    reviews: list[str] = field(default_factory=list)
    disclosures: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        """What a snapshot keeps besides the positions."""
        return {"total_value": str(self.total_value), "invested": str(self.invested),
                "unrealised": str(self.unrealised),
                "return_pct": None if self.return_pct is None else str(self.return_pct),
                "sleeves": self.sleeves, "core_satellite": self.core_satellite,
                "caps": self.caps, "sectors": self.sectors,
                "top5_pct": str(self.top5_pct), "top10_pct": str(self.top10_pct),
                "reviews": self.reviews, "disclosures": self.disclosures}


def _limit(p: Position, cfg: dict) -> Decimal | None:
    if p.sleeve != sec.OPERATING:
        return None
    caps = cfg["position_max_pct"]
    lim = Decimal(str(caps[p.role]))
    if p.cap_band == "small":
        lim = min(lim, Decimal(str(caps["small_cap"])))
    return lim


def _split(values: dict[str, Decimal], whole: Decimal) -> list[dict]:
    return [{"key": k, "value": money(v), "pct": pct(v, whole)}
            for k, v in sorted(values.items(), key=lambda kv: -kv[1])]


def build(positions: list[Position], cfg: dict | None = None) -> Overview:
    cfg = cfg or load_config()
    reviews, disclosures = [], []

    for p in positions:
        if p.avg_cost:
            p.invested = money(p.avg_cost * p.quantity)
        if p.price is not None:
            p.market_value = money(p.price * p.quantity)
            if p.invested:
                p.pnl = p.market_value - p.invested
                p.pnl_pct = pct(p.pnl, p.invested)
    priced = [p for p in positions if p.market_value is not None]
    total = sum((p.market_value for p in priced), ZERO)
    invested = sum((p.invested for p in positions if p.invested), ZERO)
    both = [p for p in priced if p.invested]
    priced_cost = sum((p.invested for p in both), ZERO)
    unrealised = sum((p.pnl for p in both), ZERO)
    with_prev = [p for p in priced if p.prev_close is not None]
    day = (money(sum((p.quantity * (p.price - p.prev_close) for p in with_prev), ZERO))
           if with_prev else None)
    dates = sorted({p.price_date for p in priced if p.price_date})

    for p in priced:
        p.weight_pct = pct(p.market_value, total)
        p.limit_pct = _limit(p, cfg)
        if p.limit_pct is not None and p.weight_pct is not None \
                and p.weight_pct > p.limit_pct:
            p.over_limit = True
            why = (f"{p.role}" + ("" if p.bucket else ", by default: bucket not set")
                   + (", small cap" if p.cap_band == "small" else ""))
            reviews.append(f"{p.symbol} is {p.weight_pct}% of the book, over the "
                           f"{p.limit_pct}% guideline ({why}).")

    # ------------------------------------------------------------ sleeves
    by_sleeve: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for p in priced:
        by_sleeve[p.sleeve] += p.market_value
    sleeves = [{"key": s, "label": sec.SLEEVE_LABEL[s], "value": money(v),
                "pct": pct(v, total)} for s, v in
               sorted(by_sleeve.items(), key=lambda kv: -kv[1])]

    # ------------------------------------------------------- core / sat
    op = [p for p in priced if p.sleeve == sec.OPERATING]
    op_total = sum((p.market_value for p in op), ZERO)
    core_v = sum((p.market_value for p in op if p.role == "core"), ZERO)
    trade_v = sum((p.market_value for p in op if p.bucket == "trade"), ZERO)
    unset = [p.symbol for p in positions
             if p.sleeve == sec.OPERATING and not p.bucket]
    t, d = cfg["targets"], cfg["drift"]
    core_pct, sat_pct = pct(core_v, op_total), pct(op_total - core_v, op_total)
    cs = {"operating_value": money(op_total), "core_value": money(core_v),
          "satellite_value": money(op_total - core_v), "trade_value": money(trade_v),
          "core_pct": core_pct, "satellite_pct": sat_pct,
          "target_core_pct": Decimal(str(t["core_pct"])),
          "target_satellite_pct": Decimal(str(t["satellite_pct"])),
          "unset": unset, "triggers": []}
    if core_pct is not None:
        if core_pct < d["core_low_pct"]:
            cs["triggers"].append(f"Core is {core_pct}%, below {d['core_low_pct']}%: "
                                  "investigate the core underweight.")
        if core_pct > d["core_high_pct"]:
            cs["triggers"].append(f"Core is {core_pct}%, above {d['core_high_pct']}%: "
                                  "consider sending new money to satellite.")
        if sat_pct > d["satellite_high_pct"]:
            cs["triggers"].append(f"Satellite is {sat_pct}%, above "
                                  f"{d['satellite_high_pct']}%: investigate the "
                                  "satellite overweight.")
        if sat_pct < d["satellite_low_pct"]:
            cs["triggers"].append(f"Satellite is {sat_pct}%, below "
                                  f"{d['satellite_low_pct']}%: are there quality "
                                  "growth opportunities?")
    reviews = cs["triggers"] + reviews
    if unset:
        disclosures.append(
            f"{len(unset)} operating holding(s) have no bucket set and count as "
            "satellite, the default. Set bucket = core in your holdings file for "
            "the ones you mean to own for a decade or more.")

    # ------------------------------------------------- caps and sectors
    caps_v: dict[str, Decimal] = defaultdict(lambda: ZERO)
    sect_v: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for p in op:
        caps_v[p.cap_band or "unknown"] += p.market_value
        sect_v[p.sector or "Unclassified"] += p.market_value
    caps = _split(caps_v, op_total)
    sectors = _split(sect_v, op_total)
    smax = Decimal(str(cfg["sector_max_pct"]))
    for s in sectors:
        s["over_limit"] = s["pct"] is not None and s["pct"] > smax \
            and s["key"] != "Unclassified"
        if s["over_limit"]:
            reviews.append(f"{s['key']} is {s['pct']}% of operating equity, over the "
                           f"{smax}% sector guideline.")
    unclassified = [p.symbol for p in op if not p.sector]
    if unclassified:
        disclosures.append("No NSE industry on file (outside the Nifty 500) for: "
                           + ", ".join(unclassified) + ".")

    # ---------------------------------------------------- concentration
    ranked = sorted(priced, key=lambda p: -p.market_value)
    top5 = pct(sum((p.market_value for p in ranked[:5]), ZERO), total)
    top10 = pct(sum((p.market_value for p in ranked[:10]), ZERO), total)

    # ------------------------------------------------------- disclosures
    for p in positions:
        if p.market_value is None:
            cost = f" (cost ₹{p.invested:,})" if p.invested else ""
            disclosures.append(f"{p.symbol} not valued{cost}: {p.price_note or 'no price'}.")
        elif p.price_note:
            disclosures.append(f"{p.symbol}: {p.price_note}")
        if not p.avg_cost:
            disclosures.append(f"{p.symbol}: no average cost, so no P/L.")
        if p.review:
            disclosures.append(f"{p.symbol}: verify the security type ({p.review}).")
    if len(dates) > 1:
        disclosures.append(f"Prices are from more than one session ({dates[0]} to "
                           f"{dates[-1]}).")

    return Overview(
        positions=positions, total_value=money(total), invested=money(invested),
        priced_cost=money(priced_cost), unrealised=money(unrealised),
        return_pct=pct(unrealised, priced_cost), day_change=day,
        price_date=dates[-1] if dates else None, sleeves=sleeves,
        core_satellite=cs, caps=caps, sectors=sectors, top5_pct=top5,
        top10_pct=top10, reviews=reviews, disclosures=disclosures)
