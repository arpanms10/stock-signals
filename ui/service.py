"""Data layer for the dashboard.

Everything here calls the existing framework modules directly -- there is no
API layer and no second process. Streamlit runs Python in-process, so an HTTP
boundary between the UI and the engine would add serialisation, a port, and a
second copy of the logic to keep in sync, in exchange for nothing.

The whole pipeline (200 symbols, indicators, ranking, scoring) takes ~30s, so
results are cached and refreshed on demand rather than recomputed on every
click.
"""
from __future__ import annotations

import datetime as dt
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import fundamentals as fu           # noqa: E402
import instruments as ins           # noqa: E402
import portfolio as pf              # noqa: E402
from data import bhavcopy as bc     # noqa: E402
from data import quality as dq      # noqa: E402
from data import store              # noqa: E402
from data.sources import universe as uni  # noqa: E402
from scoring import pipeline, timing      # noqa: E402
from signals import engine as sig         # noqa: E402
from strategy import advisor as adv       # noqa: E402
from strategy import buckets as bk        # noqa: E402
from strategy import momentum as mom      # noqa: E402
from strategy import risk_monitor as rm   # noqa: E402


@dataclass
class Snapshot:
    """Everything both dashboard sections need, computed once."""
    as_of: dt.date
    holdings: list[dict] = field(default_factory=list)
    market: list[dict] = field(default_factory=list)
    book_value: float = 0.0
    regime: str = ""
    regime_detail: str = ""
    split: dict = field(default_factory=dict)
    uncovered: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sector_exposure: list[dict] = field(default_factory=list)


def _levels(row, cfg) -> dict:
    """Trigger levels plus the stop and targets that would apply there."""
    lv = sig.entry_levels(row, cfg)
    out = {"blocked": lv["blocked"], "entries": []}
    for l in lv["levels"]:
        out["entries"].append({
            "rule": l["rule"], "price": l["price"], "pct_away": l["pct_away"],
            "stop": l["stop"], "t1": l.get("t1"), "t2": l.get("t2"),
            "note": l["note"],
        })
    return out


