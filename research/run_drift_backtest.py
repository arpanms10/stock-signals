"""Does post-results reaction drift survive costs? A tradeable rule, tested.

Uses the survivorship-free events from
`research/run_results_backtest.py --report-only --universe pit`
(data/results/backtest_events_pit.csv) and the point-in-time universe
(data/results/pit_universe.csv). See results/drift.py for the rule.

For each hold (20, 40, 60 sessions) and book size (10, 20, 40 slots):
CAGR, worst drawdown, turnover, and the excess over the equal-weight
universe per calendar quarter with its t-statistic across quarters. For the
20-slot book, two random controls with the same entry dates, holds and slots:
a random universe member that traded that day, and a random company that
filed that day whatever its reaction. Reference only: rallied minus sold off
per filing quarter, both legs paying costs (no shorting in practice).

Pre-registered gate: the 20-slot book's quarterly excess over the
equal-weight universe, after costs, t >= 2 at 20 or 60 sessions. Until it
passes, nothing ranks on the reaction.

    PYTHONPATH=. .venv/bin/python research/run_drift_backtest.py
    PYTHONPATH=. .venv/bin/python research/run_drift_backtest.py --draws 100 --ohlc-only

Writes data/results/drift_backtest_pit.json and drift_quarters_pit.csv.
"""
from __future__ import annotations
import sys as _sys; from pathlib import Path as _P; _sys.path.insert(0, str(_P(__file__).resolve().parents[1]))  # run from anywhere

import argparse
import json
import sys

import numpy as np
import pandas as pd

from results import drift, store

EVENTS = store.DIR / "backtest_events_pit.csv"
OUT = store.DIR / "drift_backtest_pit.json"
QUARTERS_OUT = store.DIR / "drift_quarters_pit.csv"
HOLDS = (20, 40, 60)
SLOTS = (10, 20, 40)
GATE_HOLDS, GATE_SLOTS = (20, 60), 20


def load_inputs(ohlc_only: bool, log=print):
    from data import store as price_store
    from results import pit_universe as pu
    from scoring import timing

    ev = pd.read_csv(EVENTS)
    if ohlc_only:
        ev = ev[~ev["close_only"].astype(bool)]
    ev["to_date"] = pd.to_datetime(ev["to_date"]).dt.date
    ev["reaction_date"] = pd.to_datetime(ev["reaction_date"]).dt.date
    members = pu.load()
    con = price_store.connect()
    idx = price_store.load_index(con, timing.load_config()["signals"]["regime_index"])
    first = min(ev["reaction_date"].min(), members["date"].min())
    idx = idx[idx["date"] >= first]
    calendar = list(idx["date"])
    symbols = set(ev["symbol"]) | set(members["symbol"])
    log(f"loading prices for {len(symbols)} symbols ...")
    px, live = drift.price_matrix(symbols, pu.price_loader(con), calendar)
    member = drift.membership(members, calendar, px.columns)
    return ev, members, calendar, idx["close"].to_numpy(dtype=float), px, live, member


def control_pools(kind: str, trades: pd.DataFrame, ev: pd.DataFrame, calendar, live,
                  member, col: dict) -> dict[int, np.ndarray]:
    """Per entry day, the symbols a random control may draw: universe members
    that traded that day, or that day's filers whatever their reaction."""
    pools: dict[int, np.ndarray] = {}
    for i0 in trades["i0"].unique():
        if kind == "member":
            ok = member.iloc[i0].to_numpy() & live.iloc[i0].to_numpy()
            pools[i0] = member.columns[ok].to_numpy()
        else:
            day = ev.loc[ev["reaction_date"] == calendar[i0], "symbol"].unique()
            pools[i0] = np.array([s for s in day if s in col and live.iat[i0, col[s]]])
    return pools


