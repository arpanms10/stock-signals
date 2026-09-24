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

    # Count weekdays since the last recorded session as a proxy for sessions
    # missed -- the table cannot tell us about days it never fetched.
    behind = 0
    d = latest + dt.timedelta(days=1)
    while d <= today:
        if d.weekday() < 5:
            behind += 1
        d += dt.timedelta(days=1)

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
    """Freshness of the per-symbol price table the scoring actually reads."""
    today = today or dt.date.today()
    row = con.execute("SELECT MAX(date) FROM prices").fetchone()
    if not row or not row[0]:
        return Freshness("stale", None, 999,
                         "No price history. Run run_backfill.py.")
    latest = dt.date.fromisoformat(row[0])
    behind = 0
    d = latest + dt.timedelta(days=1)
    while d <= today:
        if d.weekday() < 5:
            behind += 1
        d += dt.timedelta(days=1)
    label = ("fresh" if behind <= FRESH_DAYS else
             "ageing" if behind <= WARN_DAYS else "stale")
    msg = {"fresh": f"Prices current to {latest}.",
           "ageing": f"Prices are {behind} sessions behind ({latest}).",
           "stale": f"Prices are {behind} sessions behind ({latest}). "
                    f"Run run_backfill.py before acting on anything here."}[label]
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