def build(universe: str = "nifty200", universe_size: int = 200) -> Snapshot:
    """Compute the full picture. Slow; the caller caches it."""
    cfg = timing.load_config()
    con = store.connect()
    mkt = bc.connect(str(ROOT / "data" / "market.db"))

    holdings = {h["tradingsymbol"]: h for h in pf.load_holdings()}
    excluded = ins.load_excluded()
    try:
        u = uni.nifty500()
        sectors = dict(zip(u["symbol"], u["sector"]))
    except Exception:
        sectors = {}

    days = sorted(bc.have_days(mkt))
    as_of = dt.date.fromisoformat(days[-1]) if days else dt.date.today()
    pool = bc.universe_on(mkt, as_of, top_n=universe_size) if days else []
    allsyms = sorted(set(pool) | set(holdings))
    cuts = dq.report(con, allsyms) if allsyms else {}

    frames = {}
    for s in allsyms:
        f = pipeline.scored_frame(con, s, cfg, usable_from=cuts.get(s))
        if not f.empty:
            frames[s] = f

    panel = mom.build_panel(frames, cfg)
    ranked = mom.rank_on(panel, max(panel["date"]), cfg) if not panel.empty \
        else pd.DataFrame()
    rank_of = dict(zip(ranked.get("symbol", []), ranked.get("rank", [])))
    mom_of = dict(zip(ranked.get("symbol", []), ranked.get("mom", [])))
    liq = {s: i + 1 for i, s in enumerate(pool)}
    qual = fu.score_all(sorted(set(allsyms)), sectors)

    bench = store.load_index(con, cfg["signals"]["regime_index"])
    detail = rm.regime_note(bench, cfg)
    regime = "RISK-ON" if "RISK-ON" in detail else "RISK-OFF"

    # ---------------------------------------------------------- holdings
    prices, values, tradeable, uncovered = {}, {}, {}, []
    for sym, h in holdings.items():
        if sym in excluded or sym.endswith("-RR"):
            uncovered.append({"symbol": sym, "qty": h.get("quantity"),
                              "why": "fund / ETF / REIT -- not company equity"})
            continue
        if sym not in frames:
            uncovered.append({"symbol": sym, "qty": h.get("quantity"),
                              "why": "no price history on file"})
            continue
        prices[sym] = float(frames[sym]["close"].iloc[-1])
        values[sym] = prices[sym] * float(h.get("quantity") or 0)
        tradeable[sym] = h
    book = sum(values.values())

    sector_val: dict[str, float] = {}
    for s, v in values.items():
        key = sectors.get(s, "Unknown")
        sector_val[key] = sector_val.get(key, 0.0) + v
    cap = cfg["risk"]["max_sector_pct"]
    over = {k for k, v in sector_val.items() if book and 100 * v / book > cap}
    sector_exposure = sorted(
        ({"sector": k, "value": v, "pct": 100 * v / book if book else 0,
          "over_cap": k in over} for k, v in sector_val.items()),
        key=lambda r: -r["value"])

    buckets = {}
    for sym in tradeable:
        q = qual.get(sym)
        vol = bk.realised_vol(frames[sym])
        buckets[sym] = bk.classify(sym, q.score if q else None, vol,
                                   liq.get(sym), tradeable[sym].get("bucket", ""))

    rows, advices = [], []
    for sym, h in tradeable.items():
        q = qual.get(sym)
        risk = rm.holding_status(sym, frames[sym], h, cfg)
        last = frames[sym].iloc[-1]
        a = adv.advise(sym, buckets[sym][0], h, prices[sym], values[sym], book,
                       q, rank_of.get(sym), len(ranked), risk, cfg,
                       sectors.get(sym) in over,
                       row={"sma20": last.get("sma20"), "sma50": last.get("sma50")})
        advices.append(a)
        rows.append({
            "symbol": sym, "bucket": buckets[sym][0],
            "bucket_why": buckets[sym][1], "action": a.action,
            "urgency": a.urgency, "qty_action": a.qty, "timing": a.timing,
            "tranches": a.tranches, "price": prices[sym], "value": values[sym],
            "pct_of_book": 100 * values[sym] / book if book else 0,
            "quantity": float(h.get("quantity") or 0),
            "avg_price": float(h.get("average_price") or 0),
            "lt_qty": h.get("lt_quantity"), "st_qty": h.get("st_quantity"),
            "pnl_pct": a.pnl_pct, "quality": q.score if q else None,
            "flags": q.flags if q else [], "rank": rank_of.get(sym),
            "momentum": mom_of.get(sym), "sector": sectors.get(sym, "Unknown"),
            "stop": risk.get("stop"), "stop_breached": risk.get("stop_breached"),
            "t1": risk.get("t1"), "t2": risk.get("t2"),
            "t1_hit": risk.get("t1_hit"), "t2_hit": risk.get("t2_hit"),
            "add_stop": risk.get("add_stop"), "add_t1": risk.get("add_t1"),
            "add_t2": risk.get("add_t2"),
            "timing_score": None if pd.isna(last.get("timing_score"))
            else float(last["timing_score"]),
            "sma20": last.get("sma20"), "sma50": last.get("sma50"),
            "sma200": last.get("sma200"), "rsi": last.get("rsi14"),
            "reasons": a.reasons, "tax_note": a.tax_note,
            "estimated_tax": a.estimated_tax, "realised_gain": a.realised_gain,
            "tax_saved": a.tax_saved,
            "drawdown": risk.get("drawdown_from_peak"),
        })

    warnings = adv.sanity_warnings(advices, len(ranked), universe_size)
    split = bk.suggest_split(tradeable, values, {k: v[0] for k, v in buckets.items()})

    # ------------------------------------------------------------ market
    market = []
    n_hold = cfg["momentum_strategy"]["n_hold"]
    for r in ranked.itertuples():
        sym = r.symbol
        f = frames.get(sym)
        if f is None:
            continue
        last = f.iloc[-1]
        q = qual.get(sym)
        market.append({
            "rank": int(r.rank), "symbol": sym, "price": float(last["close"]),
            "momentum": float(r.mom), "vol": 100 * float(r.vol),
            "ram": float(r.ram),
            "timing_score": None if pd.isna(last.get("timing_score"))
            else float(last["timing_score"]),
            "quality": q.score if q else None,
            "flags": q.flags if q else [],
            "sector": sectors.get(sym, "Unknown"),
            "held": sym in tradeable,
            "in_top": int(r.rank) <= n_hold,
            "rsi": last.get("rsi14"), "sma50": last.get("sma50"),
            "sma200": last.get("sma200"), "atr": last.get("atr14"),
            "levels": _levels(last, cfg),
        })

    return Snapshot(as_of=as_of, holdings=rows, market=market, book_value=book,
                    regime=regime, regime_detail=detail, split=split,
                    uncovered=uncovered, warnings=warnings,
                    sector_exposure=sector_exposure)


# ------------------------------------------------------------------ scripts

SCRIPTS = {
    "Refresh prices (watchlist + holdings)": ["run_backfill.py", "--years", "10"],
    "Refresh fundamentals (NSE + Yahoo)": ["fetch_fundamentals.py"],
    "Update full-market data": ["run_market_ingest.py", "--years", "10"],
    "Suggest buckets": ["set_buckets.py"],
}


def run_script(args: list[str]):
    """Run one of the framework scripts, streaming its output.

    A subprocess rather than an in-process call, because these are long and
    chatty: streaming stdout gives real progress, and a hung network fetch
    cannot take the dashboard down with it.
    """
    env_cmd = [sys.executable, *args]
    proc = subprocess.Popen(env_cmd, cwd=str(ROOT), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1,
                            env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"})
    for line in proc.stdout:
        yield line.rstrip()
    proc.wait()
    yield f"\n[exit code {proc.returncode}]"
