"""Swing scan for the Nifty 200 / 500: load, score, plan.

Shared by scripts/run_swing.py (terminal) and the dashboard's Swing tab, so both
show the same numbers.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from data import store
from data.sources import universe as uni
from scoring import pipeline, timing
from strategy import swing as sw

ROOT = Path(__file__).resolve().parents[1]
CALIBRATION_PATH = ROOT / "config" / "swing_calibration.json"
UNIVERSES = ("nifty200", "nifty500")


@dataclass
class SwingView:
    universe: str
    as_of: dt.date | None
    table: pd.DataFrame                  # scan(): suggestions first, then the rest
    regime: sw.Regime | None
    universe_size: int = 0
    priced: int = 0
    results_checked: bool = False
    sectors: dict = field(default_factory=dict)


def universe_symbols(universe: str) -> tuple[list[str], dict[str, str]]:
    if universe not in UNIVERSES:
        raise ValueError(f"universe must be one of {UNIVERSES}")
    u = getattr(uni, universe)()
    return list(u["symbol"]), dict(zip(u["symbol"], u["sector"]))


def load_frames(con, cfg: dict, symbols: list[str], verbose: bool = False) -> dict:
    """Indicator frames for each symbol, cut at unexplained price gaps."""
    import contextlib
    import io
    buf = io.StringIO()
    # data.quality prints every truncation; useful in a CLI, noise in the UI.
    with (contextlib.nullcontext() if verbose else contextlib.redirect_stdout(buf)):
        frames = pipeline.scored_frames(con, symbols, cfg)
    return {s: f.reset_index(drop=True) for s, f in frames.items()}


def results_calendar(today: dt.date, days: int) -> dict[str, list[dt.date]] | None:
    """Upcoming results dates from NSE; None if NSE cannot be reached, so the
    caller can say the check was skipped rather than imply it passed."""
    if not days:
        return None
    try:
        from data.sources import nse_events
        return nse_events.calendar(today, today + dt.timedelta(days=days + 3))
    except Exception:                    # noqa: BLE001 -- offline is fine
        return None


def historical_results() -> dict[str, list[dt.date]]:
    """Results dates stored by the F&O ingest (2024 onward), for the backtest."""
    path = ROOT / "data" / "fo_history.db"
    if not path.exists():
        return {}
    con = sqlite3.connect(path)
    try:
        rows = con.execute("SELECT symbol, date FROM fo_results").fetchall()
    except sqlite3.Error:
        return {}
    finally:
        con.close()
    out: dict[str, list[dt.date]] = {}
    for s, d in rows:
        out.setdefault(s.upper(), []).append(dt.date.fromisoformat(d))
    return out


def load(universe: str | None = None, cfg: dict | None = None,
         check_results: bool = True, verbose: bool = False) -> SwingView:
    cfg = cfg or timing.load_config()
    universe = universe or cfg["swing"]["universe"]
    symbols, sectors = universe_symbols(universe)
    con = store.connect()
    frames = load_frames(con, cfg, symbols, verbose)
    # The last 300 sessions are plenty for today's scan and the breadth reading.
    start = dt.date.today() - dt.timedelta(days=500)
    panel = sw.build_panel(frames, cfg, start=start)
    if panel.empty:
        return SwingView(universe, None, pd.DataFrame(), None, len(symbols), 0)
    as_of = max(panel["date"])
    today = panel[panel["date"] == as_of]
    results = results_calendar(as_of, cfg["swing"].get("results_within_days", 0)) \
        if check_results else None
    table = sw.scan(panel, as_of, cfg, results)
    if not table.empty:
        table["sector"] = table["symbol"].map(sectors).fillna("Unknown")
    bench = store.load_index(con, cfg["signals"]["regime_index"])
    return SwingView(universe, as_of, table, sw.regime(bench, today, cfg),
                     len(symbols), int(today["symbol"].nunique()),
                     results is not None, sectors)


def calibration() -> dict | None:
    """The latest backtest summary written by scripts/run_swing.py --backtest."""
    if not CALIBRATION_PATH.exists():
        return None
    try:
        return json.loads(CALIBRATION_PATH.read_text())
    except (OSError, ValueError):
        return None


def suggestion_rows(table: pd.DataFrame, capital: float, cfg: dict) -> pd.DataFrame:
    """The columns a person needs to place the trade, with position size."""
    if table.empty:
        return table
    t = table.copy()
    sizes = [sw.position_size(e, s, capital, cfg) for e, s in zip(t["entry"], t["stop"])]
    t["qty"] = [z["qty"] for z in sizes]
    t["position"] = [z["value"] for z in sizes]
    t["risk_amt"] = [z["risk_amt"] for z in sizes]
    t["status"] = [
        "suggested" if sug else
        (f"results {r:%d-%b}" if r is not None and r == r else "no setup yet")
        for sug, r in zip(t["suggest"], t["results_on"])]
    return t
