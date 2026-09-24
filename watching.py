"""A curated observation list -- suggestions you want to watch closely.

Deliberately separate from config/watchlist.csv, which is the *universe* the
engine ranks over. This is much smaller and means something different: names
you have chosen to keep an eye on, usually because the framework surfaced them
and you want to see whether it was right before trusting it with money.

The key design choice: an entry inherits its **baseline price and date from the
decision log** rather than from when you happened to add it. CUPID was ranked
first at Rs 283.15 on 6 September; if you add it a fortnight later, the
question worth answering is still "what has it done since the framework called
it", not "what has it done since I got round to typing it in". A watchlist that
silently resets that baseline flatters every late addition.
"""
from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass, asdict
from pathlib import Path

WATCHING_PATH = Path(__file__).parent / "config" / "watching.csv"

COLUMNS = ["symbol", "added_on", "baseline_date", "baseline_price",
           "t1", "t2", "t1_hit_on", "t2_hit_on",
           "source", "conviction", "notes"]

HELP = """\
# Stocks you are watching closely. Opens in Excel.
#
#   added_on        when you started watching
#   baseline_date   when the framework first flagged it -- taken from the
#                   decision log where possible, so performance is measured
#                   from the call, not from when you noticed it
#   baseline_price  price on that date
#   t1, t2          target prices set when the name was flagged
#   t1_hit_on       first session the price closed at or above T1 (blank until)
#   t2_hit_on       same for T2
#   source          why it is here: rank | advisor | manual
#   conviction      your own note: watch | interested | avoid
#   notes           yours
"""


@dataclass
class Watched:
    symbol: str
    added_on: str = ""
    baseline_date: str = ""
    baseline_price: float = 0.0
    t1: float = 0.0
    t2: float = 0.0
    t1_hit_on: str = ""
    t2_hit_on: str = ""
    source: str = "manual"
    conviction: str = "watch"
    notes: str = ""


def load(path: Path | str = WATCHING_PATH) -> list[Watched]:
    path = Path(path)
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(l for l in fh if not l.lstrip().startswith("#")))
    out = []
    for r in rows:
        sym = (r.get("symbol") or "").strip().upper()
        if not sym:
            continue
        try:
            px = float(r.get("baseline_price") or 0)
        except ValueError:
            px = 0.0
        def num(key):
            try:
                return float(r.get(key) or 0)
            except ValueError:
                return 0.0
        out.append(Watched(sym, (r.get("added_on") or "").strip(),
                           (r.get("baseline_date") or "").strip(), px,
                           num("t1"), num("t2"),
                           (r.get("t1_hit_on") or "").strip(),
                           (r.get("t2_hit_on") or "").strip(),
                           (r.get("source") or "manual").strip(),
                           (r.get("conviction") or "watch").strip(),
                           (r.get("notes") or "").strip()))
    return out


