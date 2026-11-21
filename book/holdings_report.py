"""The holdings report: every current company holding on one page.

Rendered from the research pack (book/research_pack.py), so each figure is
the same one the pages show and the /research assistant cites. Deterministic:
the same pack always gives the same report, with no model in the loop.
Pure apart from write().

Written to data/reviews/holdings-<date>.md (never committed).
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "data" / "reviews"

LOTS = {"matched": "tradebook lots", "opening": "tradebook lots plus an opening lot "
        "costed from your average", "no_trades": "the broker's long/short-term split",
        "fallback": "the broker's long/short-term split"}


def _tier(c: dict) -> str:
    if c.get("tier_label") and c.get("tier"):
        return c["tier_label"]
    return "—"


def _ord(pct: float | None) -> str:
    if pct is None:
        return "—"
    n = round(pct * 100)
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def _r(v, d: int = 0) -> str:
    """₹ amount, Indian style for lakh and crore."""
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e7:
        s = f"₹{a / 1e7:,.2f} cr"
    elif a >= 1e5:
        s = f"₹{a / 1e5:,.2f} lakh"
    else:
        s = f"₹{a:,.{d}f}"
    return ("−" if v < 0 else "") + s


def _p(v, d: int = 1, sign: bool = False) -> str:
    if v is None:
        return "—"
    return f"{v:+.{d}f}%" if sign else f"{v:.{d}f}%"


def _n(v, d: int = 1) -> str:
    return "—" if v is None else f"{v:,.{d}f}"


def _metric(h: dict, key: str):
    m = (h.get("metrics") or {}).get(key) or {}
    return m.get("value")


def _yoy(h: dict, metric: str) -> tuple[str | None, float | None]:
    q = (h.get("quarters") or {}).get(metric) or {}
    if not q:
        return None, None
    ends = sorted(q)
    last = ends[-1]
    y = int(last[:4]) - 1
    ago = next((e for e in ends if e.startswith(str(y)) and e[5:] == last[5:]), None)
    a, b = (q[last] or {}).get("value"), (q.get(ago) or {}).get("value") if ago else None
    if a is None or not b or b <= 0:
        return last, None
    return last, (a / b - 1) * 100


def _action(row: dict | None) -> str:
    if not row:
        return "—"
    if (row.get("trim") or 0) > 0:
        tax = row.get("trim_tax_estimate") or 0
        return f"trim {_r(row['trim'])}" + (f" (est. tax {_r(tax)})" if tax > 0 else "")
    if (row.get("buy") or 0) > 0:
        return f"add {_r(row['buy'])}"
    return "no change"


def _valuation_line(v: dict | None) -> str:
    if not v:
        return "No valuation (not enough filed data)."
    parts = []
    b, pe, rv = v.get("band") or {}, v.get("peers") or {}, v.get("reverse") or {}
    head = v.get("headline") or b.get("metric")
    cur = (v.get("current") or {}).get(head)
    if cur is not None and head:
        parts.append(f"{head.upper().replace('_', '/')} {cur:.1f}")
    if b.get("percentile") is not None:
        parts.append(f"{_ord(b['percentile'])} percentile of its own "
                     f"{round((b.get('months') or 60) / 12)}-year range")
    if pe.get("relative") is not None:
        parts.append(f"{pe['relative']:.2f}× the median of {pe.get('n')} peers")
    if rv.get("implied_pct") is not None:
        parts.append(f"price implies {rv['implied_pct']:.1f}% growth vs "
                     f"{_p(rv.get('delivered_pct'))} delivered ({rv.get('delivered_basis', '')})")
    sc = v.get("scenarios") or {}
    if sc.get("base", {}).get("fair_value") is not None:
        parts.append("scenarios bear / base / bull " + " / ".join(
            _r(sc[k]["fair_value"]) for k in ("bear", "base", "bull") if k in sc)
            + " (scenarios, not targets)")
    return "; ".join(parts) + "." if parts else "No valuation figures."


def _holding(h: dict, plans: dict, pack_total: float) -> list[str]:
    s = h["symbol"]
    p, c, t = h.get("position") or {}, h.get("scorecard") or {}, h.get("tax") or {}
    L = [f"### {s} · {h.get('name') or ''}".rstrip(" ·"), ""]
    L.append(f"{h.get('sector') or 'sector n/a'} · {h.get('cap_band') or 'cap n/a'} · "
             f"data {h.get('data_freshness') or 'n/a'}")
    L.append("")
    L.append(f"- **Position:** {_n(p.get('quantity'), 0)} @ avg {_r(p.get('avg_cost'), 2)}, "
             f"price {_r(p.get('price'), 2)} ({p.get('price_date') or '—'}); value "
             f"{_r(p.get('market_value'))}, {_p(p.get('weight_pct'), 2)} of the book; "
             f"P/L {_p(p.get('pnl_pct'), 1, True)}. Bucket: "
             f"{p.get('bucket') or 'blank (counted as satellite)'}.")
    if c.get("score") is not None:
        L.append(f"- **Scorecard ({c.get('model')}):** {c['score']:.1f}/100 on "
                 f"{c.get('coverage_pct', 0):.0f}% of points assessable; "
                 f"{_tier(c) if c.get('tier') else 'no tier (too little assessable)'}; "
                 f"suggested role {c.get('role_suggested')}"
                 + (f" ({'; '.join(c.get('role_reasons') or [])})" if c.get("role_reasons")
                    else "") + ". Descriptive, not backtested.")
    else:
        L.append("- **Scorecard:** not enough data to score.")
    flags = c.get("flags") or []
    if flags:
        L.append("- **Governance flags:** " + "; ".join(
            f"{f.get('text')} ({f.get('severity')})" for f in flags) + ".")
    L.append(f"- **Valuation:** {_valuation_line(h.get('valuation'))}")
    end, pat = _yoy(h, "pat")
    _, rev = _yoy(h, "revenue")
    if end:
        L.append(f"- **Latest quarter ({end}):** PAT {_p(pat, 1, True)} YoY, revenue "
                 f"{_p(rev, 1, True)} YoY. Trailing PAT {_r((_metric(h, 'pat:ttm') or 0) * 1e7) if _metric(h, 'pat:ttm') is not None else '—'}"
                 f"; ROE {_p(_metric(h, 'roe_pct:fy'))} (last FY).")
    pr, pl = _metric(h, "promoter_pct:instant"), _metric(h, "pledge_pct:instant")
    if pr is not None:
        L.append(f"- **Shareholding:** promoters {_p(pr, 2)}, change "
                 f"{_n(_metric(h, 'promoter_change_pct:instant'), 2)} pts over the filings on "
                 f"file; pledged {_p(pl)} of promoter holding.")
    rk = h.get("risk") or {}
    bits = []
    if rk.get("themes"):
        bits.append("themes " + ", ".join(rk["themes"]))
    if rk.get("flags"):
        bits.append("risk flags " + ", ".join(rk["flags"]))
    if rk.get("liquidity_days") is not None:
        bits.append("sellable within a day" if rk["liquidity_days"] < 1 else
                    f"{rk['liquidity_days']:.1f} days to sell at 20% of volume")
    if bits:
        L.append("- **Risk:** " + "; ".join(bits) + ".")
    pin = h.get("plan") or {}
    L.append(f"- **Plan (no new money):** your buckets: "
             f"{_action(plans['your_buckets'].get(s))}; suggested roles: "
             f"{_action(plans['suggested_roles'].get(s))}. Position cap "
             f"{_p(pin.get('cap_pct'))}"
             + (f"; no new money: {'; '.join(pin['no_new_money'])}" if pin.get("no_new_money")
                else "") + ".")
    if t:
        L.append(f"- **Unrealised:** short-term {_r(t.get('short_term'))}, long-term "
                 f"{_r(t.get('long_term'))} (estimated from {LOTS.get(t.get('lots_source'), t.get('lots_source'))}).")
    open_ev = [e for e in h.get("events") or [] if e.get("status") == "open"
               and e.get("severity") == "thesis_check"]
    for e in open_ev:
        link = f" [filing]({e['link']})" if e.get("link") else ""
        L.append(f"- **Thesis check {e['date']}:** {e['title']}. {e['detail']}{link}")
    ch = h.get("changes")
    if ch and (ch.get("score_change") or ch.get("tier_from") != ch.get("tier_to")
               or ch.get("role_from") != ch.get("role_to")):
        L.append(f"- **Since {ch['from']}:** score {_n(ch.get('score_change'), 1)} pts; "
                 f"tier {ch.get('tier_from')} → {ch.get('tier_to')}; role "
                 f"{ch.get('role_from')} → {ch.get('role_to')}.")
    L.append("")
    return L


def render(pack: dict) -> str:
    port, hs = pack["portfolio"], pack["holdings"]
    total, op = port["total_value"], port["operating_equity"]
    plans = {k: (pack["plan"].get(k) or {}).get("rows") or {}
             for k in ("your_buckets", "suggested_roles")}
    as_of = pack["as_of"]
    L = [f"# Holdings report {as_of['today']}", "",
         f"Prices as of {as_of['prices']}; built from the research pack of "
         f"{pack['generated_at'].replace('T', ' ')}. Every figure comes from the app's "
         "engines; the scorecard is descriptive and not backtested; valuation scenarios "
         "are not targets; tax figures are estimates. Not advice, and nothing here places "
         "an order.", "",
         "Reading the table: Score is out of 100 on the points that could be assessed "
         "(Assessed); a holding with little assessable gets no tier. Own band is where "
         "today's multiple sits in the stock's own 5-year range (low = cheap against "
         "itself). Plans assume no new money.", "", "## The book", ""]
    cost = sum((h["position"].get("avg_cost") or 0) * (h["position"].get("quantity") or 0)
               for h in hs.values())
    mv = sum(h["position"].get("market_value") or 0 for h in hs.values())
    L += [f"- **Value:** {_r(total)} in all; company shares {_r(op)} "
          f"({len(hs)} holdings), REITs / InvITs / other {_r(total - op)}.",
          f"- **Company shares P/L:** {_r(mv - cost)} on cost {_r(cost)} "
          f"({_p((mv / cost - 1) * 100 if cost else None, 1, True)}).",
          f"- **Concentration:** {port['concentration']['effective_names']:.1f} effective "
          "names (1 / sum of squared weights)."]
    for k, label in (("your_buckets", "your buckets"), ("suggested_roles", "suggested roles")):
        b = {x["role"]: x for x in (pack["plan"].get(k) or {}).get("buckets", [])}
        if b:
            L.append(f"- **Core : Satellite by {label}:** "
                     f"{_p(b.get('core', {}).get('before_pct'), 0)} : "
                     f"{_p(b.get('satellite', {}).get('before_pct'), 0)} against 70 : 30.")
    if port.get("buckets_blank"):
        L.append(f"- **Buckets:** {port['buckets_blank']} of {len(hs)} blank, counted as "
                 "satellite. Set them in your holdings file for the 70:30 view to mean much.")
    rh = (pack.get("risk") or {}).get("history") or {}
    if rh:
        L.append(f"- **Historical risk of today's mix** ({rh.get('from')} to {rh.get('to')}): "
                 f"return {_p(rh.get('return_ann_pct'), 1, True)} a year, volatility "
                 f"{_p(rh.get('volatility_pct'))}, worst fall {_p(rh.get('max_drawdown_pct'))}, "
                 f"beta {_n(rh.get('beta'), 2)}. Backward-looking.")
    sec = defaultdict(float)
    for h in hs.values():
        sec[h.get("sector") or "n/a"] += h["position"].get("weight_pct") or 0
    top = sorted(sec.items(), key=lambda x: -x[1])[:5]
    L.append("- **Largest sectors:** " + ", ".join(f"{k} {v:.1f}%" for k, v in top) + ".")
    for f in (pack.get("risk") or {}).get("flags") or []:
        if f.get("holdings"):
            L.append(f"- **{f['label']}:** {', '.join(f['holdings'])} ({_p(f.get('pct'))} of "
                     "the book).")
    L.append("")

    L += ["## Holdings at a glance", "",
          "| Holding | Weight | P/L | Score | Assessed | Tier | Role (suggested) | Bucket | "
          "Own band | Plan: your buckets | Plan: suggested | Open checks |",
          "|---|---:|---:|---:|---:|---|---|---|---:|---|---|---:|"]
    order = sorted(hs.values(), key=lambda h: -(h["position"].get("weight_pct") or 0))
    for h in order:
        p, c, v = h["position"], h.get("scorecard") or {}, h.get("valuation") or {}
        band = (v.get("band") or {}).get("percentile")
        n_open = sum(1 for e in h.get("events") or [] if e.get("status") == "open"
                     and e.get("severity") == "thesis_check")
        L.append(f"| {h['symbol']} | {_p(p.get('weight_pct'))} | {_p(p.get('pnl_pct'), 1, True)} "
                 f"| {_n(c.get('score'), 0)} | {_p(c.get('coverage_pct'), 0)} | {_tier(c)} "
                 f"| {c.get('role_suggested') or '—'} | {p.get('bucket') or '—'} "
                 f"| {_ord(band)} "
                 f"| {_action(plans['your_buckets'].get(h['symbol']))} "
                 f"| {_action(plans['suggested_roles'].get(h['symbol']))} | {n_open or ''} |")
    L += ["", "## Each holding", ""]
    for h in order:
        L += _holding(h, plans, total)

    tx = pack.get("tax") or {}
    ty = tx.get("this_year") or {}
    if ty:
        L += ["## Tax this year", "",
              f"- **FY {ty['fy']} realised:** short-term {_r(ty.get('st'))}, long-term "
              f"{_r(ty.get('lt'))}; estimated tax {_r(ty.get('tax'))}; long-term exemption "
              f"left {_r(ty.get('exemption_left'))}."
              + (" Figures entered by you." if ty.get("overridden") else "")]
        inc = tx.get("incomplete_sales") or []
        if inc:
            L.append(f"- **Incomplete:** {len(inc)} sales with unknown cost ("
                     + ", ".join(sorted({x['symbol'] for x in inc})) + f", {_r(sum(x['value'] for x in inc))}"
                     " sold), so realised gains are understated. Enter the broker's figures "
                     "under realised_override in config/tax.yaml.")
        for g in (tx.get("harvest") or {}).get("gains") or []:
            if (g.get("gain") or 0) >= 1000:
                L.append(f"- **Harvest option:** {g['symbol']} {g['qty']:.0f} shares, gain "
                         f"{_r(g['gain'])} inside the exemption; future tax saved about "
                         f"{_r(g['future_tax_saved'])} for {_r(g['round_trip_cost'])} in costs.")
        L.append("")
    rv = pack.get("review") or {}
    if rv.get("latest"):
        L += ["## Monitoring", "",
              f"Last review {rv['latest']}"
              + (f", compared with {rv['previous']}" if rv.get("previous") else "")
              + f": {len(rv.get('open_thesis_checks') or [])} open thesis checks, "
              f"{len(rv.get('changes') or [])} changes. Details in the review report and on "
              "Portfolio › Reviews.", ""]
    return "\n".join(L).rstrip() + "\n"


def write(text: str, as_of: str, d: Path = REPORTS) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"holdings-{as_of}.md"
    p.write_text(text)
    return p
