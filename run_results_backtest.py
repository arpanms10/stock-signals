"""Does the results verdict predict anything? Post-earnings drift, tested.

The question: after a company files, and after the market has had its first
reaction, does a "strong" verdict go on to beat a "weak" one? If it does, the
verdict is worth acting on; if not, it is a reading aid and nothing more.

  1. Fetch 16 quarters of filings for the Nifty 200 (cached by URL, so a rerun
     only fetches what is new). About 3,000 filings the first time: an hour.
  2. Score every quarter as it would have been scored on the day: on its own
     filing and the seven before it, each as originally filed.
  3. For each, enter at the close of the reaction day -- the first close after
     the filing, so both the numbers and the market's first read are known --
     and measure the return over 20 and 60 sessions, less the NIFTY 500.
  4. Compare verdicts, reactions, and the two together.

Results season clusters events in a few weeks per quarter, so events are not
independent. The headline test averages the strong-minus-weak spread within
each quarter and tests across quarters, not across events.

Known biases, stated rather than hidden: today's Nifty 200 (survivors), and
the latest revision of each filing (rare, usually small).

    PYTHONPATH=. .venv/bin/python run_results_backtest.py               # fetch + report
    PYTHONPATH=. .venv/bin/python run_results_backtest.py --fetch-only
    PYTHONPATH=. .venv/bin/python run_results_backtest.py --report-only

Writes data/results/backtest_events.csv and config/results_calibration.json,
which the Results tab reads.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from results import engine, store
from results import filings as rf
from results import reaction as rx

ROOT = Path(__file__).resolve().parent
QUARTERS = store.DIR / "backtest_quarters.csv"
EVENTS = store.DIR / "backtest_events.csv"
CALIBRATION = ROOT / "config" / "results_calibration.json"
HORIZONS = (20, 60)


def fetch(symbols: list[str], quarters: int, log=print) -> pd.DataFrame:
    """History for every symbol; saved as it goes so an interrupted run
    keeps what it fetched (the XBRL cache keeps the rest)."""
    rows, failed = [], []
    for i, sym in enumerate(symbols, 1):
        try:
            hist = rf.history(sym, quarters)
        except Exception as exc:
            failed.append(sym)
            log(f"[{i}/{len(symbols)}] {sym}: failed ({exc})")
            continue
        rows.extend(hist)
        log(f"[{i}/{len(symbols)}] {sym}: {len(hist)} quarters")
        if i % 20 == 0:
            store.save_quarters(rows, QUARTERS)
        time.sleep(0.2)
    df = store.save_quarters(rows, QUARTERS)
    if failed:
        log(f"failed: {', '.join(failed)}")
    return df


def forward(prices: pd.DataFrame, index: pd.Series, d0: dt.date) -> dict:
    """Return from the reaction-day close over each horizon, and vs the index."""
    p = prices.set_index("date")["close"]
    dates = list(p.index)
    if d0 not in p.index:
        return {}
    i = dates.index(d0)
    out = {}
    for h in HORIZONS:
        if i + h >= len(dates):
            continue
        end = dates[i + h]
        r = 100 * (p[end] / p[d0] - 1)
        out[f"ret_{h}"] = round(r, 2)
        if d0 in index.index and end in index.index:
            out[f"excess_{h}"] = round(r - 100 * (index[end] / index[d0] - 1), 2)
    return out


def events(quarters: pd.DataFrame, cfg: dict, log=print) -> pd.DataFrame:
    from data import store as price_store
    from scoring import timing

    cards = pd.DataFrame(engine.cards_from_quarters(quarters, cfg))
    # Only cards that could be judged: a year-ago quarter on file.
    judged = cards["revenue_yoy"].notna() | cards.get("nii_yoy", pd.Series(dtype=float)).notna()
    cards = cards[judged & cards["broadcast"].notna()]
    con = price_store.connect()
    idx_df = price_store.load_index(con, timing.load_config()["signals"]["regime_index"])
    index = idx_df.set_index("date")["close"]
    out = []
    for sym, g in cards.groupby("symbol"):
        prices = price_store.load_prices(con, sym)
        if prices.empty:
            continue
        for card in g.to_dict("records"):
            m = rx.measure(prices, pd.Timestamp(card["broadcast"]).to_pydatetime(),
                           idx_df, cfg.get("reaction_atr", 1.0))
            if not m:
                continue
            out.append({**card, **m, **forward(prices, index, m["reaction_date"])})
    df = pd.DataFrame(out)
    log(f"{len(df)} events with prices, {df['symbol'].nunique()} stocks, "
        f"{df['to_date'].nunique()} quarters")
    return df


def _t(x: pd.Series) -> float | None:
    x = x.dropna()
    if len(x) < 3 or x.std(ddof=1) == 0:
        return None
    return round(float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x)))), 2)


def group(df: pd.DataFrame, by: str | list[str], col: str) -> pd.DataFrame:
    g = df.dropna(subset=[col]).groupby(by)[col]
    return pd.DataFrame({"n": g.size(), "mean": g.mean().round(2),
                         "median": g.median().round(2),
                         "hit_pct": g.apply(lambda s: round(100 * (s > 0).mean(), 1))})


def spread_by_quarter(df: pd.DataFrame, col: str, hi: str = "strong",
                      lo: str = "weak", key: str = "verdict") -> pd.Series:
    """Mean(hi) - mean(lo) within each quarter that has both."""
    out = {}
    for q, g in df.dropna(subset=[col]).groupby("to_date"):
        a, b = g.loc[g[key] == hi, col], g.loc[g[key] == lo, col]
        if len(a) >= 3 and len(b) >= 3:
            out[q] = a.mean() - b.mean()
    return pd.Series(out, dtype=float)


def summarise(df: pd.DataFrame) -> dict:
    out: dict = {"tested_on": dt.date.today().isoformat(), "events": len(df),
                 "stocks": int(df["symbol"].nunique()),
                 "quarters": [str(q) for q in sorted(df["to_date"].unique())],
                 "horizons": {}}
    for h in HORIZONS:
        col = f"excess_{h}"
        s = spread_by_quarter(df, col)
        r = spread_by_quarter(df, col, "rallied", "sold off", "reaction")
        out["horizons"][str(h)] = {
            "by_verdict": group(df, "verdict", col).reset_index().to_dict("records"),
            "by_reaction": group(df, "reaction", col).reset_index().to_dict("records"),
            "verdict_spread_mean": round(float(s.mean()), 2) if len(s) else None,
            "verdict_spread_t": _t(s), "verdict_spread_quarters": len(s),
            "verdict_spread_positive_quarters": int((s > 0).sum()),
            "reaction_spread_mean": round(float(r.mean()), 2) if len(r) else None,
            "reaction_spread_t": _t(r), "reaction_spread_quarters": len(r),
        }
    # Does the verdict even agree with the market's first read? If not, the
    # scorecard is measuring something the market does not care about.
    out["day0_by_verdict"] = group(df, "verdict", "day_excess_pct").reset_index() \
        .to_dict("records")
    h = out["horizons"][str(HORIZONS[-1])]
    t = h["verdict_spread_t"]
    out["verdict_predicts"] = bool(t is not None and t >= 2.0
                                   and (h["verdict_spread_mean"] or 0) > 0)
    return out


def report(df: pd.DataFrame, summary: dict) -> str:
    lines = [f"POST-RESULTS DRIFT -- {summary['events']} filings, {summary['stocks']} "
             f"stocks, {len(summary['quarters'])} quarters "
             f"({summary['quarters'][0]} to {summary['quarters'][-1]})",
             "Entry at the reaction-day close; returns less the NIFTY 500.", ""]
    lines.append("Reaction day, by verdict (does the market agree with the card?)")
    lines.append(group(df, "verdict", "day_excess_pct").to_string())
    for h in HORIZONS:
        col = f"excess_{h}"
        s = summary["horizons"][str(h)]
        lines += ["", f"--- {h} sessions after the reaction day ---",
                  "By verdict:", group(df, "verdict", col).to_string(),
                  "By reaction:", group(df, "reaction", col).to_string(),
                  "By verdict and reaction:",
                  group(df, ["verdict", "reaction"], col).to_string(),
                  f"Strong minus weak, averaged per quarter: "
                  f"{s['verdict_spread_mean']}% (t={s['verdict_spread_t']}, "
                  f"positive in {s['verdict_spread_positive_quarters']} of "
                  f"{s['verdict_spread_quarters']} quarters)",
                  f"Rallied minus sold off, per quarter: {s['reaction_spread_mean']}% "
                  f"(t={s['reaction_spread_t']}, {s['reaction_spread_quarters']} quarters)"]
    lines += ["", "VERDICT: " + (
        "the verdict has predicted 60-session excess returns (t >= 2 across quarters)."
        if summary["verdict_predicts"] else
        "no reliable edge -- keep the verdict as a reading aid; it should not rank.")]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--universe", default="nifty200", choices=["nifty200", "nifty500"])
    ap.add_argument("--quarters", type=int, default=16)
    ap.add_argument("--fetch-only", action="store_true")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="first N symbols (testing)")
    args = ap.parse_args(argv)

    cfg = engine.load_config()
    if not args.report_only:
        from data.sources import universe as uni
        syms = getattr(uni, args.universe)()["symbol"].astype(str).tolist()
        fetch(syms[: args.limit] if args.limit else syms, args.quarters)
        if args.fetch_only:
            return 0
    q = store.quarters(QUARTERS)
    if q.empty:
        print("No backtest quarters on file; run without --report-only first.",
              file=sys.stderr)
        return 1
    df = events(q, cfg)
    if df.empty:
        print("No events with prices.", file=sys.stderr)
        return 1
    EVENTS.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(EVENTS, index=False)
    summary = summarise(df)
    CALIBRATION.write_text(json.dumps(summary, indent=1, default=str))
    print(report(df, summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