def save(rows: list[Watched], path: Path | str = WATCHING_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        fh.write(HELP)
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for r in sorted(rows, key=lambda x: x.symbol):
            w.writerow(asdict(r))


def symbols(path: Path | str = WATCHING_PATH) -> list[str]:
    return [w.symbol for w in load(path)]


def _baseline_from_log(symbol: str) -> tuple[str, float] | None:
    """Earliest recorded sighting of this symbol, from the decision log."""
    try:
        import decision_log as dl

        con = dl.connect()
        row = con.execute(
            "SELECT COALESCE(price_date, run_date), price FROM decision_log "
            "WHERE symbol = ? AND price > 0 ORDER BY run_date LIMIT 1",
            (symbol.upper(),)).fetchone()
        # The price's own session, not the day the report ran. A weekend run
        # quotes Friday's close; dating it to the Sunday implies a price that
        # never existed.
        return (row[0], float(row[1])) if row else None
    except Exception:
        return None


def add(symbol: str, source: str = "manual", conviction: str = "watch",
        notes: str = "", price: float | None = None,
        atr_hint: float | None = None,
        path: Path | str = WATCHING_PATH) -> tuple[bool, str]:
    """Start watching a symbol, inheriting its baseline from the log if known."""
    symbol = symbol.strip().upper()
    if not symbol:
        return False, "Empty symbol."
    rows = load(path)
    if any(r.symbol == symbol for r in rows):
        return False, f"{symbol} is already on the watch list."

    today = dt.date.today().isoformat()
    logged = _baseline_from_log(symbol)
    if logged:
        b_date, b_price = logged
        note = (f"baseline from the decision log: first flagged {b_date} "
                f"at {b_price:,.2f}")
    elif price:
        b_date, b_price = today, price
        note = f"baseline set to today at {price:,.2f}"
    else:
        b_date, b_price = today, 0.0
        note = ("no baseline price -- run run_daily.py to fill it, or set "
                "baseline_price by hand")

    t1 = t2 = 0.0
    if b_price > 0:
        try:
            from scoring import timing as _t
            from signals.engine import initial_stop, targets
            cfg = _t.load_config()
            atr = atr_hint or (b_price * 0.03)   # 3% is a mid-cap day
            t1, t2 = targets(b_price, initial_stop(b_price, atr, cfg), cfg)
        except Exception:
            pass
    rows.append(Watched(symbol, today, b_date, round(b_price, 2),
                        round(t1, 2), round(t2, 2), "", "", source,
                        conviction, notes))
    save(rows, path)
    return True, f"{symbol} added. {note}"


def remove(symbol: str, path: Path | str = WATCHING_PATH) -> tuple[bool, str]:
    symbol = symbol.strip().upper()
    rows = load(path)
    kept = [r for r in rows if r.symbol != symbol]
    if len(kept) == len(rows):
        return False, f"{symbol} is not on the watch list."
    save(kept, path)
    return True, f"{symbol} removed."


def fill_missing_baselines(prices: dict[str, float],
                           path: Path | str = WATCHING_PATH) -> int:
    """Give any entry without a baseline today's price, once."""
    rows = load(path)
    n = 0
    for r in rows:
        if r.baseline_price <= 0 and prices.get(r.symbol):
            r.baseline_price = round(prices[r.symbol], 2)
            r.baseline_date = dt.date.today().isoformat()
            n += 1
    if n:
        save(rows, path)
    return n


def mark_target_hits(highs: dict[str, float], on_date: dt.date | None = None,
                     path: Path | str = WATCHING_PATH) -> list[str]:
    """Record the first session each target was reached. Written once, never
    revised -- the date a target was first met is a fact about the past."""
    on_date = (on_date or dt.date.today()).isoformat()
    rows, hits = load(path), []
    for r in rows:
        px = highs.get(r.symbol)
        if not px:
            continue
        if r.t1 and not r.t1_hit_on and px >= r.t1:
            r.t1_hit_on = on_date
            hits.append(f"{r.symbol} reached T1 ({r.t1:,.2f})")
        if r.t2 and not r.t2_hit_on and px >= r.t2:
            r.t2_hit_on = on_date
            hits.append(f"{r.symbol} reached T2 ({r.t2:,.2f})")
    if hits:
        save(rows, path)
    return hits


def report(prices: dict[str, float], path: Path | str = WATCHING_PATH) -> list[dict]:
    """Each watched name against the price it was flagged at."""
    out = []
    today = dt.date.today()
    for w in load(path):
        now = prices.get(w.symbol)
        days = None
        if w.baseline_date:
            try:
                days = (today - dt.date.fromisoformat(w.baseline_date)).days
            except ValueError:
                pass
        move = (100 * (now / w.baseline_price - 1)
                if now and w.baseline_price > 0 else None)
        # Where each target sits against how long it normally takes.
        t1_state = t2_state = t1_note = t2_note = ""
        try:
            from scoring import timing as _t
            from strategy import horizon as hz
            cfg = _t.load_config()
            if w.baseline_date:
                start = dt.date.fromisoformat(w.baseline_date)
                elapsed = hz.sessions_between(start, today)
                for tgt, hit_on, setter in (("t1", w.t1_hit_on, "1"),
                                            ("t2", w.t2_hit_on, "2")):
                    h = hz.for_target(tgt, cfg, start)
                    if not h:
                        continue
                    took = (hz.sessions_between(start,
                            dt.date.fromisoformat(hit_on)) if hit_on else elapsed)
                    st, msg = h.status(took, bool(hit_on))
                    if setter == "1":
                        t1_state, t1_note = st, msg
                    else:
                        t2_state, t2_note = st, msg
        except Exception:
            pass
        out.append({"symbol": w.symbol, "source": w.source,
                    "conviction": w.conviction,
                    "baseline_date": w.baseline_date,
                    "baseline_price": w.baseline_price, "price": now,
                    "t1": w.t1, "t2": w.t2,
                    "t1_hit_on": w.t1_hit_on, "t2_hit_on": w.t2_hit_on,
                    "t1_state": t1_state, "t1_note": t1_note,
                    "t2_state": t2_state, "t2_note": t2_note,
                    "days": days, "move_pct": move, "notes": w.notes})
    return sorted(out, key=lambda r: (r["move_pct"] is None,
                                      -(r["move_pct"] or 0)))
