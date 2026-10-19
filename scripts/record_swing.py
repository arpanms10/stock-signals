"""Record the day's swing scan, and rebuild history/swing_picks.xlsx from it.

Each run:

  1. writes the full scan to the permanent CSV record (records/swing_store.py:
     history/swing/runs.csv, scans/YYYY-MM.csv, members.csv) -- once per
     price date and universe; --force replaces that one scan;
  2. rebuilds history/swing_picks.xlsx from that record: the top picks of
     every recorded scan (default 10), each played forward through the
     prices since with the plan its scan used -- not triggered, open, or
     closed with the exit reason and the result in R and % after costs --
     plus a Summary sheet.

The workbook is a report, regenerated in full every time; the CSV files are
the record. Nothing about holdings goes in either.

    PYTHONPATH=. .venv/bin/python scripts/record_swing.py                       # Nifty 200
    PYTHONPATH=. .venv/bin/python scripts/record_swing.py --universe nifty500
    PYTHONPATH=. .venv/bin/python scripts/record_swing.py --force               # redo this date
    PYTHONPATH=. .venv/bin/python scripts/record_swing.py --update              # workbook only

Decision support, not investment advice.
"""
from __future__ import annotations
import sys as _sys; from pathlib import Path as _P; _sys.path.insert(0, str(_P(__file__).resolve().parents[1]))  # run from anywhere

import argparse
import datetime as dt
import json
from pathlib import Path

import pandas as pd

HISTORY = Path(__file__).resolve().parents[1] / "history" / "swing_picks.xlsx"

PICK_COLUMNS = ["recorded_on", "price_date", "universe", "regime", "breadth_pct",
                "benchmark_close", "rank", "symbol", "setup", "score", "close",
                "entry", "stop", "stop_pct", "t1", "t2", "sector"]
OUTCOME_COLUMNS = ["outcome", "entry_date", "fill", "exit_date", "exit_reason",
                   "sessions", "r_net", "ret_pct", "checked_through"]
COLUMNS = PICK_COLUMNS + OUTCOME_COLUMNS


def _num(v, nd: int = 2):
    if v is None or (isinstance(v, float) and v != v):
        return None
    return round(float(v), nd) if nd else int(round(float(v)))


# ------------------------------------------------------------------ workbook

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


def picks(root: Path | None = None, frames: dict | None = None) -> pd.DataFrame:
    """The top picks of every recorded scan, with outcomes recomputed."""
    from records import swing_store as ss
    root = root or ss.STORE
    s, r = ss.scans(root), ss.runs(root)
    if s.empty:
        return pd.DataFrame(columns=COLUMNS)
    r = r.assign(top_n=[json.loads(c)["top_n"] for c in r["config_json"]],
                 recorded_on=pd.to_datetime(r["recorded_at"]).dt.date)
    s = s.merge(r[["price_date", "universe", "top_n", "recorded_on", "regime",
                   "breadth_pct", "benchmark_close"]], on=["price_date", "universe"])
    top = s[s["suggest"] & (s["rank"] <= s["top_n"])]
    top = top.sort_values(["price_date", "universe", "rank"]).reset_index(drop=True)
    top = ss.outcomes(top[PICK_COLUMNS], frames, root)
    # The CSV record keeps 6 decimals; the workbook is for reading.
    # Python's round, as _num uses: pandas' .round breaks exact half-paisa
    # ties (entry = high x 1.001) the other way.
    for col, nd in (("score", 1), ("close", 2), ("entry", 2), ("stop", 2),
                    ("stop_pct", 2), ("t1", 2), ("t2", 2)):
        top[col] = [_num(v, nd) for v in top[col]]
    return top[COLUMNS].astype(object).where(top[COLUMNS].notna(), None)


def record(view, cfg: dict, top: int | None = None, path: Path = HISTORY,
           force: bool = False, bench_close=None,
           root: Path | None = None) -> tuple[int, pd.DataFrame]:
    """Write the scan to the record, then rebuild the workbook from it. Used
    by the CLI and the Swing tab's button. Returns (scan rows written, the
    picks sheet). `top` is kept for compatibility; the record's own top_n
    decides the picks, so it cannot drift from what the tab showed."""
    from records import swing_store as ss
    root = root or ss.STORE
    n = ss.save_scan(view, cfg, root, force, bench_close)
    df = picks(root)
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
    ap.add_argument("--force", action="store_true",
                    help="replace this date's recorded scan instead of keeping it")
    ap.add_argument("--update", action="store_true",
                    help="only rebuild the workbook from the record")
    ap.add_argument("--no-results", action="store_true",
                    help="skip the NSE results-calendar lookup")
    ap.add_argument("--path", type=Path, default=HISTORY)
    args = ap.parse_args()

    from scoring import timing
    cfg = timing.load_config()
    if args.update:
        df = picks()
        save(df, args.path)
        print(summary(df).to_string(index=False))
        return

    from records import swing_report as sr
    view = sr.load(args.universe, cfg, check_results=not args.no_results)
    if view.as_of is None:
        raise SystemExit("No swing scan to record -- refresh prices first.")
    n, df = record(view, cfg, path=args.path, force=args.force,
                   bench_close=benchmark_close(cfg, view.as_of))
    if n:
        print(f"Recorded the {view.universe} scan of {view.as_of}: {n} eligible "
              f"names, {int(view.table['suggest'].sum())} with a setup.")
    else:
        print(f"{view.as_of} ({view.universe}) already recorded -- kept as it was. "
              f"--force replaces it.")
    if not view.results_checked:
        print("  (results calendar unavailable -- the results-week filter was skipped)")
    print(f"Workbook rebuilt: {args.path}")
    print(summary(df).to_string(index=False))


if __name__ == "__main__":
    main()
