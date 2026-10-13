"""The permanent record of every swing scan, as plain CSV in git.

What the Swing tab suggested on a day cannot be rebuilt later: the code, the
config and even the index membership will have moved on, and rebuilding old
days with new code measures the new code. So each scan is written down once,
in full, and never edited:

  history/swing/runs.csv          one row per (price_date, universe): market
                                  backdrop, NIFTY 500 close, code commit and
                                  the exact `swing:` settings used
  history/swing/scans/YYYY-MM.csv every ELIGIBLE stock in that scan, not only
                                  the top 10: rank, setup, score and each
                                  component, the raw inputs, and the plan
                                  (entry, stop, targets) as shown
  history/swing/members.csv       the universe's members, written only when
                                  they change -- a point-in-time universe, so
                                  future backtests are free of survivorship

Facts only. Outcomes (triggered? closed? R?) are not stored: they follow from
prices and the recorded plan, so they are recomputed whenever needed (see
outcomes() / replay()). That also means a future exit rule can be replayed
over the same recorded entries.

Append-only: a (price_date, universe) already recorded is left alone; only
`force` replaces it, and only that scan's rows.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent
STORE = ROOT / "history" / "swing"

RUN_COLUMNS = ["price_date", "universe", "recorded_at", "commit", "dirty",
               "config_hash", "regime", "index_vs_200_pct", "breadth_pct",
               "benchmark_close", "results_checked", "universe_size", "priced",
               "n_eligible", "n_suggested", "members_hash", "config_json"]

# The scan rows. Grouped: identity, verdict, score, plan, raw inputs.
SCAN_COLUMNS = [
    "price_date", "universe", "symbol", "rank", "suggest", "setup", "results_on",
    "sector",
    "score", "sc_trend", "sc_rs", "sc_setup", "sc_volume", "sc_risk",
    "entry", "stop", "stop_pct", "t1", "t2",
    # On the scan date the latest bar is unadjusted -- adjustments only ever
    # apply backwards from a later ex-date -- so these are the prices of the day.
    "open", "high", "low", "close", "volume", "turnover_cr",
    "sma50", "sma200", "sma200_slope", "ema_pb", "atr14", "atr_pct", "adx14",
    "rsi14", "vol_ratio", "ud_vol_ratio", "rs_raw", "rs_pct", "high_52w",
    "from_high_pct", "base_pct", "pb_touch_atr", "close_loc",
    "setup_pullback", "setup_breakout",
]
MEMBER_COLUMNS = ["since", "universe", "members_hash", "symbol"]


def paths(root: Path = STORE) -> dict[str, Path]:
    return {"runs": root / "runs.csv", "members": root / "members.csv",
            "scans": root / "scans"}


def _git() -> tuple[str | None, bool | None]:
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, timeout=10).stdout.strip()
        # Only code and config count as "dirty"; the history files themselves
        # change on every run.
        st = subprocess.run(["git", "status", "--porcelain", "--", "*.py", "config/*.yaml"],
                            cwd=ROOT, capture_output=True, text=True, timeout=10).stdout
        return head or None, bool(st.strip())
    except (OSError, subprocess.SubprocessError):
        return None, None


def config_hash(swing_cfg: dict) -> str:
    blob = json.dumps(swing_cfg, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:10]


def members_hash(symbols) -> str:
    return hashlib.sha1(",".join(sorted(symbols)).encode()).hexdigest()[:10]


# ------------------------------------------------------------------- reading

def runs(root: Path = STORE) -> pd.DataFrame:
    p = paths(root)["runs"]
    if not p.exists():
        return pd.DataFrame(columns=RUN_COLUMNS)
    df = pd.read_csv(p, dtype={"commit": str, "config_hash": str, "members_hash": str})
    df["price_date"] = pd.to_datetime(df["price_date"]).dt.date
    return df


def scans(root: Path = STORE) -> pd.DataFrame:
    files = sorted(paths(root)["scans"].glob("*.csv"))
    if not files:
        return pd.DataFrame(columns=SCAN_COLUMNS)
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    df["price_date"] = pd.to_datetime(df["price_date"]).dt.date
    df["results_on"] = pd.to_datetime(df["results_on"]).dt.date
    df["setup"] = df["setup"].fillna("")
    for c in ("suggest", "setup_pullback", "setup_breakout"):
        df[c] = df[c].astype(bool)
    return df


def members(root: Path = STORE) -> pd.DataFrame:
    p = paths(root)["members"]
    if not p.exists():
        return pd.DataFrame(columns=MEMBER_COLUMNS)
    df = pd.read_csv(p, dtype={"members_hash": str})
    df["since"] = pd.to_datetime(df["since"]).dt.date
    return df


def members_on(day: dt.date, universe: str, root: Path = STORE) -> list[str]:
    """The universe as recorded on `day` (the latest list on or before it)."""
    m = members(root)
    m = m[(m["universe"] == universe) & (m["since"] <= day)]
    if m.empty:
        return []
    return sorted(m[m["since"] == m["since"].max()]["symbol"])


def has(price_date: dt.date, universe: str, root: Path = STORE) -> bool:
    r = runs(root)
    return bool(((r["price_date"] == price_date) & (r["universe"] == universe)).any())


def run_config(row) -> dict:
    """The full config with the `swing:` section exactly as that scan used it."""
    from scoring import timing
    cfg = timing.load_config()
    cfg["swing"] = json.loads(row["config_json"])
    return cfg


# ------------------------------------------------------------------- writing

def _round(df: pd.DataFrame, nd: int = 6) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_float_dtype(out[c]):
            out[c] = out[c].round(nd)
    return out


def _append(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, mode="a", header=not path.exists(), index=False)


def _drop(path: Path, price_date: dt.date, universe: str, key: str = "price_date") -> None:
    if not path.exists():
        return
    df = pd.read_csv(path, dtype=str)
    keep = ~((df[key] == price_date.isoformat()) & (df["universe"] == universe))
    df[keep].to_csv(path, index=False)


def save_scan(view, cfg: dict, root: Path = STORE, force: bool = False,
              benchmark_close: float | None = None,
              now: dt.datetime | None = None) -> int:
    """Write one scan. Returns the rows written; 0 if already recorded."""
    if view.as_of is None or view.table is None or view.table.empty:
        return 0
    p = paths(root)
    if has(view.as_of, view.universe, root):
        if not force:
            return 0
        _drop(p["runs"], view.as_of, view.universe)
        _drop(p["scans"] / f"{view.as_of:%Y-%m}.csv", view.as_of, view.universe)

    t = view.table.copy()
    t["price_date"], t["universe"] = view.as_of, view.universe
    for c in SCAN_COLUMNS:
        if c not in t:
            t[c] = None
    _append(p["scans"] / f"{view.as_of:%Y-%m}.csv", _round(t[SCAN_COLUMNS]))

    member_list = sorted(view.sectors) if view.sectors else []
    mh = members_hash(member_list)
    m = members(root)
    last = m[m["universe"] == view.universe]
    if member_list and (last.empty or last.sort_values("since")["members_hash"].iloc[-1] != mh):
        _append(p["members"], pd.DataFrame({"since": view.as_of, "universe": view.universe,
                                            "members_hash": mh, "symbol": member_list}))

    commit, dirty = _git()
    reg = view.regime
    swing_cfg = cfg["swing"]
    run = {"price_date": view.as_of, "universe": view.universe,
           "recorded_at": (now or dt.datetime.now()).isoformat(timespec="seconds"),
           "commit": commit, "dirty": dirty, "config_hash": config_hash(swing_cfg),
           "regime": reg.state if reg else None,
           "index_vs_200_pct": round(reg.index_vs_200_pct, 2) if reg and reg.index_vs_200_pct is not None else None,
           "breadth_pct": round(reg.breadth_pct, 1) if reg and reg.breadth_pct is not None else None,
           "benchmark_close": benchmark_close,
           "results_checked": bool(view.results_checked),
           "universe_size": view.universe_size, "priced": view.priced,
           "n_eligible": len(t), "n_suggested": int(t["suggest"].sum()),
           "members_hash": mh,
           "config_json": json.dumps(swing_cfg, sort_keys=True, default=str)}
    _append(p["runs"], pd.DataFrame([run], columns=RUN_COLUMNS))
    return len(t)


# ------------------------------------------------------------------- replay

def _frames(symbols, cfg: dict) -> dict:
    import swing_report as sr
    from data import store
    return sr.load_frames(store.connect(), cfg, sorted(set(symbols)))


def outcomes(rows: pd.DataFrame, frames: dict | None = None, root: Path = STORE) -> pd.DataFrame:
    """Play each recorded row forward with the plan of the scan it came from.

    Adds outcome, entry_date, fill, exit_date, exit_reason, sessions, r_net,
    ret_pct, checked_through. Nothing is written back: this is derived data."""
    import record_swing as rs
    if rows.empty:
        return rows.assign(**{c: None for c in rs.OUTCOME_COLUMNS})
    r = runs(root).set_index(["price_date", "universe"])
    cfgs = {k: run_config(v) for k, v in r.iterrows()}
    if frames is None:
        frames = _frames(rows["symbol"], next(iter(cfgs.values())))
    out = []
    for row in rows.to_dict("records"):
        f = frames.get(row["symbol"])
        cfg = cfgs.get((row["price_date"], row["universe"]))
        res = rs.settle(row, f, cfg) if f is not None and cfg is not None else {}
        out.append({c: res.get(c) for c in rs.OUTCOME_COLUMNS})
    o = pd.DataFrame(out, index=rows.index)
    return pd.concat([rows, o], axis=1)


def replay(root: Path = STORE, frames: dict | None = None) -> dict:
    """The swing backtest's comparisons, over the recorded scans only.

    Every eligible name, every setup, and the top N actually suggested, each
    played forward with its own scan's plan. Unlike run_swing.py --backtest
    this uses what the scan saw on the day, with the universe as it was."""
    from backtest import swing_backtest as sb
    s = scans(root)
    if s.empty:
        return {"error": "nothing recorded yet"}
    s = outcomes(s, frames, root)
    closed = s[s["outcome"] == "closed"].copy()
    closed["r_net"] = closed["r_net"].astype(float)
    closed["ret_pct"] = closed["ret_pct"].astype(float)
    closed["reason"] = closed["exit_reason"]
    closed["sessions"] = closed["sessions"].astype(float)
    r = runs(root)
    top_n = {(d, u): json.loads(c)["top_n"] for d, u, c in
             zip(r["price_date"], r["universe"], r["config_json"])}
    is_top = [bool(sug) and rk <= top_n.get((d, u), 10) for sug, rk, d, u in
              zip(closed["suggest"], closed["rank"], closed["price_date"], closed["universe"])]
    setup = closed["setup"] != ""
    groups = {"all eligible": closed, "any setup": closed[setup],
              "top suggested": closed[is_top]}
    counts = s["outcome"].value_counts().to_dict()
    return {"scans": int(len(r)), "dates": f"{min(r['price_date'])} to {max(r['price_date'])}",
            "rows": int(len(s)), "outcomes": counts,
            "groups": {k: sb.summarise(v) for k, v in groups.items()}}
