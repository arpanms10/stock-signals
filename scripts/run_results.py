"""Quarterly results: score new filings for the stocks you follow.

Covers your holdings, the watch list, and the latest swing and momentum picks
(results/coverage.py). For each, reads NSE's XBRL filings for the last eight
quarters and scores the newest against the company's own trend -- growth,
acceleration, margins, earnings quality; NII, credit cost and bad loans for
lenders (results/scorecard.py). Then measures how the stock reacted.

The verdict (strong / in line / weak) is judged against the company's own
history, not analyst estimates, which no free source carries. It is shown
for reading, and does not feed any ranking until research/run_results_backtest.py
shows it predicts anything.

Idempotent: a filing already scored is not fetched again. Run daily in
reporting season; scripts/run_swing_daily.py calls it after the price refresh.

    PYTHONPATH=. .venv/bin/python scripts/run_results.py
    PYTHONPATH=. .venv/bin/python scripts/run_results.py --symbols TCS INFY
    PYTHONPATH=. .venv/bin/python scripts/run_results.py --days 14      # a longer look back
"""
from __future__ import annotations
import sys as _sys; from pathlib import Path as _P; _sys.path.insert(0, str(_P(__file__).resolve().parents[1]))  # run from anywhere

import argparse
import datetime as dt

import pandas as pd

from results import coverage, engine
from results import scorecard as sc


def _pct(v) -> str:
    return "n/a" if v is None or pd.isna(v) else f"{v:+.1f}%"


def reaction_line(r) -> str | None:
    """'reacted +4.2% (1.9 ATR), +3.1% vs index; 5d +6.0%' -- None before prices."""
    if pd.isna(r.get("day_pct", float("nan"))):
        return None
    out = f"reacted {_pct(r['day_pct'])}"
    if not pd.isna(r.get("day_atr", float("nan"))):
        out += f" ({r['day_atr']:+.1f} ATR)"
    if not pd.isna(r.get("day_excess_pct", float("nan"))):
        out += f", {_pct(r['day_excess_pct'])} vs index"
    if not pd.isna(r.get("d5_pct", float("nan"))):
        out += f"; 5 sessions {_pct(r['d5_pct'])}"
    return out


def card_lines(r) -> list[str]:
    when = r["broadcast"].strftime("%d %b %H:%M") if not pd.isna(r.get("broadcast")) else "?"
    head = (f"{r['symbol']:<12} {r['quarter']}  {r['verdict'].upper():<8} "
            f"({int(r['points']):+d})  filed {when}   [{r.get('sources', '')}]")
    out = [head, f"{'':14}{sc.headline(r)}"]
    if isinstance(r.get("flags"), str) and r["flags"]:
        out.append(f"{'':14}! {r['flags']}")
    react = reaction_line(r)
    if react:
        out.append(f"{'':14}{react}")
    return out


def report(status: pd.DataFrame, upcoming: dict, today: dt.date, days: int) -> str:
    want = engine.expected_quarter(today)
    lines = [f"QUARTERLY RESULTS -- {today:%a %d %b %Y}  "
             f"({len(status)} stocks followed; reporting quarter ends {want:%d %b %Y})",
             "Verdicts compare each company with its own recent quarters, not analyst "
             "estimates.", ""]
    cutoff = pd.Timestamp(today - dt.timedelta(days=days))
    recent = (status[status["broadcast"].notna() & (status["broadcast"] >= cutoff)]
              if "broadcast" in status else status.iloc[0:0])
    lines.append(f"REPORTED IN THE LAST {days} DAYS ({len(recent)})")
    if recent.empty:
        lines.append("  none")
    for _, r in recent.sort_values("broadcast", ascending=False).iterrows():
        lines += ["  " + x for x in card_lines(r)]
    lines.append("")

    # A board meeting today whose filing is already in drops off the list.
    done = set(status.loc[status["current"], "symbol"])
    soon = sorted(((d, s) for s, d in upcoming.items() if s not in done),
                  key=lambda x: x[0])
    lines.append(f"COMING UP ({len(soon)})")
    if not soon:
        lines.append("  none announced")
    src = dict(zip(status["symbol"], status["sources"]))
    for d, s in soon:
        lines.append(f"  {d:%a %d %b}  {s:<12} [{src.get(s, '')}]")
    lines.append("")

    waiting = status[~status["current"] & ~status["symbol"].isin(upcoming)]
    if not waiting.empty:
        lines.append(f"NOT YET REPORTED, NO DATE ANNOUNCED ({len(waiting)})")
        for _, r in waiting.iterrows():
            last = (r.get("quarter") if isinstance(r.get("quarter"), str)
                    else "no filings found on NSE")
            lines.append(f"  {r['symbol']:<12} last: {last}   [{r['sources']}]")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--symbols", nargs="+", help="instead of the coverage list")
    ap.add_argument("--days", type=int, default=7, help="report filings this recent")
    ap.add_argument("--force", action="store_true", help="re-score filings already on file")
    ap.add_argument("--no-fetch", action="store_true", help="report from what is on file")
    args = ap.parse_args(argv)

    today = dt.date.today()
    cov = ({s.upper(): ["manual"] for s in args.symbols} if args.symbols
           else coverage.symbols(log=print))
    if not args.no_fetch:
        engine.refresh(list(cov), force=args.force)
    engine.rescore()
    engine.update_reactions(log=print)
    status = engine.status(cov, today)
    print()
    print(report(status, engine.upcoming(list(cov), today), today, args.days))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
