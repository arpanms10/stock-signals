"""Record the day's top swing picks, and score earlier ones as they play out.

The swing backtest says what the plan WOULD have done; this file says what
the Swing tab actually suggested, on the day, at the levels shown. Each run:

  1. appends the scan's top picks (default 10) to the "Picks" sheet -- once
     per price date and universe; a later run on the same data does nothing
     unless --force replaces those rows;
  2. plays every recorded pick that is not yet settled forward through the
     prices since, with the plan as recorded (entry, stop, 1.5R / 3R, time
     limit), and fills in its outcome: not triggered, open, or closed with
     the exit reason and the result in R and % after costs;
  3. rewrites the "Summary" sheet: settled trades and their average R.

Writes history/swing_picks.xlsx, which is meant to live in git, so nothing
about holdings goes in it.

    PYTHONPATH=. .venv/bin/python record_swing.py
    PYTHONPATH=. .venv/bin/python record_swing.py --universe nifty500 --top 10
    PYTHONPATH=. .venv/bin/python record_swing.py --force      # redo this date
    PYTHONPATH=. .venv/bin/python record_swing.py --update     # outcomes only

Decision support, not investment advice.
"""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

HISTORY = Path(__file__).parent / "history" / "swing_picks.xlsx"

PICK_COLUMNS = ["recorded_on", "price_date", "universe", "regime", "breadth_pct",
                "benchmark_close", "rank", "symbol", "setup", "score", "close",
                "entry", "stop", "stop_pct", "t1", "t2", "sector"]
OUTCOME_COLUMNS = ["outcome", "entry_date", "fill", "exit_date", "exit_reason",
                   "sessions", "r_net", "ret_pct", "checked_through"]
COLUMNS = PICK_COLUMNS + OUTCOME_COLUMNS
SETTLED = {"closed", "not triggered"}


def _num(v, nd: int = 2):
    if v is None or (isinstance(v, float) and v != v):
        return None
    return round(float(v), nd) if nd else int(round(float(v)))


def pick_rows(view, top: int, recorded_on: dt.date, bench_close=None) -> list[dict]:
    """The scan's suggested names, best first, as rows for the sheet."""
    t = view.table
    if t is None or t.empty:
        return []
    t = t[t["suggest"]].head(top)
    reg = view.regime
    base = {"recorded_on": recorded_on, "price_date": view.as_of,
            "universe": view.universe,
            "regime": reg.state if reg else None,
            "breadth_pct": _num(reg.breadth_pct, 1) if reg else None,
            "benchmark_close": _num(bench_close)}
    rows = []
    for i, r in enumerate(t.to_dict("records"), 1):
        rows.append({**base, "rank": i, "symbol": r["symbol"], "setup": r["setup"],
                     "score": _num(r["score"], 1), "close": _num(r["close"]),
                     "entry": _num(r["entry"]), "stop": _num(r["stop"]),
                     "stop_pct": _num(r["stop_pct"]), "t1": _num(r["t1"]),
                     "t2": _num(r["t2"]), "sector": r.get("sector", ""),
                     "outcome": "pending"})
    return rows


# ------------------------------------------------------------------ workbook

def load(path: Path = HISTORY) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_excel(path, sheet_name="Picks")
    for c in COLUMNS:
        if c not in df:
            df[c] = None
    for c in ("recorded_on", "price_date", "entry_date", "exit_date", "checked_through"):
        df[c] = pd.to_datetime(df[c]).dt.date
    # Missing values come back as NaN; the rest of the code expects None.
    return df[COLUMNS].astype(object).where(df[COLUMNS].notna(), None)


def add(df: pd.DataFrame, rows: list[dict], force: bool = False) -> tuple[pd.DataFrame, int]:
    """Append rows for one (price_date, universe). Returns (frame, rows added);
    0 added if that date is already recorded and not forced."""
    if not rows:
        return df, 0
    key = (rows[0]["price_date"], rows[0]["universe"])
    same = (df["price_date"] == key[0]) & (df["universe"] == key[1])
    if same.any():
        if not force:
            return df, 0
        df = df[~same]
    new = pd.DataFrame(rows, columns=COLUMNS).astype(object)
    out = new if df.empty else pd.concat([df, new], ignore_index=True)
    out = out.sort_values(["price_date", "universe", "rank"], kind="stable")
    return out.reset_index(drop=True), len(rows)


def summary(df: pd.DataFrame) -> pd.DataFrame:
    """Settled trades by setup and overall."""
    closed = df[df["outcome"] == "closed"].copy()
    closed["r_net"] = closed["r_net"].astype(float)
    closed["ret_pct"] = closed["ret_pct"].astype(float)
    rows = []
    for name, g in [("all", closed)] + list(closed.groupby("setup")):
        if g.empty:
            continue
        rows.append({"group": name, "trades": len(g),
                     "win_pct": round(100 * float((g["r_net"] > 0).mean()), 1),
                     "avg_r": round(float(g["r_net"].mean()), 3),
                     "avg_ret_pct": round(float(g["ret_pct"].mean()), 2)})
    counts = df["outcome"].value_counts()
    rows.append({"group": "picks recorded", "trades": len(df)})
    for k in ("pending", "open", "not triggered", "closed"):
        rows.append({"group": k, "trades": int(counts.get(k, 0))})
    return pd.DataFrame(rows)


