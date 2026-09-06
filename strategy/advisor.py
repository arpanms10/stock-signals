"""Buy / add / trim / exit suggestions for a real book.

The central question you asked -- partial or whole -- has a principled answer,
and the engine is built around it:

    TRIM (partial)  The position is too BIG, not bad. Sizing problem.
                    -> overweight vs target, sector over its cap, or a
                       satellite whose rank has slipped but is still eligible.
                    Sell down to the target and keep owning it.

    EXIT (whole)    The REASON to own it is gone. Thesis problem.
                    -> quality below the floor, a hard fundamental red flag,
                       or a satellite that has fallen out of the ranking
                       entirely.
                    Sell all of it; there is no size at which a broken thesis
                    is the right size.

Conflating the two is how people average down into failures and take profits
on their winners -- exactly backwards.

Core holdings are deliberately immune to rank-based exits. A core name falling
out of the momentum ranking is not a sell signal; only the business
deteriorating is. That distinction is why v1 turned an 85% rise in TCS into
1.3% -- it treated a compounder as a trade.

Everything here is a suggestion with its reasoning attached. Nothing is placed.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# Fundamental facts that end a thesis regardless of price action.
#
# Phrasing matters here. An earlier version matched "CFO/PAT" and so treated
# "cash quality not scored: no annual profit on file" -- a note about OUR data
# coverage -- as a red flag, and told the user to liquidate a holding because
# we were missing a number. A gap in our data is never evidence about their
# company.
HARD_FLAGS = (
    "pledge RISING",
    "HIGH. Forced selling",
    "interest cover",                     # only emitted with a real value
    "reported profit is not arriving as cash",
    "promoter holding fell",
)
# Anything matching these is a limitation of our data, never a finding.
DATA_GAP_MARKERS = ("not scored", "no fundamentals", "cannot be scored",
                    "score suppressed", "not available")


@dataclass
class Advice:
    symbol: str
    bucket: str
    action: str = "HOLD"         # BUY | ADD | HOLD | TRIM | EXIT
    qty: float = 0.0             # shares to transact (0 for HOLD)
    value: float = 0.0
    current_pct: float = 0.0
    target_pct: float = 0.0
    pnl_pct: float = 0.0
    reasons: list[str] = field(default_factory=list)
    urgency: str = "normal"      # normal | review | urgent
    timing: str = "now"          # now | wait_for_ltcg | wait_for_strength
    tranches: list[float] = field(default_factory=list)
    tax_note: str = ""
    tax_saved: float = 0.0
    estimated_tax: float = 0.0
    realised_gain: float = 0.0

    @property
    def is_sell(self) -> bool:
        return self.action in ("TRIM", "EXIT")


def _hard_flag(flags: list[str]) -> str | None:
    for f in flags or []:
        if any(g in f for g in DATA_GAP_MARKERS):
            continue                       # our gap, not their problem
        if any(h in f for h in HARD_FLAGS):
            return f
    return None


def _attach_plan(a: Advice, holding: dict, price: float, cfg: dict,
                 row: dict | None = None) -> Advice:
    """Tax view, timing and tranches for a sell.

    Applied to TRIM only. An exit leaves because the thesis is broken, and
    waiting three weeks for a better tax rate on a company with an interest
    cover of -1.2 is optimising the wrong thing.
    """
    from strategy import tax as tx

    if not a.qty:
        return a
    view = tx.assess(price, float(holding.get("average_price") or 0), a.qty,
                     cfg, holding.get("purchase_date"),
                     lt_qty=holding.get("lt_quantity"),
                     st_qty=holding.get("st_quantity"))
    a.tax_note = view.note
    a.estimated_tax = view.estimated_tax
    a.tax_saved = view.tax_saved_vs_worst
    a.realised_gain = view.total_gain

    if a.action != "TRIM":
        a.timing, a.tranches = "now", [a.qty]
        return a

    row = row or {}
    a.timing, why = tx.trim_timing(view, price, row.get("sma50"),
                                   row.get("sma20"),
                                   a.current_pct / max(a.target_pct, 1e-9) * 100 - 100,
                                   cfg)
    a.reasons.extend(why)
    a.tranches = tx.tranche_plan(a.qty, price, cfg)

    # The question people actually ask: should I sell at a loss?
    if view.total_gain < 0:
        a.reasons.append(
            f"this trim realises a loss of {abs(view.total_gain):,.0f}. Two "
            f"things are true at once: the position is oversized whatever it "
            f"cost you, and a realised loss can offset gains elsewhere this "
            f"year. Refusing to sell purely because it is down is the "
            f"disposition effect, not a strategy")
    return a


def advise(symbol: str, bucket: str, holding: dict, price: float,
           value: float, book_value: float, quality, rank: int | None,
           rank_universe: int, risk: dict, cfg: dict,
           sector_over_cap: bool = False, row: dict | None = None) -> Advice:
    """One holding, one recommendation."""
    r = cfg["risk"]
    m = cfg.get("momentum_strategy", {})
    qty = float(holding.get("quantity") or 0)
    avg = float(holding.get("average_price") or 0)
    cur_pct = 100 * value / book_value if book_value else 0.0
    pnl = 100 * (price / avg - 1) if avg else 0.0
    max_pct = r["max_position_pct"]

    a = Advice(symbol=symbol, bucket=bucket, value=value, current_pct=cur_pct,
               target_pct=max_pct, pnl_pct=pnl)

    qscore = quality.score if quality else None
    flags = quality.flags if quality else []
    hard = _hard_flag(flags)

    # ---- EXIT: the reason to own it is gone ---------------------------
    if hard:
        a.action, a.urgency = "EXIT", "urgent"
        a.qty = qty
        a.reasons.append(f"fundamental red flag: {hard}")
        a.reasons.append("a broken thesis has no correct position size")
        return _attach_plan(a, holding, price, cfg, row)

    floor = m.get("quality_floor", 50.0)
    if qscore is not None and qscore < floor:
        a.action, a.urgency = "EXIT", "review"
        a.qty = qty
        a.reasons.append(f"quality {qscore:.0f} is below the {floor:.0f} floor -- "
                         f"this is no longer a business you chose to own")
        return a

    # The exit threshold is configured against a full universe. Scale it to
    # however many names are actually ranked today, or a thin universe makes
    # every holding look like a laggard: rank 40 of 79 is mid-pack, while
    # rank 40 of 200 is genuinely trailing.
    exit_rank = m.get("exit_rank", 30)
    full = m.get("universe_size", 200)
    if rank_universe and rank_universe < full:
        exit_rank = max(int(exit_rank * rank_universe / full),
                        m.get("n_hold", 15) + 5)
    if bucket == "legacy" and rank is not None and rank > exit_rank:
        a.action, a.urgency = "HOLD", "review"
        a.reasons.append(f"rank {rank} of {rank_universe} would trigger a "
                         f"satellite exit, but this is unclassified")
        a.reasons.append("it was not bought on momentum, so it is not sold on "
                         "momentum -- set bucket=satellite in "
                         "config/holdings.csv if you want it rotated")
        return a
    if bucket == "satellite" and rank is not None and rank > exit_rank:
        a.action, a.urgency = "EXIT", "review"
        a.qty = qty
        a.reasons.append(f"satellite holding has fallen to rank {rank} of "
                         f"{rank_universe}, past the exit threshold of {exit_rank}")
        a.reasons.append("satellites are owned for the trend; the trend is over")
        return a

    if bucket == "legacy" and rank is None:
        a.action, a.urgency = "HOLD", "review"
        a.reasons.append("outside the momentum universe, but unclassified -- "
                         "no rank-based action taken")
        return a
    if bucket == "satellite" and rank is None:
        # Absence from the ranking has two very different causes: the stock is
        # genuinely in a downtrend, or we simply have no data for it. Only the
        # first is a reason to sell, and a quality score is what distinguishes
        # them -- so an unscored name gets a review prompt, not an exit.
        if qscore is None:
            a.action, a.urgency = "HOLD", "review"
            a.reasons.append("not in the momentum ranking and no quality score "
                             "on file -- cannot tell a downtrend from a data "
                             "gap, so this needs your judgement, not a trade")
            return a
        a.action, a.urgency = "EXIT", "review"
        a.qty = qty
        a.reasons.append("satellite holding is below its 200 DMA, so it is "
                         "outside the eligible universe -- the trend that "
                         "justified holding it has ended")
        return a

    # ---- TRIM: the position is too big, not bad -----------------------
    if cur_pct > max_pct * 1.2:
        excess_value = value - book_value * max_pct / 100
        a.action = "TRIM"
        # A TRIM must always leave a position behind. If the arithmetic ever
        # asks for the whole lot, that is a sizing calculation overshooting --
        # not a decision to exit -- and silently selling out on it would turn
        # "this is too big" into "sell everything".
        want = max(excess_value / price, 0) if price else 0
        a.qty = min(want, qty * 0.9)
        a.reasons.append(f"{cur_pct:.1f}% of the book, above the {max_pct:.0f}% "
                         f"single-stock cap -- sizing, not a view on the stock")
        if bucket == "core":
            a.reasons.append("core holding: trimming to size, still owned")
        return _attach_plan(a, holding, price, cfg, row)

    if sector_over_cap and bucket == "satellite":
        a.action = "TRIM"
        a.qty = max(qty * 0.33, 1)
        a.reasons.append("its sector is over the concentration cap; several "
                         "holdings here are one bet, not several")
        return _attach_plan(a, holding, price, cfg, row)

    # ---- ADD / HOLD ---------------------------------------------------
    if risk.get("stop_breached") and bucket == "satellite":
        a.action, a.urgency = "TRIM", "review"
        a.qty = max(qty * 0.5, 1)
        a.reasons.append("below its stop with the thesis still intact -- "
                         "halve it rather than guess")
        return _attach_plan(a, holding, price, cfg, row)

    underweight = cur_pct < max_pct * 0.5
    strong = rank is not None and rank <= m.get("enter_rank", 15)
    if underweight and (bucket == "core" or strong) and \
            (qscore is None or qscore >= floor):
        a.action = "ADD"
        want_value = book_value * max_pct / 100
        a.qty = max((want_value - value) / price, 0) if price else 0
        a.reasons.append(f"only {cur_pct:.1f}% of the book against a "
                         f"{max_pct:.0f}% target")
        if strong:
            a.reasons.append(f"ranked {rank} of {rank_universe} on momentum")
        elif bucket == "core":
            a.reasons.append("core holding below its intended weight")
        return a

    a.action = "HOLD"
    if bucket == "core":
        a.reasons.append("core holding, business intact -- rank is irrelevant here")
    elif rank is not None:
        a.reasons.append(f"rank {rank} of {rank_universe}, still inside the band")
    if qscore is not None:
        a.reasons.append(f"quality {qscore:.0f}")
    return a


EXIT_ALARM_FRACTION = 0.35


def sanity_warnings(advices: list[Advice], ranked_universe: int,
                    expected_universe: int = 200) -> list[str]:
    """Catch the case where the tool, not the portfolio, is what is wrong.

    A first run against a book that was never built this way will always
    disagree with it. But if it wants to sell most of the portfolio at once,
    the likelier explanation is thin data or a mis-set threshold -- and acting
    on that would be expensive and irreversible.
    """
    out = []
    if not advices:
        return out
    exits = [a for a in advices if a.action == "EXIT"]
    frac = len(exits) / len(advices)
    if frac > EXIT_ALARM_FRACTION:
        out.append(
            f"{len(exits)} of {len(advices)} holdings are flagged EXIT "
            f"({100 * frac:.0f}%). Treat that as a question about the setup "
            f"before treating it as a plan: selling most of a book at once "
            f"costs real money in STT, spreads and tax, and cannot be undone.")
    if ranked_universe and ranked_universe < expected_universe * 0.4:
        out.append(
            f"only {ranked_universe} of {expected_universe} names are ranked. "
            f"Most of the gap is the 200-DMA filter doing its job -- names in "
            f"downtrends are excluded by design -- but a very low count also "
            f"means a broadly weak market, which is a poor moment to act on "
            f"relative rank.")
    unscored = [a for a in advices if "no quality score" in " ".join(a.reasons)]
    if len(unscored) > len(advices) * 0.25:
        out.append(
            f"{len(unscored)} holdings have no quality score, so they default "
            f"to satellite and rotate on rank alone. Filling "
            f"config/fundamentals.csv would change several of these.")
    return out


def summarise(advices: list[Advice]) -> dict:
    """Counts and cash impact, so the plan can be read at a glance."""
    out = {"EXIT": [], "TRIM": [], "ADD": [], "HOLD": [], "BUY": []}
    for a in advices:
        out.setdefault(a.action, []).append(a)
    raised = sum(a.qty * (a.value / a.qty if a.qty else 0)
                 for a in advices if a.is_sell and a.qty)
    return {"by_action": out,
            "urgent": [a for a in advices if a.urgency == "urgent"],
            "cash_raised_estimate": raised}
