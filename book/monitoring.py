"""Monitoring & reviews: the I/O around strategy/monitor.py (Phase 8).

Tables in data/research.db (local only):

  announcement        NSE announcements fetched for your holdings, kept
  announcement_fetch  how far each holding has been fetched
  monitor_event       every event, with its status (open / reviewed) and
                      your note; an event is never re-opened once reviewed
  review_snapshot     a dated copy of scores, tiers, roles, weights, flags,
                      shareholding and plan actions, so reviews can diff

Reports are written to data/reviews/ (never committed): review-<date>.md
(events and changes) and holdings-<date>.md (every holding, from the research
pack; book/holdings_report.py).
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from book import book_view as bvw
from book import research_db as rdb
from book import securities as sec
from strategy import monitor as mon

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "data" / "reviews"

SCHEMA = """
CREATE TABLE IF NOT EXISTS announcement (
    seq_id   TEXT PRIMARY KEY,
    symbol   TEXT NOT NULL,
    at       TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    text     TEXT NOT NULL DEFAULT '',
    link     TEXT NOT NULL DEFAULT '',
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_announcement_symbol ON announcement(symbol, at);
CREATE TABLE IF NOT EXISTS announcement_fetch (
    symbol     TEXT PRIMARY KEY,
    fetched_to TEXT NOT NULL,
    at         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS monitor_event (
    key        TEXT PRIMARY KEY,
    symbol     TEXT NOT NULL,
    date       TEXT NOT NULL,
    kind       TEXT NOT NULL,
    severity   TEXT NOT NULL,
    title      TEXT NOT NULL,
    detail     TEXT NOT NULL,
    source     TEXT NOT NULL,
    link       TEXT NOT NULL DEFAULT '',
    facts      TEXT NOT NULL DEFAULT '{}',
    first_seen TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'open',
    note       TEXT NOT NULL DEFAULT '',
    reviewed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_event_symbol ON monitor_event(symbol, date);
CREATE TABLE IF NOT EXISTS review_snapshot (
    as_of      TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    payload    TEXT NOT NULL
);
"""


def connect():
    con = rdb.connect()
    con.executescript(SCHEMA)
    return con


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def held_symbols() -> list[str]:
    """Operating-equity holdings (REITs and ETFs file elsewhere or not at all)."""
    o, _ = bvw.load(refresh=False)
    if o is None:
        return []
    return sorted({sec.split_symbol(p.symbol)[0] if hasattr(sec, "split_symbol") else p.symbol
                   for p in o.positions if p.sleeve == sec.OPERATING})


# ------------------------------------------------------------ announcements

def refresh_announcements(con, symbols: list[str], today: dt.date, cfg: dict,
                          fetch=None) -> tuple[int, list[str]]:
    """Fetch what's new since each holding's last fetch. Returns (new rows,
    problems). A failed holding keeps what it had and is retried next time."""
    if fetch is None:
        from data.sources import nse_announcements as nas
        fetch = nas.fetch
    new, problems = 0, []
    last = dict(con.execute("SELECT symbol, fetched_to FROM announcement_fetch"))
    for s in symbols:
        if s in last:
            start = dt.date.fromisoformat(last[s]) - dt.timedelta(days=cfg["refetch_overlap_days"])
        else:
            start = today - dt.timedelta(days=cfg["lookback_days"])
        try:
            rows = fetch(s, start, today)
        except Exception as exc:                    # noqa: BLE001
            problems.append(f"{s}: announcements not fetched ({type(exc).__name__}: {exc})")
            continue
        before = con.total_changes
        con.executemany(
            "INSERT OR IGNORE INTO announcement (seq_id, symbol, at, category, text, link, "
            "fetched_at) VALUES (?,?,?,?,?,?,?)",
            [(r["seq_id"], s, r["at"], r["category"], r["text"], r["link"], _now())
             for r in rows])
        new += con.total_changes - before
        con.execute("INSERT OR REPLACE INTO announcement_fetch VALUES (?,?,?)",
                    (s, today.isoformat(), _now()))
        con.commit()
    return new, problems


def announcements(con, symbol: str | None = None, since: str | None = None) -> list[dict]:
    q, args = "SELECT * FROM announcement WHERE 1=1", []
    if symbol:
        q += " AND symbol = ?"
        args.append(symbol)
    if since:
        q += " AND at >= ?"
        args.append(since)
    return [dict(r) for r in con.execute(q + " ORDER BY at DESC", args)]


# ------------------------------------------------------------------- events

def detect(con, symbols: list[str], today: dt.date, cfg: dict) -> list[mon.Event]:
    """Every event the stored data supports for these holdings."""
    since = (today - dt.timedelta(days=cfg["lookback_days"])).isoformat()
    results_since = (today - dt.timedelta(days=cfg["lookback_days"] + 90)).isoformat()
    out: list[mon.Event] = []
    for s in symbols:
        out += mon.announcement_events(announcements(con, s, since), cfg)
        rows = rdb.metrics(con, s)
        out += mon.shareholding_events(s, rows, cfg)
        out += mon.results_events(s, rows, cfg, since=results_since)
    return out


def record(con, events: list[mon.Event]) -> int:
    """Insert new events; refresh the wording of open ones; never touch a
    reviewed one's status or note. Returns how many were new."""
    before = con.execute("SELECT COUNT(*) FROM monitor_event").fetchone()[0]
    for e in events:
        con.execute(
            "INSERT INTO monitor_event (key, symbol, date, kind, severity, title, detail, "
            "source, link, facts, first_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET severity=excluded.severity, title=excluded.title, "
            "detail=excluded.detail, link=excluded.link, facts=excluded.facts, "
            "kind=excluded.kind WHERE status = 'open'",
            (e.key, e.symbol, e.date, e.kind, e.severity, e.title, e.detail, e.source,
             e.link, json.dumps(e.facts, default=str), _now()))
    con.commit()
    return con.execute("SELECT COUNT(*) FROM monitor_event").fetchone()[0] - before


def events(con, symbols: list[str] | None = None, status: str | None = None,
           severities: tuple[str, ...] = mon.SEVERITIES, since: str | None = None) -> list[dict]:
    q = (f"SELECT * FROM monitor_event WHERE severity IN "
         f"({','.join('?' * len(severities))})")
    args: list = list(severities)
    if status:
        q += " AND status = ?"
        args.append(status)
    if since:
        q += " AND date >= ?"
        args.append(since)
    rows = [dict(r) for r in con.execute(q + " ORDER BY date DESC, symbol", args)]
    if symbols is not None:
        keep = set(symbols)
        rows = [r for r in rows if r["symbol"] in keep]
    for r in rows:
        r["facts"] = json.loads(r["facts"] or "{}")
    return rows


def set_status(con, key: str, status: str, note: str = "") -> None:
    """Mark an event reviewed (with what you decided) or re-open it."""
    if status not in ("open", "reviewed"):
        raise ValueError(status)
    con.execute("UPDATE monitor_event SET status = ?, note = ?, reviewed_at = ? WHERE key = ?",
                (status, note, _now() if status == "reviewed" else None, key))
    con.commit()
    rdb.log(con, f"event {status}", key, {"note": note})


# ---------------------------------------------------------------- snapshots

def _metric(rows: list[dict], metric: str) -> float | None:
    got = [r for r in rows if r["metric"] == metric and r["period"] == "instant"
           and r.get("status") == "ok" and r.get("value") is not None]
    return float(max(got, key=lambda r: r["period_end"])["value"]) if got else None


def snapshot(rg: dict, plans: dict, con, today: dt.date) -> dict:
    """rg: risk_view.gather(); plans: {"your_buckets": Plan, "suggested_roles": Plan}."""
    total = float(rg["overview"].total_value)
    rows_by = {k: {r.symbol: r for r in p.rows} for k, p in plans.items()}
    hold = {}
    for i in rg["items"]:
        p, c, v, s = i["position"], i["card"], i["valuation"], i["base"]
        m = rdb.metrics(con, s)
        hold[s] = {
            "score": c.score if c else None, "tier": c.tier if c else None,
            "role": c.role if c else None, "bucket": p.bucket or None,
            "coverage_pct": c.coverage_pct if c else None,
            "flags": [f.text for f in c.flags] if c else [],
            "weight_pct": float(p.market_value) / total * 100 if total else None,
            "market_value": float(p.market_value),
            "promoter_pct": _metric(m, "promoter_pct"), "pledge_pct": _metric(m, "pledge_pct"),
            "valuation": {"margin_of_safety_pct": v.margin_of_safety_pct if v else None,
                          "band_percentile": (v.band or {}).get("percentile") if v else None},
            "plan": {k: ({"buy": r.buy, "trim": r.trim, "target": r.target}
                         if (r := rows_by[k].get(s)) else {}) for k in rows_by}}
    return {"as_of": today.isoformat(), "created_at": _now(),
            "prices_as_of": rg["overview"].price_date, "total_value": total,
            "holdings": hold}


def save_snapshot(con, snap: dict) -> None:
    """One per day; a second review the same day replaces it."""
    con.execute("INSERT OR REPLACE INTO review_snapshot VALUES (?,?,?)",
                (snap["as_of"], snap["created_at"], json.dumps(snap, default=str)))
    con.commit()


def snapshots(con) -> list[str]:
    return [r[0] for r in con.execute("SELECT as_of FROM review_snapshot ORDER BY as_of")]


def load_snapshot(con, as_of: str) -> dict | None:
    r = con.execute("SELECT payload FROM review_snapshot WHERE as_of = ?", (as_of,)).fetchone()
    return json.loads(r[0]) if r else None


def previous(con, as_of: str) -> dict | None:
    r = con.execute("SELECT payload FROM review_snapshot WHERE as_of < ? ORDER BY as_of DESC "
                    "LIMIT 1", (as_of,)).fetchone()
    return json.loads(r[0]) if r else None


# ------------------------------------------------------------------- report

def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.2f}"
    return str(v)


def report(snap: dict, prev: dict | None, evs: list[dict], changes: list[dict],
           problems: list[str]) -> str:
    """The review as Markdown: open thesis checks, what changed, watch items."""
    L = [f"# Portfolio review {snap['as_of']}", "",
         f"Prices as of {snap['prices_as_of']}; {len(snap['holdings'])} company holdings; "
         + (f"compared with the review of {prev['as_of']}." if prev else
            "first review, so nothing to compare yet."), "",
         "Events are prompts to re-read the thesis, not trade signals. The scorecard is "
         "descriptive and not backtested.", ""]
    open_t = [e for e in evs if e["severity"] == mon.THESIS and e["status"] == "open"]
    L += [f"## Thesis checks open ({len(open_t)})", ""]
    if not open_t:
        L += ["None.", ""]
    for e in open_t:
        link = f" [filing]({e['link']})" if e["link"] else ""
        L.append(f"- **{e['symbol']}** {e['date']}: {e['title']}. {e['detail']}{link}")
    L += ["", f"## What changed since the last review ({len(changes)})", ""]
    if not prev:
        L += ["First review: the next one will list changes in scores, tiers, roles, "
              "weights, flags, shareholding and plan actions.", ""]
    elif not changes:
        L += ["Nothing above the thresholds in config/monitoring.yaml.", ""]
    else:
        L += ["| Holding | What | Before | After | Note |", "|---|---|---|---|---|"]
        L += [f"| {c['symbol']} | {c['what']} | {_fmt(c['before'])} | {_fmt(c['after'])} "
              f"| {c['note']} |" for c in changes]
        L.append("")
    watch = [e for e in evs if e["severity"] == mon.WATCH and e["status"] == "open"]
    L += [f"## Watch ({len(watch)})", ""]
    L += [f"- **{e['symbol']}** {e['date']}: {e['title']}. {e['detail'][:200]}"
          + (f" [filing]({e['link']})" if e["link"] else "") for e in watch] or ["None."]
    done = [e for e in evs if e["status"] == "reviewed"]
    if done:
        L += ["", f"## Reviewed ({len(done)})", ""]
        L += [f"- **{e['symbol']}** {e['date']}: {e['title']}: {e['note'] or 'no note'}"
              for e in done]
    if problems:
        L += ["", "## Data problems", ""] + [f"- {p}" for p in problems]
    return "\n".join(L) + "\n"


def write_report(text: str, as_of: str, d: Path = REPORTS) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"review-{as_of}.md"
    p.write_text(text)
    return p


def holdings_report(today: dt.date | None = None) -> Path:
    """Rebuild the research pack and render the holdings report from it, so
    the report, the pages and /research all read the same figures."""
    from book import holdings_report as hr
    from book import research_pack as rp
    pack = rp.build(today=today)
    rp.write(pack)
    return hr.write(hr.render(pack), pack["as_of"]["today"])


def run(today: dt.date | None = None, fetch: bool = True, save_scores: bool = True) -> dict:
    """The whole review: fetch, detect, snapshot, diff, report."""
    from book import risk_view as rv
    from book import scorecards as bsc
    today = today or dt.date.today()
    cfg = mon.load_config()
    con = connect()
    try:
        if save_scores:
            bsc.build(save=True)
        rg = rv.gather()
        if "error" in rg:
            raise RuntimeError(rg["error"])
        symbols = sorted(i["base"] for i in rg["items"])
        new_ann, problems = (refresh_announcements(con, symbols, today, cfg)
                             if fetch else (0, ["announcements not fetched (offline run)"]))
        new_ev = record(con, detect(con, symbols, today, cfg))
        plans = {"your_buckets": rv.make_plan(rg, 0, 12, False),
                 "suggested_roles": rv.make_plan(rg, 0, 12, True)}
        snap = snapshot(rg, plans, con, today)
        prev = previous(con, snap["as_of"])
        save_snapshot(con, snap)
        changes = mon.diff(prev, snap, cfg)
        since = (today - dt.timedelta(days=cfg["lookback_days"])).isoformat()
        evs = events(con, symbols, severities=(mon.THESIS, mon.WATCH), since=since)
        path = write_report(report(snap, prev, evs, changes, problems), snap["as_of"])
        holdings = holdings_report(today)
        return {"as_of": snap["as_of"], "previous": prev["as_of"] if prev else None,
                "holdings_report": holdings,
                "new_announcements": new_ann, "new_events": new_ev, "changes": changes,
                "events": evs, "problems": problems, "report": path}
    finally:
        con.close()
