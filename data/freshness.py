"""How old is the data, and is that a problem?

Every refresh in this framework is manual. A dashboard showing three-week-old
prices with no warning is the failure mode most likely to cause a bad decision,
because nothing about the page looks wrong -- the numbers are all there, they
are just from a fortnight ago.

Staleness is measured in TRADING days, not calendar days. A Monday reading of
Friday's close is current; the same one-calendar-day gap in midweek is not.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

FRESH_DAYS = 1          # yesterday's close on a trading morning is normal
WARN_DAYS = 3
STALE_DAYS = 6


def sessions_since(latest: dt.date, today: dt.date) -> int:
    """Trading sessions after `latest` up to today: weekdays minus NSE's
    holidays. Without the holidays, a market holiday (Gandhi Jayanti, Diwali)
    counted as a missed session and pushed the dashboard into "stale" early.
    If the holiday calendar can't be fetched, weekdays alone."""
    hol: set[dt.date] = set()
    try:
        from data.sources import nse_derivatives as nsed
        hol = set(nsed.holidays_between(latest, today))
    except Exception:          # noqa: BLE001 -- staleness must still be reported
        pass
    behind, d = 0, latest + dt.timedelta(days=1)
    while d <= today:
        if d.weekday() < 5 and d not in hol:
            behind += 1
        d += dt.timedelta(days=1)
    return behind


@dataclass
class Freshness:
    label: str              # fresh | ageing | stale
    latest: dt.date | None
    sessions_behind: int
    message: str

    @property
    def is_stale(self) -> bool:
        return self.label == "stale"


def _sessions_between(con, start: dt.date, end: dt.date) -> int:
    """Trading sessions recorded between two dates, from the market table."""
    row = con.execute(
        "SELECT COUNT(*) FROM market_days WHERE date > ? AND date <= ? AND rows > 0",
        (start.isoformat(), end.isoformat())).fetchone()
    return int(row[0]) if row else 0


def assess(con, today: dt.date | None = None,
           source: str = "market data") -> Freshness:
    """Freshness of the full-market table."""
    today = today or dt.date.today()
    row = con.execute("SELECT MAX(date) FROM market_days WHERE rows > 0").fetchone()
    if not row or not row[0]:
        return Freshness("stale", None, 999,
                         f"No {source} at all. Run run_market_ingest.py.")
    latest = dt.date.fromisoformat(row[0])

    # Trading sessions since the last recorded one -- the table cannot tell
    # us about days it never fetched.
    behind = sessions_since(latest, today)

    if behind <= FRESH_DAYS:
        return Freshness("fresh", latest, behind,
                         f"{source} current to {latest}.")
    if behind <= WARN_DAYS:
        return Freshness("ageing", latest, behind,
                         f"{source} is {behind} sessions behind, ending "
                         f"{latest}. Fine for a look, worth refreshing before "
                         f"acting.")
    return Freshness("stale", latest, behind,
                     f"{source} is {behind} sessions behind, ending {latest}. "
                     f"Prices, ranks and stop levels on this page are all from "
                     f"then -- refresh before making any decision.")


def assess_prices(con, symbols: list[str], today: dt.date | None = None) -> Freshness:
    """Freshness of the per-symbol price table the scoring actually reads.

    Measured at the date MOST symbols have reached (the median of each
    symbol's last date), not the newest date anywhere: a refresh that updated
    one symbol out of 500 used to read as "current". Symbols whose prices
    stop well before that (suspended, delisted) don't hold it back either.
    """
    today = today or dt.date.today()
    rows = con.execute("SELECT symbol, MAX(date) FROM prices GROUP BY symbol").fetchall()
    if not rows:
        return Freshness("stale", None, 999,
                         "No price history. Run run_backfill.py.")
    lasts = sorted(dt.date.fromisoformat(r[1]) for r in rows)
    latest = lasts[len(lasts) // 2]
    behind = sessions_since(latest, today)
    label = ("fresh" if behind <= FRESH_DAYS else
             "ageing" if behind <= WARN_DAYS else "stale")
    msg = {"fresh": f"Prices current to {latest}.",
           "ageing": f"Prices are {behind} sessions behind ({latest}).",
           "stale": f"Prices are {behind} sessions behind ({latest}). "
                    f"Refresh prices before acting on anything here."}[label]
    return Freshness(label, latest, behind, msg)


def assess_fundamentals(path=None, today: dt.date | None = None,
                        warn_days: int = 100) -> Freshness:
    """Fundamentals age in quarters, not sessions.

    A results season passes roughly every 90 days, so a sheet older than that
    is missing at least one set of numbers -- which is when a quality score
    starts describing a company that no longer exists in that form.
    """
    from pathlib import Path

    import fundamentals as fu

    today = today or dt.date.today()
    path = Path(path or fu.FUNDAMENTALS_PATH)
    if not path.exists():
        return Freshness("stale", None, 999,
                         "No fundamentals on file. Run fetch_fundamentals.py.")
    modified = dt.date.fromtimestamp(path.stat().st_mtime)
    age = (today - modified).days
    if age <= warn_days // 2:
        return Freshness("fresh", modified, age,
                         f"Fundamentals fetched {modified}.")
    if age <= warn_days:
        return Freshness("ageing", modified, age,
                         f"Fundamentals are {age} days old ({modified}).")
    return Freshness("stale", modified, age,
                     f"Fundamentals are {age} days old ({modified}) -- at least "
                     f"one results season has passed since. Quality scores may "
                     f"describe a company that has since reported.")
