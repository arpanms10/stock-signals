"""Month-over-month changes in a fund's portfolio, read as manager decisions.

Three things make the raw month-to-month numbers lie, and each is undone here:

1. Weight moves with price. A stock going 3% -> 4% of the fund may be pure
   price drift. Share COUNT is what the manager controls, so every decision
   below is measured in shares.

2. Inflows. A fund taking in fresh money buys a slice of everything just to
   stay invested, so nearly every share count rises every month. The units
   outstanding grow by

       flow factor F = (net assets now / net assets before) / (NAV now / NAV before)

   and a holding that grew its shares by exactly F was merely kept in step.
   The *active* change is shares_now / (shares_before x F) - 1: positive means
   the manager added beyond what inflows required, negative means they let it
   shrink or sold.

3. Splits and bonuses. A 1:5 split multiplies the share count by five with no
   buying at all. These are found in the data itself: the per-share price
   implied by value / quantity drops by a clean ratio (1/2, 1/5, ...) while the
   share count rises by roughly the inverse. Kotak Bank's 5:1 split in Jan 2026
   is the one in this year's data; the face-value change also gave it a new
   ISIN, which is why holdings are matched on the first nine characters of the
   ISIN (issuer + security type), not the full code.

Arbitrage positions (stock long + futures short) are not stock picks -- they
are a cash substitute -- so they are tracked beside the picks, never mixed in.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from mf.ppfas import ARBITRAGE, FOREIGN, PICK_BUCKETS, REIT, SPECIAL, Disclosure

# Ratios a split or bonus can produce: 1:1 bonus = 2, 1:2 bonus = 1.5,
# Rs 10 -> Rs 2 split = 5, and so on.
SPLIT_RATIOS = (1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 10.0)
SPLIT_TOLERANCE = 0.15     # implied price ratio may wander this far from clean

# Active-change bands for labelling a month's move.
BIG = 0.10                 # +/-10% beyond inflows: a deliberate, large move
SMALL = 0.03               # within +/-3% of inflows: just kept in step
UNCHANGED = 0.0005         # share count within 0.05%: no trade at all

LAKH_PER_CRORE = 100.0


def stock_key(isin: str) -> str:
    """Stable identity across face-value changes: INE237A01028 and
    INE237A01036 are both Kotak equity."""
    isin = (isin or "").strip().upper()
    return isin[:9] if len(isin) == 12 and isin[:2] == "IN" else isin


def month_label(d: dt.date) -> str:
    return d.strftime("%b %Y")


def crore(lakh: float) -> float:
    return lakh / LAKH_PER_CRORE


# --- tables -------------------------------------------------------------

def holdings_table(discs: list[Disclosure]) -> pd.DataFrame:
    """All holdings from all months, one row per instrument per month."""
    h = pd.concat([d.holdings for d in discs], ignore_index=True)
    h["key"] = h["isin"].map(stock_key)
    h["price"] = np.where(h["quantity"].fillna(0) > 0,
                          h["value_lakh"] * 1e5 / h["quantity"], np.nan)
    return h


def fund_summary(discs: list[Disclosure]) -> pd.DataFrame:
    """One row per month: size, flows, and where the money sits."""
    rows = []
    prev = None
    for d in discs:
        b = d.holdings.groupby("bucket")["weight"].sum()
        picks = d.holdings[d.holdings["bucket"].isin(PICK_BUCKETS)]
        stocks = picks.groupby(picks["isin"].map(stock_key))["weight"].sum()
        row = {
            "month_end": d.month_end,
            "net_assets_cr": crore(d.net_assets),
            "nav_direct": d.nav_direct,
            "nav_return": (d.nav_direct / d.nav_direct_prev - 1)
            if d.nav_direct and d.nav_direct_prev else np.nan,
            "domestic_equity": b.get("core_equity", 0) + b.get(SPECIAL, 0)
            + b.get("unlisted", 0),
            "foreign_equity": b.get(FOREIGN, 0),
            "reits": b.get(REIT, 0),
            "arbitrage": b.get(ARBITRAGE, 0),
            "debt_money_market": b.get("money_market", 0),
            "cash_treps_liquid": b.get("treps", 0) + b.get("mf_units", 0)
            + d.net_receivables / d.net_assets,
            "n_stocks": int((stocks > 0).sum()),
            "n_domestic": int(picks[picks["bucket"] != FOREIGN]["isin"].map(stock_key).nunique()),
            "n_foreign": int(picks[picks["bucket"] == FOREIGN]["isin"].nunique()),
            "top10_weight": stocks.nlargest(10).sum(),
            "short_futures": -d.derivatives["value_lakh"].clip(upper=0).sum() / d.net_assets
            if len(d.derivatives) else 0.0,
            "turnover_incl_arb": d.turnover_incl_arb,
            "turnover_excl_arb": d.turnover_excl_arb,
        }
        row["total_equity"] = row["domestic_equity"] + row["foreign_equity"]
        row["cash_like_total"] = (row["arbitrage"] + row["debt_money_market"]
                                  + row["cash_treps_liquid"])
        if prev is not None and d.nav_direct and prev.nav_direct:
            nav_growth = d.nav_direct / prev.nav_direct
            row["flow_factor"] = (d.net_assets / prev.net_assets) / nav_growth
            row["net_inflow_cr"] = crore(d.net_assets - prev.net_assets * nav_growth)
        else:
            row["flow_factor"] = np.nan
            row["net_inflow_cr"] = np.nan
        rows.append(row)
        prev = d
    return pd.DataFrame(rows)


def _snap_ratio(r: float) -> float | None:
    best = min(SPLIT_RATIOS, key=lambda c: abs(r / c - 1))
    return best if abs(r / best - 1) <= SPLIT_TOLERANCE else None


def known_actions(signals_db: Path, equities_csv: Path) -> dict[str, list[tuple[dt.date, float]]]:
    """Split/bonus factors NSE published, keyed by stock key, from the price
    pipeline's corporate_actions table. Best effort: empty if either file is
    missing, and inference in detect_splits covers the rest."""
    try:
        eq = pd.read_csv(equities_csv, comment="#", usecols=["symbol", "isin"])
        with sqlite3.connect(signals_db) as con:
            ca = pd.read_sql("SELECT symbol, ex_date, factor FROM corporate_actions", con)
    except Exception:
        return {}
    ca = ca.merge(eq, on="symbol")
    out: dict[str, list[tuple[dt.date, float]]] = {}
    for r in ca.itertuples():
        out.setdefault(stock_key(r.isin), []).append(
            (dt.date.fromisoformat(r.ex_date), float(r.factor)))
    return out


def detect_splits(h: pd.DataFrame,
                  known: dict[str, list[tuple[dt.date, float]]] | None = None) -> pd.DataFrame:
    """Splits/bonuses between consecutive months, per stock key.

    A clean price ratio is a candidate; it also needs a new ISIN or a share
    count that moved by about the same ratio (a plain crash does not multiply
    share counts). When NSE's own record of the action is in `known`, its
    factor wins: a month's price move can leave the implied ratio between
    two clean ones (MCX's 5:1 split read as 4.4 after a +13% month)."""
    known = known or {}
    picks = h[h["bucket"].isin(PICK_BUCKETS)]
    g = picks.groupby(["key", "month_end"], as_index=False).agg(
        quantity=("quantity", "sum"), value_lakh=("value_lakh", "sum"),
        name=("name", "last"), isin=("isin", "last"))
    g["price"] = g["value_lakh"] * 1e5 / g["quantity"]
    months = sorted(h["month_end"].unique())
    nxt = dict(zip(months[:-1], months[1:]))
    out = []
    for key, s in g.groupby("key"):
        s = s.set_index("month_end")
        for m in s.index:
            n = nxt.get(m)
            if n is None or n not in s.index:
                continue
            pr = s.at[m, "price"] / s.at[n, "price"]
            qr = s.at[n, "quantity"] / s.at[m, "quantity"]
            k = _snap_ratio(pr) if pr > 1 + SPLIT_TOLERANCE else None
            # A face-value split changes the ISIN; a bonus does not. With a new
            # ISIN the clean price ratio is enough -- the share count can say
            # anything, because the fund may trade in the same month (MCX:
            # 5:1 split in Jan 2026 AND ~80% sold, so the count looked flat).
            # Without it the share count must also have moved by about k:
            # Mahanagar Gas fell 24% in Mar 2026 while a starter stake grew
            # 11x, which is buying into a falling price, not a 1:2 bonus.
            isin_changed = s.at[m, "isin"] != s.at[n, "isin"]
            nse = [f for d, f in known.get(key, []) if m < d <= n]
            if nse:
                k, source = float(np.prod(nse)), "NSE corporate action"
            elif k and (isin_changed or k * (1 - SPLIT_TOLERANCE) <= qr <= k * 1.6):
                source = "inferred from price + share count"
            else:
                continue
            out.append({"key": key, "name": s.at[n, "name"], "month_end": n,
                        "factor": k, "source": source, "old_isin": s.at[m, "isin"],
                        "new_isin": s.at[n, "isin"],
                        "price_before": s.at[m, "price"], "price_after": s.at[n, "price"]})
    return pd.DataFrame(out, columns=["key", "name", "month_end", "factor", "source", "old_isin",
                                      "new_isin", "price_before", "price_after"])


def _label(raw: float, active: float) -> str:
    if abs(raw) <= UNCHANGED:
        return "No trade"
    if raw < 0:
        return "Trimmed (big)" if raw <= -BIG else "Trimmed"
    if active >= BIG:
        return "Added (big)"
    if active >= SMALL:
        return "Added"
    if active <= -SMALL:
        return "Bought less than inflows"
    return "Kept in step with inflows"


def monthly_changes(h: pd.DataFrame, summary: pd.DataFrame,
                    splits: pd.DataFrame) -> pd.DataFrame:
    """One row per stock per month (from the 2nd month on): what changed."""
    picks = h[h["bucket"].isin(PICK_BUCKETS)]
    arb = h[h["bucket"] == ARBITRAGE].groupby(["key", "month_end"])["quantity"].sum()
    g = picks.groupby(["key", "month_end"], as_index=False).agg(
        name=("name", "last"), isin=("isin", "last"), industry=("industry", "last"),
        bucket=("bucket", "last"), quantity=("quantity", "sum"),
        value_lakh=("value_lakh", "sum"), weight=("weight", "sum"))
    g["price"] = g["value_lakh"] * 1e5 / g["quantity"]
    split_f = {(r.key, r.month_end): r.factor for r in splits.itertuples()}
    flow = dict(zip(summary["month_end"], summary["flow_factor"]))
    months = list(summary["month_end"])
    by = {k: s.set_index("month_end") for k, s in g.groupby("key")}

    rows = []
    for prev_m, m in zip(months[:-1], months[1:]):
        F = flow[m]
        for key, s in by.items():
            had, has = prev_m in s.index, m in s.index
            if not had and not has:
                continue
            f = split_f.get((key, m), 1.0)
            cur = s.loc[m] if has else None
            old = s.loc[prev_m] if had else None
            ref = cur if has else old
            r = {
                "month_end": m, "key": key, "name": ref["name"], "isin": ref["isin"],
                "industry": ref["industry"], "bucket": ref["bucket"],
                "qty_prev_adj": old["quantity"] * f if had else 0.0,
                "qty": cur["quantity"] if has else 0.0,
                "weight_prev": old["weight"] if had else 0.0,
                "weight": cur["weight"] if has else 0.0,
                "value_cr": crore(cur["value_lakh"]) if has else 0.0,
                "split_factor": f if f != 1.0 else np.nan,
                "arb_qty_prev": arb.get((key, prev_m), 0.0),
                "arb_qty": arb.get((key, m), 0.0),
            }
            r["shares_change"] = r["qty"] - r["qty_prev_adj"]
            r["price_return"] = (cur["price"] / (old["price"] / f) - 1) if had and has else np.nan
            # Rupee value of the shares bought/sold, at this month-end's price.
            r["traded_value_cr"] = (r["shares_change"] * (cur["price"] if has else old["price"] / f)
                                    / 1e7)
            if had and has:
                raw = r["qty"] / r["qty_prev_adj"] - 1
                act = r["qty"] / (r["qty_prev_adj"] * F) - 1
                r.update(raw_change=raw, active_change=act, action=_label(raw, act))
            elif has:
                r.update(raw_change=np.nan, active_change=np.nan,
                         action="NEW ENTRY" if r["arb_qty_prev"] == 0
                         else "NEW ENTRY (was in arbitrage book)")
            else:
                r.update(raw_change=-1.0, active_change=-1.0,
                         action="EXIT" if r["arb_qty"] == 0
                         else "EXIT (still in arbitrage book)")
            r["weight_change_pp"] = (r["weight"] - r["weight_prev"]) * 100
            r["note"] = _sentence(r)
            rows.append(r)
    return pd.DataFrame(rows)


def _fmt_shares(n: float) -> str:
    n = abs(n)
    if n >= 1e7:
        return f"{n / 1e7:.2f} crore"
    if n >= 1e5:
        return f"{n / 1e5:.2f} lakh"
    return f"{n:,.0f}"


def _sentence(r: dict) -> str:
    w0, w1 = r["weight_prev"] * 100, r["weight"] * 100
    a = r["action"]
    if "partly paid" in r["name"].lower():
        return ("Partly-paid shares from a rights issue: these arrive and convert into "
                "ordinary shares on the company's schedule, so this is not a buy/sell call.")
    if a.startswith("NEW ENTRY"):
        s = (f"New position: bought {_fmt_shares(r['qty'])} shares worth "
             f"Rs {r['value_cr']:,.0f} cr, now {w1:.2f}% of the fund.")
        if r["arb_qty_prev"]:
            s += " Last month it was held only in the hedged arbitrage book."
        return s
    if a.startswith("EXIT"):
        s = f"Sold out completely (was {w0:.2f}% of the fund)."
        if r["arb_qty"]:
            s += " Still appears in the hedged arbitrage book, which is not a stock bet."
        return s
    split = (f" Share count adjusted for a {r['split_factor']:g}-for-1 split/bonus."
             if r.get("split_factor") == r.get("split_factor") and r.get("split_factor") else "")
    px = r["price_return"]
    if a == "No trade":
        return (f"No shares bought or sold. Weight {w0:.2f}% -> {w1:.2f}% "
                f"from the price move ({px:+.1%}).{split}")
    verb = "Sold" if r["shares_change"] < 0 else "Bought"
    return (f"{verb} {_fmt_shares(r['shares_change'])} shares "
            f"(~Rs {abs(r['traded_value_cr']):,.0f} cr); share count {r['raw_change']:+.1%}, "
            f"{r['active_change']:+.1%} after allowing for inflows. "
            f"Weight {w0:.2f}% -> {w1:.2f}%; price {px:+.1%}.{split}")


def stock_journeys(h: pd.DataFrame, changes: pd.DataFrame,
                   splits: pd.DataFrame) -> pd.DataFrame:
    """One row per stock ever held: the whole year in a line."""
    picks = h[h["bucket"].isin(PICK_BUCKETS)]
    g = picks.groupby(["key", "month_end"], as_index=False).agg(
        name=("name", "last"), industry=("industry", "last"), bucket=("bucket", "last"),
        quantity=("quantity", "sum"), value_lakh=("value_lakh", "sum"),
        weight=("weight", "sum"))
    months = sorted(h["month_end"].unique())
    first_m, last_m = months[0], months[-1]
    # Cumulative split factor to restate any month's shares in latest terms.
    sp = splits.groupby("key")
    rows = []
    for key, s in g.groupby("key"):
        s = s.sort_values("month_end").set_index("month_end")
        ks = sp.get_group(key) if key in sp.groups else splits.iloc[0:0]

        def to_latest(m, q):
            return q * float(np.prod([f for mm, f in zip(ks["month_end"], ks["factor"]) if mm > m]
                                     or [1.0]))

        held = list(s.index)
        start, end = held[0], held[-1]
        c = changes[changes["key"] == key]
        both = c[c["active_change"].notna() & ~c["action"].str.startswith(("NEW", "EXIT"))]
        cum_active = float(np.prod(1 + both["active_change"]) - 1) if len(both) else np.nan
        q0, q1 = to_latest(start, s.at[start, "quantity"]), s.at[end, "quantity"]
        p0 = s.at[start, "value_lakh"] * 1e5 / q0
        p1 = s.at[end, "value_lakh"] * 1e5 / q1
        gaps = len(held) < months.index(end) - months.index(start) + 1
        still_held = end == last_m
        gone = month_label(months[months.index(end) + 1]) if not still_held else ""
        if start == first_m and still_held:
            status = "Held all year" + (" (with a gap)" if gaps else "")
        elif start == first_m:
            status = f"Exited {gone}"
        elif still_held:
            status = f"New this year ({month_label(start)})"
        else:
            status = f"In and out ({month_label(start)} to {month_label(end)})"
        new_pos = start != first_m
        rows.append({
            "key": key, "name": s["name"].iloc[-1], "industry": s["industry"].iloc[-1],
            "bucket": s["bucket"].iloc[-1], "status": status,
            "first_month": month_label(start), "last_month": month_label(end),
            "exit_month": gone, "months_held": len(held),
            "weight_start": s.at[start, "weight"],
            "weight_end": s.at[end, "weight"] if still_held else 0.0,
            "weight_peak": s["weight"].max(),
            "weight_change_pp": ((s.at[end, "weight"] if still_held else 0.0)
                                 - s.at[start, "weight"]) * 100,
            "shares_start": q0, "shares_end": q1 if still_held else 0.0,
            # From a starter stake of a few thousand shares the % is noise;
            # new positions are described by weight instead.
            "shares_change": np.nan if new_pos else ((q1 / q0 - 1) if still_held else -1.0),
            "active_change_cum": cum_active if (still_held and not new_pos) else np.nan,
            "months_added": int(both["action"].str.startswith("Added").sum()),
            "months_trimmed": int(both["action"].str.startswith("Trimmed").sum()),
            "months_no_trade": int((both["action"] == "No trade").sum()),
            "price_return_while_held": p1 / p0 - 1,
            "value_end_cr": crore(s.at[end, "value_lakh"]) if still_held else 0.0,
        })
    j = pd.DataFrame(rows)
    j["verdict"] = j.apply(_verdict, axis=1)
    return j.sort_values(["weight_end", "weight_start"], ascending=False).reset_index(drop=True)


def _verdict(r) -> str:
    st = r["status"]
    pr = r["price_return_while_held"]
    if "Partly Paid" in r["name"]:
        return ("Partly-paid shares from Bharti Airtel's rights issue; these are "
                "converted, not traded, so the 'exit' is not a sell decision.")
    if st.startswith("New"):
        return (f"Fresh conviction: first bought in {r['first_month']}, built to "
                f"{r['weight_end']:.2%} of the fund (price {pr:+.0%} since first seen).")
    if st.startswith("Exited"):
        return (f"Dropped: was {r['weight_start']:.2%} of the fund in {r['first_month']}, "
                f"gone by {r['exit_month']} (price {pr:+.0%} while held).")
    if st.startswith("In and out"):
        return (f"Short stay: held {r['first_month']} to {r['last_month']}, peak weight "
                f"{r['weight_peak']:.2%}, gone by {r['exit_month']} (price {pr:+.0%} while held).")
    a, sh = r["active_change_cum"], r["shares_change"]
    if abs(sh) < 0.005:
        tone = ("Frozen: not a single share bought or sold all year, so it shrank "
                "as the fund grew")
    elif a >= 0.5:
        tone = "Strongly accumulated"
    elif a >= 0.1:
        tone = "Accumulated"
    elif a > -0.1:
        tone = "Held steady (bought roughly in line with inflows)"
    elif a > -0.3:
        tone = "Under-weighted: bought far less than inflows"
    else:
        tone = "Heavily trimmed"
    return (f"{tone}. Shares {sh:+.0%} over the year ({a:+.0%} beyond inflows); "
            f"weight {r['weight_start']:.2%} -> {r['weight_end']:.2%}; price {pr:+.0%}.")


def sector_weights(h: pd.DataFrame) -> pd.DataFrame:
    """Industry x month weights for the stock picks; foreign names grouped."""
    picks = h[h["bucket"].isin(PICK_BUCKETS)].copy()
    picks["sector"] = np.where(picks["bucket"] == FOREIGN, "Foreign equity (US-listed)",
                               np.where(picks["bucket"] == REIT, "REITs", picks["industry"]))
    t = picks.pivot_table(index="sector", columns="month_end", values="weight",
                          aggfunc="sum", fill_value=0.0)
    return t.loc[t.iloc[:, -1].sort_values(ascending=False).index]
