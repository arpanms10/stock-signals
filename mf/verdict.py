"""Fund state and the action for it: EXIT / TRIM / ADD / WATCH / HOLD.

Two steps, kept apart so the backtest can test the first on its own:

  state()   judged on the fund alone, against its category:
            YOUNG, WEAK, SLIPPING, STRONG or OK
  decide()  the state plus your book (config/mf_holdings.csv or .xlsx): how much of
            the MF portfolio it is, whether the category or a near-twin is
            already overweight, and whether a sale now costs exit load / STCG

Weights are percent of the whole MF portfolio. Nothing here needs rupee
amounts, and nothing places an order.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "config" / "mf_rules.yaml"

YOUNG, WEAK, SLIPPING, STRONG, OK = "YOUNG", "WEAK", "SLIPPING", "STRONG", "OK"
EXIT, TRIM, ADD, WATCH, HOLD, AVOID, NEUTRAL, NOT_JUDGED = (
    "EXIT", "TRIM", "ADD", "WATCH", "HOLD", "AVOID", "NEUTRAL", "NOT JUDGED")


def load_rules(path: Path = RULES) -> dict:
    return yaml.safe_load(path.read_text())


def _v(row, col):
    x = row.get(col) if hasattr(row, "get") else None
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else x


def _pct(x) -> str:
    return f"{x * 100:+.1f} pts/yr"


def state(row, rules: dict) -> tuple[str, list[str]]:
    """(state, reasons) for one metrics.snapshot() row."""
    s = rules["states"]
    if (row.get("history_months") or 0) < rules["min_history_months"]:
        return YOUNG, [f"only {int(row.get('history_months') or 0)} months of history"]
    r3, r1 = _v(row, "rank_3y"), _v(row, "rank_1y")
    e3, e1 = _v(row, "excess_3y"), _v(row, "excess_1y")
    vi3 = _v(row, "vs_index_3y")
    cons, dc = _v(row, "consistency_3y"), _v(row, "downside_capture_3y")
    if r3 is None or r1 is None:
        return YOUNG, ["too few category peers to rank against"]

    w = s["weak"]
    if r3 <= w["rank_3y_max"] and r1 <= w["rank_1y_max"] and e3 is not None \
            and e3 <= w["excess_3y_max"]:
        return WEAK, [f"bottom {r3:.0%} of category over 3y ({_pct(e3)} vs median)",
                      f"no recovery: rank {r1:.0%} over the last year"]
    if vi3 is not None and vi3 <= w["vs_index_3y_max"] and r3 <= w["vs_index_rank_3y_max"]:
        return WEAK, [f"trails the category's index fund by {_pct(vi3)} over 3y",
                      f"and only mid-table or worse (rank {r3:.0%})"]

    sl = s["slipping"]
    if r1 <= sl["rank_1y_max"] or (e1 is not None and e1 <= sl["excess_1y_max"]):
        why = [f"last year: rank {r1:.0%}" + (f", {_pct(e1)} vs median" if e1 is not None else "")]
        why.append(f"3-year record still rank {r3:.0%}")
        return SLIPPING, why

    st = s["strong"]
    if r3 >= st["rank_3y_min"] and r1 >= st["rank_1y_min"] \
            and (cons is None or cons >= st["consistency_3y_min"]) \
            and (dc is None or dc <= st["downside_capture_3y_max"]):
        why = [f"rank {r3:.0%} over 3y ({_pct(e3)} vs median)" if e3 is not None
               else f"rank {r3:.0%} over 3y"]
        if cons is not None:
            why.append(f"beat the median in {cons:.0%} of rolling 1-year windows")
        return STRONG, why
    return OK, [f"rank {r3:.0%} over 3y, {r1:.0%} over 1y"]


def _sell_caution(held: pd.DataFrame, c) -> str | None:
    over1y = _held_over_1y(held.at[c, "held_over_1y"]) if "held_over_1y" in held.columns else None
    return {
        False: "held under a year: most equity funds charge ~1% exit load, and gains are "
               "short-term (taxed higher) -- weigh waiting for the 1-year mark",
        None: "check exit load and holding period (STCG under 1 year) before selling",
        True: None}[over1y]


@dataclass
class Decision:
    code: int | None
    name: str
    category: str | None
    weight: float | None        # % of the MF book, None if not held
    state: str
    action: str
    reasons: list[str] = field(default_factory=list)
    target: float | None = None  # suggested % of the MF book after acting
    cautions: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)


def _held_over_1y(v) -> bool | None:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    t = str(v).strip().lower()
    if t in {"yes", "y", "true", "1"}:
        return True
    if t in {"no", "n", "false", "0"}:
        return False
    return None


def decide(snap: pd.DataFrame, names: dict[int, str], rules: dict,
           holdings: pd.DataFrame | None = None, evaluate: list[int] | None = None,
           corr: pd.DataFrame | None = None) -> list[Decision]:
    """Actions for every held fund plus any other fund named in `evaluate`.

    snap      metrics.snapshot() at the newest month, indexed by code, covering
              at least the categories of everything held or named
    holdings  columns code, weight (% of MF book), held_over_1y (optional)
    """
    a = rules["allocation"]
    judged = set(rules["categories"])
    holdings = holdings if holdings is not None else pd.DataFrame(
        columns=["code", "weight", "held_over_1y"])
    held = holdings.set_index("code")
    codes = list(dict.fromkeys(list(held.index) + list(evaluate or [])))

    states = {}
    for c in codes:
        if c in snap.index and snap.at[c, "category"] in judged:
            states[c] = state(snap.loc[c], rules)

    def cat(c):
        return snap.at[c, "category"] if c in snap.index else None

    def alternatives(c):
        k = cat(c)
        if k is None:
            return []
        peers = snap[(snap["category"] == k) & (snap.index != c)]
        good = [p for p in peers.index if state(peers.loc[p], rules)[0] == STRONG]
        good = sorted(good, key=lambda p: -(peers.at[p, "rank_3y"] or 0))[:3]
        out = [f"{names.get(p, p)} (rank {peers.at[p, 'rank_3y']:.0%} 3y)" for p in good]
        idx = rules.get("index_funds", {}).get(k)
        if idx:
            out.append(f"{names.get(idx, idx)} (index fund)")
        return out

    # Category totals and the weakest held fund per category, for caps.
    cat_w: dict[str, float] = {}
    for c in held.index:
        k = cat(c)
        if k:
            cat_w[k] = cat_w.get(k, 0.0) + float(held.at[c, "weight"])
    order = {WEAK: 0, SLIPPING: 1, YOUNG: 2, OK: 3, STRONG: 4}

    def strength(c):
        r3 = snap.at[c, "rank_3y"] if c in snap.index else None
        return (order.get(states.get(c, (OK,))[0], 3), r3 if pd.notna(r3) else 0.5)

    def weakest_held(k):
        return min((c for c in held.index if cat(c) == k), key=strength)

    # Near-duplicates among held funds: the weaker of each pair is trimmed.
    twins: dict[int, int] = {}
    if corr is not None:
        hc = [c for c in held.index if c in corr.index]
        for i, x in enumerate(hc):
            for y in hc[i + 1:]:
                r = corr.at[x, y]
                if pd.notna(r) and r >= a["near_duplicate_corr"]:
                    lo, hi = sorted((x, y), key=strength)
                    twins[lo] = hi

    out = []
    for c in codes:
        is_held = c in held.index
        w = float(held.at[c, "weight"]) if is_held else None
        k = cat(c)
        name = names.get(c, str(c))
        if c not in states:
            if k is None:
                why = ["no NAV history on file"]
            elif "index" in k:
                why = ["index fund: nothing to rank it against -- it is the yardstick "
                       "the active funds are measured on"]
            else:
                why = [f"{k} is not a category the engine ranks (config/mf_rules.yaml)"]
            d = Decision(c, name, k, w, "-", HOLD if is_held else NOT_JUDGED, why)
            # Allocation caps still apply: a 30% thematic fund is a sizing
            # question whether or not its returns are ranked.
            cap = a["category_max_pct"].get(k)
            if is_held and cap is not None and cat_w.get(k, 0) > cap and weakest_held(k) == c:
                d.action = TRIM
                d.target = max(0.0, w - (cat_w[k] - cap))
                d.reasons.append(f"{k} is {cat_w[k]:.0f}% of the book, cap {cap}%")
                if _sell_caution(held, c):
                    d.cautions.append(_sell_caution(held, c))
            out.append(d)
            continue
        st, why = states[c]
        d = Decision(c, name, k, w, st, HOLD, list(why))
        if not is_held:
            d.action = {STRONG: ADD, WEAK: AVOID, SLIPPING: WATCH,
                        YOUNG: WATCH, OK: NEUTRAL}[st]
            if st == STRONG:
                d.target = None
                d.reasons.append(f"room for up to {a['max_fund_pct']}% of the MF book")
            out.append(d)
            continue

        sell_caution = _sell_caution(held, c)
        cap = a["category_max_pct"].get(k)

        if st == WEAK:
            d.action, d.target = EXIT, 0.0
            d.alternatives = alternatives(c)
        elif cap is not None and cat_w.get(k, 0) > cap and weakest_held(k) == c:
            d.action = TRIM
            d.target = max(0.0, w - (cat_w[k] - cap))
            d.reasons.append(f"{k} is {cat_w[k]:.0f}% of the book, cap {cap}%")
        elif c in twins:
            d.action = TRIM
            d.target = round(w / 2, 1)
            other = twins[c]
            d.reasons.append(f"moves almost exactly with {names.get(other, other)} "
                             f"(36m correlation {corr.at[c, other]:.2f}) -- consolidate into the stronger")
        elif w > a["max_fund_pct"]:
            d.action, d.target = TRIM, float(a["max_fund_pct"])
            d.reasons.append(f"{w:.0f}% of the book, above the {a['max_fund_pct']}% single-fund cap")
        elif st in (SLIPPING, YOUNG):
            d.action = WATCH
        elif st == STRONG:
            room = a["max_fund_pct"] - w
            if cap is not None:
                room = min(room, cap - cat_w.get(k, 0))
            if room > 1:
                d.action, d.target = ADD, round(w + room, 1)
                d.reasons.append(f"room to {d.target:.0f}% of the book")
            else:
                d.reasons.append("already at its cap")
        if d.action in (EXIT, TRIM) and sell_caution:
            d.cautions.append(sell_caution)
        out.append(d)
    return out
