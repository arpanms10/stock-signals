"""Capital gains on listed equity: lots, FIFO, the financial year's tax.

  lots       each purchase is a lot with a date and a cost per share. Bonus
             shares are their own lot: cost nil, dated the allotment (s.55).
             A split divides the cost and keeps the date
  FIFO       demat shares are sold first-in first-out (CBDT circular 768),
             so which shares a sale delivers is not a choice -- only WHEN
  FY tax     short-term losses set off against short- then long-term gains,
             long-term losses only against long-term gains; the annual LTCG
             exemption applies to what's left; then cess and surcharge

Rates live in config/tax.yaml, dated, because Budgets change them. Pure: no I/O.
Estimates, not tax advice.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "tax.yaml"


def load_config(path: Path | str = CONFIG) -> dict:
    import yaml
    return yaml.safe_load(Path(path).read_text())


@dataclass
class Lot:
    symbol: str
    qty: float
    cost: float                       # ₹ per share
    date: dt.date | None              # None: before the tradebooks, date unknown
    source: str = "tradebook"         # tradebook | opening | bonus
    long_term: bool | None = None     # for undated lots: what the broker split says

    def is_long(self, on: dt.date, days: int) -> bool:
        if self.date is None:
            return bool(self.long_term)
        return (on - self.date).days > days

    def turns_long(self, days: int) -> dt.date | None:
        """First day a sale counts as long-term."""
        return None if self.date is None else self.date + dt.timedelta(days=days + 1)


@dataclass
class Sale:
    """One sale matched FIFO against lots."""
    symbol: str
    date: dt.date
    qty: float
    price: float
    st_gain: float = 0.0
    lt_gain: float = 0.0
    unmatched_qty: float = 0.0        # shares no lot covers (cost unknown)
    pieces: list[dict] = field(default_factory=list)

    @property
    def gain(self) -> float:
        return self.st_gain + self.lt_gain


def fy_of(d: dt.date) -> str:
    y = d.year if d.month >= 4 else d.year - 1
    return f"{y}-{(y + 1) % 100:02d}"


def fy_start(fy: str) -> dt.date:
    return dt.date(int(fy[:4]), 4, 1)


def regime(d: dt.date, cfg: dict) -> dict:
    for r in sorted(cfg["regimes"], key=lambda r: r["from"], reverse=True):
        if d >= dt.date.fromisoformat(r["from"]):
            return r
    return cfg["regimes"][-1]


def sell_fifo(lots: list[Lot], qty: float, price: float, on: dt.date, cfg: dict,
              consume: bool = False) -> tuple[Sale, list[Lot]]:
    """Match a sale of `qty` against `lots` (oldest first). Returns the sale
    and the lots left. With consume=False the input is not changed."""
    days = cfg["long_term_after_days"]
    left = [replace(l) for l in lots] if not consume else lots
    s = Sale(lots[0].symbol if lots else "", on, qty, price)
    need = qty
    for l in left:
        if need <= 1e-9:
            break
        take = min(l.qty, need)
        if take <= 0:
            continue
        g = (price - l.cost) * take
        lt = l.is_long(on, days)
        if lt:
            s.lt_gain += g
        else:
            s.st_gain += g
        s.pieces.append({"qty": take, "cost": l.cost, "date": l.date, "source": l.source,
                         "long_term": lt, "gain": g})
        l.qty -= take
        need -= take
    s.unmatched_qty = max(need, 0.0)
    return s, [l for l in left if l.qty > 1e-9]


def fy_tax(st: float, lt: float, d: dt.date, cfg: dict) -> dict:
    """Tax on a financial year's net short- and long-term gains (losses
    negative), under the regime in force on `d` (the year's last sale)."""
    r = regime(d, cfg)
    stg, ltg = max(st, 0.0), max(lt, 0.0)
    stl, ltl = max(-st, 0.0), max(-lt, 0.0)
    # short-term loss: first against short-term gains, then long-term
    use = min(stl, stg); stg -= use; stl -= use
    use = min(stl, ltg); ltg -= use; stl -= use
    use = min(ltl, ltg); ltg -= use; ltl -= use
    exempt = min(ltg, r["ltcg_exemption"])
    base = stg * r["stcg_pct"] / 100 + (ltg - exempt) * r["ltcg_pct"] / 100
    mult = (1 + cfg.get("surcharge_pct", 0) / 100) * (1 + cfg.get("cess_pct", 0) / 100)
    return {"st_taxable": stg, "lt_taxable": ltg - exempt, "exemption_used": exempt,
            "exemption_left": r["ltcg_exemption"] - exempt,
            "carry_st_loss": stl, "carry_lt_loss": ltl, "tax": base * mult,
            "stcg_pct": r["stcg_pct"], "ltcg_pct": r["ltcg_pct"]}


def sale_costs(value: float, cfg: dict, scrips: int = 1) -> float:
    """Costs of delivery sales worth `value` across `scrips` stocks, one day each."""
    c = cfg["costs"]
    exch = value * c["exchange_txn_pct"] / 100
    sebi = value * c["sebi_per_crore"] / 1e7
    dp = c["dp_per_scrip_day"] * scrips
    brk = c["brokerage_per_order"] * scrips
    return (value * c["stt_delivery_pct"] / 100 + exch + sebi + dp + brk
            + (exch + sebi + dp + brk) * c["gst_pct"] / 100)


def buy_costs(value: float, cfg: dict) -> float:
    c = cfg["costs"]
    exch = value * c["exchange_txn_pct"] / 100
    sebi = value * c["sebi_per_crore"] / 1e7
    return (value * c["stt_delivery_pct"] / 100 + exch + sebi + c["brokerage_per_order"]
            + value * c["stamp_buy_pct"] / 100
            + (exch + sebi + c["brokerage_per_order"]) * c["gst_pct"] / 100)
