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

WATCHING_PATH = Path(__file__).parent / "config" / "watching.csv"   # or .xlsx (userfiles.py)

COLUMNS = ["symbol", "added_on", "baseline_date", "baseline_price",
           "stop", "t1", "t2", "stopped_on", "t1_hit_on", "t2_hit_on",
           "source", "conviction", "notes"]

HELP = """\
# Stocks you are watching closely. Opens in Excel.
#
#   added_on        when you started watching
#   baseline_date   when the framework first flagged it -- taken from the
#                   decision log where possible, so performance is measured
#                   from the call, not from when you noticed it
#   baseline_price  price on that date
#   stop            stop-loss set when the name was flagged; moves up to the
#                   baseline once T1 is reached
#   t1, t2          target prices set when the name was flagged
#   stopped_on      session the low reached the stop (blank until); the
#                   entry is closed from then on
#   t1_hit_on       first session the high reached T1 (blank until)
#   t2_hit_on       same for T2; the entry is closed from then on
#   source          why it is here: rank | advisor | swing | manual
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
    stop: float = 0.0
    stopped_on: str = ""


def _path(path) -> Path:
    """config/watching.xlsx if you keep a workbook, else watching.csv."""
    if path:
        return Path(path)
    import userfiles as uf
    return uf.path_for("watching")


def load(path: Path | str | None = None) -> list[Watched]:
    import userfiles as uf
    _, rows = uf.read(_path(path))
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
                           (r.get("notes") or "").strip(),
                           num("stop"), (r.get("stopped_on") or "").strip()))
    return out


def save(rows: list[Watched], path: Path | str | None = None) -> None:
    path = _path(path)
    if path.suffix.lower() == ".xlsx":
        import userfiles as uf
        uf.write(path, uf.WATCHING, [asdict(r) for r in sorted(rows, key=lambda x: x.symbol)],
                 COLUMNS)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        fh.write(HELP)
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for r in sorted(rows, key=lambda x: x.symbol):
            w.writerow(asdict(r))


def symbols(path: Path | str | None = None) -> list[str]:
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


def _plan(price: float, atr: float | None = None) -> tuple[float, float, float]:
    """Stop, T1 and T2 for a momentum entry at this price, from the engine's
    own rules. Zeros if the config cannot be read."""
    try:
        from scoring import timing as _t
        from signals.engine import initial_stop, targets
        cfg = _t.load_config()
        sl = initial_stop(price, atr or price * 0.03, cfg)   # 3% is a mid-cap day
        return (sl, *targets(price, sl, cfg))
    except Exception:
        return 0.0, 0.0, 0.0


def _t1_r() -> float:
    try:
        from scoring import timing as _t
        return float(_t.load_config().get("targets", {}).get("t1_r", 1.5))
    except Exception:
        return 1.5


def add(symbol: str, source: str = "manual", conviction: str = "watch",
        notes: str = "", price: float | None = None,
        atr_hint: float | None = None,
        targets: tuple[float, float] | None = None,
        stop: float | None = None,
        use_log: bool = True,
        path: Path | str | None = None) -> tuple[bool, str]:
    """Start watching a symbol, inheriting its baseline from the log if known.

    The decision log records momentum calls. A swing pick is a different call,
    made by today's scan, so its caller passes use_log=False with the scan's
    price and its own stop and T1/T2 rather than borrow a momentum baseline."""
    symbol = symbol.strip().upper()
    if not symbol:
        return False, "Empty symbol."
    rows = load(path)
    if any(r.symbol == symbol for r in rows):
        return False, f"{symbol} is already on the watch list."

    today = dt.date.today().isoformat()
    logged = _baseline_from_log(symbol) if use_log else None
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

    sl, (t1, t2) = stop or 0.0, targets or (0.0, 0.0)
    if b_price > 0 and not targets:
        sl, t1, t2 = _plan(b_price, atr_hint)
    rows.append(Watched(symbol, today, b_date, round(b_price, 2),
                        round(t1, 2), round(t2, 2), "", "", source,
                        conviction, notes, stop=round(sl, 2)))
    save(rows, path)
    return True, f"{symbol} added. {note}"


def remove(symbol: str, path: Path | str | None = None) -> tuple[bool, str]:
    symbol = symbol.strip().upper()
    rows = load(path)
    kept = [r for r in rows if r.symbol != symbol]
    if len(kept) == len(rows):
        return False, f"{symbol} is not on the watch list."
    save(kept, path)
    return True, f"{symbol} removed."


def fill_missing_baselines(prices: dict[str, float],
                           path: Path | str | None = None) -> int:
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


def fill_missing_plans(path: Path | str | None = None) -> int:
    """Give any entry with a baseline but no stop (or no targets) one, once.

    An entry that already has targets gets the stop those targets were
    measured from (T1 sits t1_r x R above the baseline), so the three stay
    consistent. One with nothing gets the engine's plan at its baseline."""
    rows = load(path)
    n = 0
    for r in rows:
        if r.baseline_price <= 0 or r.stop > 0:
            continue
        if r.t1 > r.baseline_price:
            risk = (r.t1 - r.baseline_price) / _t1_r()
            r.stop = round(r.baseline_price - risk, 2)
        else:
            sl, t1, t2 = _plan(r.baseline_price)
            r.stop = round(sl, 2)
            if not r.t1:
                r.t1, r.t2 = round(t1, 2), round(t2, 2)
        n += r.stop > 0
    if n:
        save(rows, path)
    return n


