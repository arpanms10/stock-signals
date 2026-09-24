"""How long a target normally takes.

Answers "by when should this have happened?" from measured history rather than
from a model. A volatility-based estimate -- distance divided by daily
volatility, squared -- was tested against 9,650 real paths and correlated -0.03
with the actual time taken. It is not used.

What IS used is the observed distribution: for a target 1.5R away, the median
was 17 sessions, three quarters arrived within 45, nine tenths within 103, and
12% never arrived inside a year.

These are reported as calibration, not as a rule. Positions slow to reach T1
went on to return 7.4% over the next six months against 9.6% for fast ones --
a real difference in the expected direction, but far too small to trade on
after costs. Knowing what normal looks like is worth something; acting
mechanically on it is not.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

SESSIONS_PER_MONTH = 21


@dataclass
class Horizon:
    target: str
    median_sessions: int
    typical_sessions: int
    slow_sessions: int
    never_pct: int
    median_date: dt.date
    typical_date: dt.date
    slow_date: dt.date

    def describe(self) -> str:
        return (f"{self.target}: normally {self.median_sessions} sessions "
                f"(~{self.median_sessions / SESSIONS_PER_MONTH:.1f} months), "
                f"three quarters within {self.typical_sessions}. "
                f"{self.never_pct}% never get there inside a year.")

    def status(self, sessions_elapsed: int, hit: bool) -> tuple[str, str]:
        """Where a live position sits against the distribution."""
        if hit:
            speed = ("fast" if sessions_elapsed <= self.median_sessions
                     else "normal" if sessions_elapsed <= self.typical_sessions
                     else "slow")
            return speed, f"reached in {sessions_elapsed} sessions ({speed})"
        if sessions_elapsed <= self.median_sessions:
            return "early", f"{sessions_elapsed} sessions in; typical is {self.median_sessions}"
        if sessions_elapsed <= self.typical_sessions:
            return "due", (f"{sessions_elapsed} sessions in, past the "
                           f"{self.median_sessions}-session median")
        if sessions_elapsed <= self.slow_sessions:
            return "late", (f"{sessions_elapsed} sessions without reaching "
                            f"{self.target} -- three quarters arrive by "
                            f"{self.typical_sessions}")
        return "stalled", (f"{sessions_elapsed} sessions and still short of "
                           f"{self.target}. Nine in ten arrive by "
                           f"{self.slow_sessions}; this is in the tail, or in "
                           f"the {self.never_pct}% that never arrive")


def _add_sessions(start: dt.date, sessions: int) -> dt.date:
    """Approximate a session count as calendar days, skipping weekends."""
    d, added = start, 0
    while added < sessions:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


def for_target(target: str, cfg: dict, start: dt.date | None = None) -> Horizon | None:
    h = (cfg.get("targets", {}).get("horizon_sessions") or {}).get(target.lower())
    if not h:
        return None
    start = start or dt.date.today()
    return Horizon(target.upper(), h["median"], h["typical"], h["slow"],
                   h["never_pct"],
                   _add_sessions(start, h["median"]),
                   _add_sessions(start, h["typical"]),
                   _add_sessions(start, h["slow"]))


def sessions_between(start: dt.date, end: dt.date | None = None) -> int:
    """Trading sessions between two dates, weekends excluded."""
    end = end or dt.date.today()
    n, d = 0, start
    while d < end:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n