def draw(trades: pd.DataFrame, pools: dict, rng) -> pd.DataFrame:
    """The same trades with each symbol swapped for a random one that day."""
    out = trades.copy()
    out["symbol"] = [rng.choice(pools[i]) if len(pools[i]) else s
                     for s, i in zip(trades["symbol"], trades["i0"])]
    return out


def book_stats(nav: pd.Series, held, res: dict, ew_q: pd.Series) -> dict:
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    q = drift.quarterly(nav)
    ex = (q - ew_q.reindex(q.index)).dropna()
    return {"cagr": drift.cagr(nav), "max_drawdown": drift.max_drawdown(nav),
            "turnover_x_per_year": round(res["bought"] / float(nav.mean()) / years, 1),
            "buys": res["buys"], "skipped": res["skipped"],
            "avg_positions": round(float(np.mean(held)), 1),
            "excess_q_mean": round(float(ex.mean()), 2), "excess_q_t": drift.t_stat(ex),
            "excess_q_positive": int((ex > 0).sum()), "quarters": len(ex)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--draws", type=int, default=500, help="random-control draws")
    ap.add_argument("--ohlc-only", action="store_true",
                    help="drop filings priced from closes only")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)
    if not EVENTS.exists():
        print("No point-in-time events. Run research/run_results_backtest.py --report-only "
              "--universe pit first.", file=sys.stderr)
        return 1
    from picker import engine as pe
    cost = pe.load_config()["round_trip_cost_pct"]
    ev, members, calendar, park, px, live, member = load_inputs(args.ohlc_only)
    ew = drift.ew_universe(px, member, cost)
    ew_level = (1 + ew).cumprod()
    col = {s: i for i, s in enumerate(px.columns)}
    arr = px.to_numpy(dtype=float)

    # The book starts with the first full results season: earlier quarters
    # have a handful of filings on file (24 quarters reach back to late 2020).
    n_q = ev.groupby("to_date").size()
    first_q = n_q[n_q >= 100].index.min()
    pos = {d: i for i, d in enumerate(calendar)}
    start = pos[ev.loc[ev["to_date"] == first_q, "reaction_date"].min()]
    dates = calendar[start:]
    lvl = ew_level.iloc[start:] / ew_level.iloc[start]
    ew_q = drift.quarterly(lvl)
    park_nav = pd.Series(park[start:] / park[start], index=dates)
    summary = {"from": str(dates[0]), "to": str(dates[-1]), "cost_pct": cost,
               "ohlc_only": args.ohlc_only, "draws": args.draws,
               "ew_universe": {"cagr": drift.cagr(lvl), "max_drawdown": drift.max_drawdown(lvl)},
               "nifty500": {"cagr": drift.cagr(park_nav),
                            "max_drawdown": drift.max_drawdown(park_nav)},
               "books": {}, "events": {}}
    qrows = []
    rng = np.random.default_rng(args.seed)
    for hold in HOLDS:
        trades = drift.entries(ev, calendar, hold)
        trades = trades[trades["i0"] >= start]
        summary["events"][str(hold)] = drift.event_test(ev, px, ew_level, calendar, hold, cost)
        for slots in SLOTS:
            res = drift.simulate(trades, arr, col, park, slots, cost, start)
            nav = pd.Series(res["nav"], index=dates)
            s = book_stats(nav, res["held"], res, ew_q)
            if slots == GATE_SLOTS:
                q = drift.quarterly(nav)
                for kind in ("member", "filer"):
                    pools = control_pools(kind, trades, ev, calendar, live, member, col)
                    cagrs, qs = [], []
                    for _ in range(args.draws):
                        ct = draw(trades, pools, rng)
                        r = drift.simulate(ct, arr, col, park, slots, cost, start)
                        cn = pd.Series(r["nav"], index=dates)
                        cagrs.append(drift.cagr(cn))
                        qs.append(drift.quarterly(cn))
                    cq = pd.concat(qs, axis=1).mean(axis=1)
                    ex = q - cq
                    s[f"control_{kind}"] = {
                        "cagr_mean": round(float(np.mean(cagrs)), 1),
                        "cagr_p95": round(float(np.percentile(cagrs, 95)), 1),
                        "beats_pct": round(100 * float(np.mean(np.array(cagrs) < s["cagr"])), 1),
                        "excess_q_mean": round(float(ex.mean()), 2),
                        "excess_q_t": drift.t_stat(ex)}
                    for p, v in cq.items():
                        qrows.append({"hold": hold, "quarter": str(p), "series": f"control_{kind}",
                                      "ret": round(float(v), 2)})
                # Informational, not the gate: idle capital in the equal-weight
                # universe instead of the NIFTY 500 (no fund tracks it), to
                # show how much of the gap is where the idle money sits.
                r = drift.simulate(trades, arr, col, ew_level.to_numpy(), slots, cost, start)
                s["idle_in_ew"] = book_stats(pd.Series(r["nav"], index=dates), r["held"], r, ew_q)
                for p, v in q.items():
                    qrows.append({"hold": hold, "quarter": str(p), "series": "book",
                                  "ret": round(float(v), 2)})
            summary["books"][f"{hold}_{slots}"] = s
            print(f"hold {hold:2d}  slots {slots:2d}: CAGR {s['cagr']:+.1f}%  "
                  f"DD {s['max_drawdown']:.1f}%  turnover {s['turnover_x_per_year']}x/yr  "
                  f"excess/q {s['excess_q_mean']:+.2f}% (t={s['excess_q_t']}, "
                  f"{s['excess_q_positive']}/{s['quarters']})  "
                  f"buys {s['buys']} skipped {s['skipped']} avg held {s['avg_positions']}")
            if "idle_in_ew" in s:
                c = s["idle_in_ew"]
                print(f"    idle in EW universe: CAGR {c['cagr']:+.1f}%  "
                      f"excess/q {c['excess_q_mean']:+.2f}% (t={c['excess_q_t']})")
            for kind in ("member", "filer"):
                c = s.get(f"control_{kind}")
                if c:
                    print(f"    vs random {kind}: CAGR {c['cagr_mean']:+.1f}% "
                          f"(95th pct {c['cagr_p95']:+.1f}%, book beats {c['beats_pct']}%)  "
                          f"excess/q {c['excess_q_mean']:+.2f}% (t={c['excess_q_t']})")
        e = summary["events"][str(hold)]
        print(f"  events, {hold} sessions: rallied net of costs vs EW universe "
              f"{e['rallied_net_vs_ew_mean']:+.2f}%/quarter (t={e['rallied_net_vs_ew_t']}, "
              f"{e['rallied_net_vs_ew_positive']}/{e['quarters']})  "
              f"spread net {e['spread_net_mean']:+.2f}% (t={e['spread_net_t']})")
    for q in ew_q.items():
        qrows.append({"hold": 0, "quarter": str(q[0]), "series": "ew_universe",
                      "ret": round(float(q[1]), 2)})
    gate = [summary["books"][f"{h}_{GATE_SLOTS}"] for h in GATE_HOLDS]
    summary["passes"] = any((g["excess_q_t"] or 0) >= 2 and g["excess_q_mean"] > 0 for g in gate)
    print(f"\nEW universe CAGR {summary['ew_universe']['cagr']}% "
          f"(DD {summary['ew_universe']['max_drawdown']}%), NIFTY 500 "
          f"{summary['nifty500']['cagr']}% (DD {summary['nifty500']['max_drawdown']}%), "
          f"{summary['from']} to {summary['to']}")
    print("GATE: " + ("passes -- t >= 2 after costs." if summary["passes"] else
                      "fails -- the drift does not survive as a long-only rule; nothing ranks on it."))
    if not args.ohlc_only:
        OUT.write_text(json.dumps(summary, indent=1, default=str))
        pd.DataFrame(qrows).to_csv(QUARTERS_OUT, index=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