def _walk(r: Watched, bars) -> tuple[str, str, str]:
    """Stopped / T1 / T2 dates from the bars after the baseline session.

    Only blanks are filled: a recorded date is a fact about the past. The
    stop is checked first within a bar -- daily bars do not say whether the
    low or the high came first, and assuming the target did would flatter
    every result. Once T1 is reached the stop moves up to the baseline, as a
    trade plan would. An entry is closed after the stop or T2."""
    stopped, t1d, t2d = r.stopped_on, r.t1_hit_on, r.t2_hit_on
    for d, high, low in bars:
        if stopped or t2d:
            break
        if d <= r.baseline_date:
            continue
        level = r.baseline_price if (t1d and t1d < d) else r.stop
        if level > 0 and low <= level:
            stopped = d
            break
        if r.t1 and not t1d and high >= r.t1:
            t1d = d
        if r.t2 and not t2d and high >= r.t2:
            t2d = d
    return stopped, t1d, t2d


def check_outcomes(bars: dict, path: Path | str | None = None) -> list[str]:
    """Record stops and targets reached since each entry's baseline.

    `bars` maps symbol -> rows of (iso date, high, low) in date order, or a
    DataFrame with date/high/low columns. Every session since the baseline is
    checked, so days the job missed are caught up rather than lost."""
    rows, news = load(path), []
    for r in rows:
        b = bars.get(r.symbol)
        if b is None or not r.baseline_date or r.baseline_price <= 0:
            continue
        if hasattr(b, "itertuples"):
            b = [(str(x.date), float(x.high), float(x.low)) for x in b.itertuples()]
        stopped, t1d, t2d = _walk(r, b)
        if t1d != r.t1_hit_on:
            news.append(f"{r.symbol} reached T1 ({r.t1:,.2f}) on {t1d}")
        if t2d != r.t2_hit_on:
            news.append(f"{r.symbol} reached T2 ({r.t2:,.2f}) on {t2d}")
        if stopped != r.stopped_on:
            at = r.baseline_price if t1d and t1d < stopped else r.stop
            news.append(f"{r.symbol} hit its stop ({at:,.2f}) on {stopped}")
        r.stopped_on, r.t1_hit_on, r.t2_hit_on = stopped, t1d, t2d
    if news:
        save(rows, path)
    return news


def update_from_store(con=None, path: Path | str | None = None) -> list[str]:
    """Fill missing stops, then check every entry against the prices on file.

    Run after each price refresh (run_swing_daily.py), on every dashboard
    recompute and by run_daily.py; it is idempotent, so running it twice
    records nothing new."""
    from data import store
    con = con or store.connect()
    fill_missing_plans(path)
    bars = {}
    for sym in symbols(path):
        df = store.load_prices(con, sym)
        if not df.empty:
            bars[sym] = df[["date", "high", "low"]]
    return check_outcomes(bars, path)


def report(prices: dict[str, float], path: Path | str | None = None) -> list[dict]:
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
            # The session counts are measured on momentum targets; a swing
            # trade's T1/T2 sit much closer and expire after a few weeks.
            if w.baseline_date and w.source != "swing":
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
        outcome = ("stopped" if w.stopped_on else "t2" if w.t2_hit_on
                   else "t1" if w.t1_hit_on else "open")
        out.append({"symbol": w.symbol, "source": w.source,
                    "outcome": outcome, "stop": w.stop,
                    "stopped_on": w.stopped_on,
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


if __name__ == "__main__":
    # PYTHONPATH=. .venv/bin/python watching.py   -- what run_swing_daily runs
    found = update_from_store()
    print("\n".join(found) if found else "Watch list: nothing new reached.")
