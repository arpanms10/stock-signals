"""Monitoring events and review diffs (Phase 8; docs/monitoring.md).

Turns three kinds of input into dated events, each with its source link:

  announcements   NSE corporate announcements, classified by config rules
  shareholding    promoter stake and pledge from the shareholding filings
  results         each new quarter's PAT / revenue (and NPAs for lenders)
                  against the same quarter a year earlier

and compares two review snapshots (scores, tiers, roles, weights, plan
actions, flags) so a review shows what moved since the last one.

An event is a prompt to re-read the thesis. Nothing here ranks or orders.
Pure: no I/O.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "monitoring.yaml"

THESIS, WATCH, INFO = "thesis_check", "watch", "info"
SEVERITIES = (THESIS, WATCH, INFO)
LABEL = {THESIS: "Thesis check", WATCH: "Watch", INFO: "Info"}


def load_config(path: Path = CONFIG) -> dict:
    return yaml.safe_load(path.read_text())


@dataclass
class Event:
    key: str                 # stable id: the same filing never makes two events
    symbol: str
    date: str                # ISO date of the filing / period
    kind: str
    severity: str
    title: str
    detail: str
    source: str
    link: str = ""
    facts: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------ announcements

def classify(category: str, text: str, rules: list[dict]) -> dict | None:
    """The first rule whose pattern matches the category or the text."""
    blob_cat, blob_txt = (category or "").strip(), (text or "").strip()
    for r in rules:
        pat = re.compile(r["match"], re.I)
        if pat.search(blob_cat) or pat.search(blob_txt):
            return r
    return None


def announcement_events(rows: list[dict], cfg: dict) -> list[Event]:
    """rows: {seq_id, symbol, at, category, text, link}."""
    out = []
    for a in rows:
        r = classify(a.get("category", ""), a.get("text", ""), cfg["announcements"])
        if r is None:
            continue
        text = re.sub(r"\s+", " ", a.get("text") or "").strip()
        out.append(Event(
            key=f"ann:{a['seq_id']}", symbol=a["symbol"], date=str(a["at"])[:10],
            kind=r["kind"], severity=r["severity"], title=r["title"],
            detail=f"{a.get('category') or 'Announcement'}: {text[:400]}",
            source="NSE corporate announcement", link=a.get("link") or "",
            facts={"category": a.get("category", "")}))
    return out


# ------------------------------------------------------------- shareholding

def _latest(rows: list[dict], metric: str, period: str = "instant") -> dict | None:
    got = [r for r in rows if r["metric"] == metric and r["period"] == period
           and r.get("status", "ok") == "ok" and r.get("value") is not None]
    return max(got, key=lambda r: r["period_end"]) if got else None


def shareholding_events(symbol: str, rows: list[dict], cfg: dict) -> list[Event]:
    c = cfg["shareholding"]
    out = []
    src = "NSE shareholding pattern"
    link = f"https://www.nseindia.com/get-quotes/equity?symbol={symbol}"
    pp = _latest(rows, "promoter_pct")
    q = _latest(rows, "promoter_qoq_change_pct")
    if q and float(q["value"]) <= -c["promoter_qoq_drop_pts"]:
        out.append(Event(f"shp:promoter_qoq:{symbol}:{q['period_end']}", symbol,
                         q["period_end"], "promoter", THESIS,
                         "Promoter stake fell in the quarter",
                         f"Promoter holding down {-float(q['value']):.2f} pts in the quarter to "
                         f"{q['period_end']}" + (f", now {float(pp['value']):.2f}%" if pp else "")
                         + ".", src, link, {"change_pts": float(q["value"])}))
    t = _latest(rows, "promoter_change_pct")
    if t and float(t["value"]) <= -c["promoter_2y_drop_pts"]:
        out.append(Event(f"shp:promoter_2y:{symbol}:{t['period_end']}", symbol,
                         t["period_end"], "promoter", WATCH,
                         "Promoter stake lower over the filings on file",
                         f"Promoter holding down {-float(t['value']):.2f} pts "
                         f"({t.get('note') or 'over ~2 years'}).", src, link,
                         {"change_pts": float(t["value"])}))
    pl = _latest(rows, "pledge_pct")
    if pl and float(pl["value"]) >= c["pledge_pct"]:
        out.append(Event(f"shp:pledge:{symbol}:{pl['period_end']}", symbol, pl["period_end"],
                         "pledge", THESIS, "Promoter shares pledged",
                         f"{float(pl['value']):.1f}% of promoter holding pledged as of "
                         f"{pl['period_end']}.", src, link, {"pledge_pct": float(pl["value"])}))
    pr = _latest(rows, "pledge_change_pct")
    if pr and float(pr["value"]) >= c["pledge_rise_pts"]:
        out.append(Event(f"shp:pledge_rise:{symbol}:{pr['period_end']}", symbol,
                         pr["period_end"], "pledge", THESIS, "Promoter pledge rising",
                         f"Pledge up {float(pr['value']):.1f} pts "
                         f"({pr.get('note') or 'since the earliest filing on file'}).",
                         src, link, {"change_pts": float(pr["value"])}))
    return out


# ------------------------------------------------------------------ results

def _quarters(rows: list[dict], metric: str) -> dict[str, dict]:
    return {r["period_end"]: r for r in rows if r["metric"] == metric
            and r["period"] == "quarter" and r.get("status", "ok") == "ok"
            and r.get("value") is not None}


def _year_ago(end: str, series: dict[str, dict]) -> dict | None:
    d = dt.date.fromisoformat(end)
    for k, r in series.items():
        k_d = dt.date.fromisoformat(k)
        if abs((d - k_d).days - 365) <= 20:
            return r
    return None


def results_events(symbol: str, rows: list[dict], cfg: dict, since: str | None = None,
                   kind: str = "company") -> list[Event]:
    """One event per quarter filed after `since` (all quarters on file if None
    is not wanted: the caller passes the earliest date it cares about)."""
    c = cfg["results"]
    pat, rev = _quarters(rows, "pat"), _quarters(rows, "revenue")
    gnpa = _quarters(rows, "gnpa_pct")
    out = []
    for end in sorted(pat):
        if since and end < since:
            continue
        cur = pat[end]
        link = cur.get("ref") or ""
        facts, bad, notes = {"quarter_end": end}, [], []
        prev = _year_ago(end, pat)
        p_now = float(cur["value"])
        if prev is not None:
            p_then = float(prev["value"])
            facts["pat"], facts["pat_year_ago"] = p_now, p_then
            if p_then > 0:
                ch = (p_now / p_then - 1) * 100
                facts["pat_yoy_pct"] = round(ch, 1)
                notes.append(f"PAT {ch:+.1f}% YoY")
                if ch <= -c["pat_yoy_drop_pct"]:
                    bad.append(f"profit down {-ch:.0f}% on the year")
                if p_now < 0 and c.get("pat_loss"):
                    bad.append("a loss against a profit a year ago")
            else:
                notes.append("year-ago PAT not positive: no % change")
        if end in rev and _year_ago(end, rev) is not None:
            r_now, r_then = float(rev[end]["value"]), float(_year_ago(end, rev)["value"])
            if r_then > 0:
                ch = (r_now / r_then - 1) * 100
                facts["revenue_yoy_pct"] = round(ch, 1)
                notes.append(f"revenue {ch:+.1f}% YoY")
                if ch <= -c["revenue_yoy_drop_pct"]:
                    bad.append(f"revenue down {-ch:.0f}% on the year")
        if end in gnpa and _year_ago(end, gnpa) is not None:
            g_now, g_then = float(gnpa[end]["value"]), float(_year_ago(end, gnpa)["value"])
            facts["gnpa_pct"], facts["gnpa_year_ago"] = g_now, g_then
            notes.append(f"gross NPA {g_then:.2f}% → {g_now:.2f}%")
            if g_now - g_then >= c["gnpa_rise_pts"]:
                bad.append(f"gross NPA up {g_now - g_then:.2f} pts on the year")
        if prev is None and not notes:
            notes.append("no year-ago quarter on file to compare")
        sev = THESIS if bad else WATCH
        title = ("Results: " + "; ".join(bad)) if bad else "Results filed"
        out.append(Event(f"res:{symbol}:{end}", symbol, end, "results", sev, title,
                         f"Quarter to {end}: " + ", ".join(notes) + ".",
                         "NSE results filing (XBRL)", link, facts))
    return out


# ------------------------------------------------------------ review diffs

def diff(prev: dict | None, cur: dict, cfg: dict) -> list[dict]:
    """What changed between two review snapshots.

    A snapshot is {"as_of", "holdings": {SYM: {score, tier, role, bucket,
    weight_pct, flags, promoter_pct, pledge_pct, plan: {your_buckets: {buy,
    trim}, suggested_roles: {...}}, valuation: {margin_of_safety_pct,
    band_percentile}}}}. Returns rows {symbol, what, before, after, note}.
    """
    if not prev:
        return []
    c = cfg["review"]
    out = []
    a, b = prev.get("holdings", {}), cur.get("holdings", {})
    for s in sorted(set(a) | set(b)):
        if s not in a:
            out.append({"symbol": s, "what": "holding", "before": None, "after": "added",
                        "note": "new in the book"})
            continue
        if s not in b:
            out.append({"symbol": s, "what": "holding", "before": "held", "after": None,
                        "note": "no longer in the book"})
            continue
        x, y = a[s], b[s]

        def add(what, before, after, note=""):
            out.append({"symbol": s, "what": what, "before": before, "after": after,
                        "note": note})

        if x.get("score") is not None and y.get("score") is not None:
            ch = y["score"] - x["score"]
            if abs(ch) >= c["score_change_pts"]:
                add("score", round(x["score"], 1), round(y["score"], 1), f"{ch:+.1f} pts")
        elif (x.get("score") is None) != (y.get("score") is None):
            add("score", x.get("score"), y.get("score"), "now scorable" if
                y.get("score") is not None else "no longer scorable")
        for k, label in (("tier", "quality tier"), ("role", "suggested role"),
                         ("bucket", "your bucket")):
            if (x.get(k) or None) != (y.get(k) or None):
                add(label, x.get(k), y.get(k))
        fa, fb = set(x.get("flags") or []), set(y.get("flags") or [])
        for f in sorted(fb - fa):
            add("governance flag", None, f, "new")
        for f in sorted(fa - fb):
            add("governance flag", f, None, "cleared")
        if x.get("weight_pct") is not None and y.get("weight_pct") is not None:
            ch = y["weight_pct"] - x["weight_pct"]
            if abs(ch) >= c["weight_change_pts"]:
                add("weight", round(x["weight_pct"], 2), round(y["weight_pct"], 2),
                    f"{ch:+.2f} pts of the book")
        for k, label in (("promoter_pct", "promoter %"), ("pledge_pct", "pledge %")):
            if x.get(k) is not None and y.get(k) is not None and abs(y[k] - x[k]) >= 0.5:
                add(label, round(x[k], 2), round(y[k], 2), f"{y[k] - x[k]:+.2f} pts")
        for plan, label in (("your_buckets", "plan (your buckets)"),
                            ("suggested_roles", "plan (suggested roles)")):
            pa = (x.get("plan") or {}).get(plan) or {}
            pb = (y.get("plan") or {}).get(plan) or {}
            va, vb = _action(pa), _action(pb)
            if va[0] != vb[0] or abs(va[1] - vb[1]) >= c["plan_change_rupees"]:
                if va == vb:
                    continue
                add(label, _fmt_action(va), _fmt_action(vb))
    return out


def _action(p: dict) -> tuple[str, float]:
    if (p.get("trim") or 0) > 0:
        return "trim", round(p["trim"])
    if (p.get("buy") or 0) > 0:
        return "add", round(p["buy"])
    return "hold", 0.0


def _fmt_action(a: tuple[str, float]) -> str:
    return "no change" if a[0] == "hold" else f"{a[0]} ₹{a[1]:,.0f}"


def summarise(events: list[Event]) -> dict:
    out = {s: 0 for s in SEVERITIES}
    for e in events:
        out[e.severity] = out.get(e.severity, 0) + 1
    return out