def save(df: pd.DataFrame, path: Path = HISTORY) -> None:
    from openpyxl.styles import Font
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, frame in (("Picks", df), ("Summary", summary(df))):
            frame.to_excel(xw, sheet_name=name, index=False)
            ws = xw.sheets[name]
            for cell in ws[1]:
                cell.font = Font(bold=True)
            ws.freeze_panes = "A2"
            for i, col in enumerate(frame.columns, 1):
                vals = [str(v) for v in frame[col].tolist() if v is not None]
                width = max([len(str(col)), *map(len, vals)] or [8])
                ws.column_dimensions[ws.cell(1, i).column_letter].width = min(width + 2, 30)


# ------------------------------------------------------------------ outcomes

def settle(row: dict, frame: pd.DataFrame, cfg: dict) -> dict:
    """Play one recorded pick forward with the plan it was recorded with."""
    from strategy import swing as sw
    s = cfg["swing"]
    dates = list(frame["date"])
    if row["price_date"] not in dates:
        return {}
    i = dates.index(row["price_date"])
    after = len(dates) - 1 - i
    out = {"checked_through": dates[-1]}
    if after == 0:
        return {**out, "outcome": "pending"}
    o, h, l, c = (frame[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    t = sw.simulate(o, h, l, c, i, float(row["entry"]), float(row["stop"]), cfg)
    if t is None:
        # The buy-stop has a fixed life; until it has passed, it may still fire.
        done = after >= s["entry_valid_sessions"]
        return {**out, "outcome": "not triggered" if done else "pending"}
    trade = {"entry_date": dates[t.entry_idx], "fill": _num(t.entry),
             "sessions": t.sessions, "r_net": _num(t.r_net, 3),
             "ret_pct": _num(t.ret_pct)}
    if t.exit_reason.endswith("end"):
        # Still running: valued at the last close, not a result yet.
        return {**out, **trade, "outcome": "open", "exit_date": None,
                "exit_reason": None}
    return {**out, **trade, "outcome": "closed", "exit_date": dates[t.exit_idx],
            "exit_reason": t.exit_reason}


def update_outcomes(df: pd.DataFrame, cfg: dict, frames: dict | None = None) -> pd.DataFrame:
    """Fill in outcomes for every row not yet settled."""
    todo = df.index[~df["outcome"].isin(SETTLED)]
    if not len(todo):
        return df
    if frames is None:
        frames = _frames(sorted(set(df.loc[todo, "symbol"])), cfg)
    df = df.copy()
    for i in todo:
        f = frames.get(df.at[i, "symbol"])
        if f is None or f.empty:
            continue
        for k, v in settle(df.loc[i].to_dict(), f, cfg).items():
            df.at[i, k] = v
    return df


def _frames(symbols: list[str], cfg: dict) -> dict:
    """Adjusted daily bars, the same frames the scan used."""
    import swing_report as sr
    from data import store
    return sr.load_frames(store.connect(), cfg, symbols)


# ---------------------------------------------------------------------- run

def record(view, cfg: dict, top: int = 10, path: Path = HISTORY,
           force: bool = False, bench_close=None) -> tuple[int, pd.DataFrame]:
    """Append the view's picks, settle what can be settled, save. Used by the
    CLI and the Swing tab's button. Returns (rows added, the whole sheet)."""
    df = load(path)
    df, n = add(df, pick_rows(view, top, dt.date.today(), bench_close), force)
    df = update_outcomes(df, cfg)
    save(df, path)
    return n, df


def benchmark_close(cfg: dict, on: dt.date):
    from data import store
    b = store.load_index(store.connect(), cfg["signals"]["regime_index"])
    b = b[b["date"] <= on] if not b.empty else b
    return float(b["close"].iloc[-1]) if not b.empty else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--universe", choices=("nifty200", "nifty500"), default=None)
    ap.add_argument("--top", type=int, default=None, help="picks to record (default: top_n)")
    ap.add_argument("--force", action="store_true",
                    help="replace this date's record instead of keeping it")
    ap.add_argument("--update", action="store_true",
                    help="only fill in outcomes for recorded picks")
    ap.add_argument("--no-results", action="store_true",
                    help="skip the NSE results-calendar lookup")
    ap.add_argument("--path", type=Path, default=HISTORY)
    args = ap.parse_args()

    from scoring import timing
    cfg = timing.load_config()
    if args.update:
        df = update_outcomes(load(args.path), cfg)
        save(df, args.path)
        print(summary(df).to_string(index=False))
        return

    import swing_report as sr
    view = sr.load(args.universe, cfg, check_results=not args.no_results)
    if view.as_of is None:
        raise SystemExit("No swing scan to record -- refresh prices first.")
    top = args.top or cfg["swing"]["top_n"]
    n, df = record(view, cfg, top, args.path, args.force,
                   benchmark_close(cfg, view.as_of))
    if n:
        print(f"Recorded {n} picks for {view.as_of} ({view.universe}) -> {args.path}")
    else:
        print(f"{view.as_of} ({view.universe}) already recorded, or no setups -- "
              f"kept as it was. --force replaces it.")
    if not view.results_checked:
        print("  (results calendar unavailable -- the results-week filter was skipped)")
    print(summary(df).to_string(index=False))


if __name__ == "__main__":
    main()
