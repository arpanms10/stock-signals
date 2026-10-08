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
import watching as wg               # noqa: E402
from data import bhavcopy as bc     # noqa: E402
from data import freshness as fr    # noqa: E402
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
    freshness: list[dict] = field(default_factory=list)
    watching: list[dict] = field(default_factory=list)
    cash: dict = field(default_factory=dict)
    lookbacks: list[int] = field(default_factory=list)
    # Every name top-n on either lookback, with rank and score on both.
    momentum_table: list[dict] = field(default_factory=list)


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
    equities = bc.require_equity_symbols(mkt)
    # The exclusion file is a human-readable record; the ISIN map is the
    # authority. A holding with a fund ISIN is not equity even if etfs.csv is
    # stale or was never written.
    excluded |= {h for h in holdings if h in bc.non_equity_symbols(mkt)}
    pool = bc.universe_on(mkt, as_of, top_n=universe_size) if days else []
    allsyms = sorted(set(pool) | set(holdings))
    cuts = dq.report(con, allsyms) if allsyms else {}

    frames = {}
    for s in allsyms:
        f = pipeline.scored_frame(con, s, cfg, usable_from=cuts.get(s))
        if not f.empty:
            frames[s] = f

    panel = mom.build_panel(frames, cfg)
    ranked = mom.rank_on(panel, max(panel["date"]), cfg, eligible=equities) \
        if not panel.empty \
        else pd.DataFrame()
    # The comparison lookback (12 months by default), and which names are top-n
    # on both. Shown alongside the primary rank, never instead of it.
    lbs = mom.lookbacks(cfg)
    alt_rank_of: dict[str, int] = {}
    in_both: set[str] = set()
    alt = pd.DataFrame()
    if len(lbs) > 1 and not ranked.empty:
        c = mom.with_lookback(cfg, lbs[1])
        p = mom.build_panel(frames, c)
        alt = mom.rank_on(p, max(p["date"]), c, eligible=equities) \
            if not p.empty else pd.DataFrame()
        alt_rank_of = dict(zip(alt.get("symbol", []), alt.get("rank", [])))
        in_both = set(mom.common_top({lbs[0]: ranked, lbs[1]: alt},
                                     cfg["momentum_strategy"]["n_hold"]))
    rank_of = dict(zip(ranked.get("symbol", []), ranked.get("rank", [])))
    mom_of = dict(zip(ranked.get("symbol", []), ranked.get("mom", [])))
    liq = bc.liquidity_ranks(mkt, as_of) if days else {}
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

    risks = {sym: rm.holding_status(sym, frames[sym], h, cfg)
             for sym, h in tradeable.items()}
    inputs = {}
    for sym, h in tradeable.items():
        last = frames[sym].iloc[-1]
        inputs[sym] = dict(
            symbol=sym, bucket=buckets[sym][0], holding=h, price=prices[sym],
            value=values[sym], book_value=book, quality=qual.get(sym),
            rank=rank_of.get(sym), rank_universe=len(ranked), risk=risks[sym],
            cfg=cfg,
            row={"sma20": last.get("sma20"), "sma50": last.get("sma50")})
    # Sector trims planned after the exits and sizing trims (two passes).
    advised = adv.advise_book(inputs, values, sectors, qual, book, cfg)
    rows, advices = [], []
    for sym, h in tradeable.items():
        q = qual.get(sym)
        risk = risks[sym]
        last = frames[sym].iloc[-1]
        a = advised[sym]
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
            "quality_note": fu.quality_note(q),
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
            "ret_12m": mom.trailing_return(f["close"], 12),
            "ret_1m": mom.trailing_return(f["close"], 1),
            "timing_score": None if pd.isna(last.get("timing_score"))
            else float(last["timing_score"]),
            "quality": q.score if q else None,
            "quality_note": fu.quality_note(q),
            "flags": q.flags if q else [],
            "sector": sectors.get(sym, "Unknown"),
            "held": sym in tradeable,
            "in_top": int(r.rank) <= n_hold,
            "rank_alt": alt_rank_of.get(sym),
            "in_both": sym in in_both,
            "rsi": last.get("rsi14"), "sma50": last.get("sma50"),
            "sma200": last.get("sma200"), "atr": last.get("atr14"),
            "levels": _levels(last, cfg),
        })

    # One consolidated table for the Momentum tab: every name in the top n on
    # either lookback, with its rank AND score on both. Built from the
    # rankings directly rather than `market`, because a name can be top-n on
    # the comparison lookback without appearing in the primary ranking.
    def _scores(rk: pd.DataFrame) -> tuple[dict, dict]:
        if rk.empty:
            return {}, {}
        return (dict(zip(rk["symbol"], rk["rank"])),
                dict(zip(rk["symbol"], rk["ram"])))

    main_rank, main_score = _scores(ranked)
    cmp_rank, cmp_score = _scores(alt)
    vol_of = {**dict(zip(alt.get("symbol", []), alt.get("vol", []))),
              **dict(zip(ranked.get("symbol", []), ranked.get("vol", [])))}
    top = (list(ranked["symbol"].head(n_hold)) if not ranked.empty else []) + \
        [x for x in (alt["symbol"].head(n_hold) if not alt.empty else [])
         if x not in set(ranked["symbol"].head(n_hold))]
    momentum_table = []
    for sym in top:
        f = frames.get(sym)
        q = qual.get(sym)
        r_main, r_cmp = main_rank.get(sym), cmp_rank.get(sym)
        on_main = r_main is not None and r_main <= n_hold
        on_cmp = r_cmp is not None and r_cmp <= n_hold
        ret = (lambda mo: mom.trailing_return(f["close"], mo)) if f is not None \
            else (lambda mo: None)
        momentum_table.append({
            "symbol": sym,
            "status": "Both" if on_main and on_cmp else
                      (f"{lbs[0]}M only" if on_main else f"{lbs[-1]}M only"),
            "rank_main": r_main, "score_main": main_score.get(sym),
            "rank_cmp": r_cmp, "score_cmp": cmp_score.get(sym),
            "price": float(f["close"].iloc[-1]) if f is not None else None,
            "ret_main": ret(lbs[0]), "ret_cmp": ret(lbs[-1]), "ret_1m": ret(1),
            "vol": 100 * float(vol_of[sym]) if sym in vol_of else None,
            "quality": q.score if q else None,
            "quality_note": fu.quality_note(q),
            "sector": sectors.get(sym, "Unknown"),
            "held": sym in holdings,
        })
    # Both first, then by main rank (names absent from it sort last).
    momentum_table.sort(key=lambda r: (r["status"] != "Both",
                                       r["rank_main"] or 10**6,
                                       r["rank_cmp"] or 10**6))

    # Watched names carry their own price history, fetched by run_daily.py.
    watch_prices = {}
    for sym in wg.symbols():
        f = frames.get(sym)
        if f is None:
            f = pipeline.scored_frame(con, sym, cfg, usable_from=cuts.get(sym))
        if f is not None and not f.empty:
            watch_prices[sym] = float(f["close"].iloc[-1])
    wg.fill_missing_baselines(watch_prices)
    watching = wg.report(watch_prices)

    checks = [("market", fr.assess(mkt)), ("prices", fr.assess_prices(con, [])),
              ("fundamentals", fr.assess_fundamentals())]
    freshness = [{"kind": k, "label": c.label, "message": c.message} for k, c in checks]
    cash = adv.apply_cash_constraint(
        advices, prices, qual, 0.0, sectors=sectors, values=values, book=book,
        max_sector_pct=cfg["risk"]["max_sector_pct"],
        min_trade_value=book * cfg["risk"].get("min_trade_pct", 0.0) / 100)
    # apply_cash_constraint can downgrade an ADD to WATCH, so rebuild the rows
    # it touched rather than reporting an action the plan cannot fund.
    by_symbol = {a.symbol: a for a in advices}
    for r in rows:
        a = by_symbol.get(r["symbol"])
        if a:
            r["action"], r["qty_action"] = a.action, a.qty
            r["reasons"] = a.reasons

    return Snapshot(as_of=as_of, holdings=rows, market=market, book_value=book,
                    freshness=freshness, cash=cash, watching=watching,
                    regime=regime, regime_detail=detail, split=split,
                    uncovered=uncovered, warnings=warnings,
                    sector_exposure=sector_exposure, lookbacks=lbs,
                    momentum_table=momentum_table)


# ------------------------------------------------------------------ scripts

SCRIPTS = {
    "Refresh prices (watchlist + holdings)": ["run_backfill.py", "--years", "10"],
    "Refresh fundamentals (NSE + Yahoo)": ["fetch_fundamentals.py"],
    "Update full-market data": ["run_market_ingest.py", "--years", "10"],
    "Suggest buckets": ["set_buckets.py"],
    # F&O: the bhavcopy history behind IV percentile, realised vol and the
    # validation's hit rates and reach curves (rewritten on every run).
    "Refresh F&O history + validation": ["run_fno_backtest.py", "--ingest"],
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


# Which script fixes which stale source -- for the button on the warning.
FIXES = {"prices": ("Refresh now", "Refresh prices (watchlist + holdings)"),
         "market": ("Update now", "Update full-market data"),
         "fundamentals": ("Refresh now", "Refresh fundamentals (NSE + Yahoo)")}


def exit_code(lines: list[str]) -> int | None:
    """The code run_script reports as its last line."""
    import re
    for line in reversed(lines):
        m = re.search(r"\[exit code (-?\d+)\]", line)
        if m:
            return int(m.group(1))
    return None
