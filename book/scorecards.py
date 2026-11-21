"""Score every company share you hold (the I/O around strategy/scorecard.py).

Reads the holdings file, the provenance rows in research.db, your research
inputs and sector outlook files, and NSE sector / index membership; works out
each company's sector model; scores it; and saves a dated copy of each card
in research.db (scorecard table) so later reviews can see what changed.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path

from analysis import sector as sector_mod
from book import marks as mk
from book import portfolio as pf
from book import research_db as rdb
from book import securities as sec
from book import userfiles as uf
from strategy import scorecard as sc

ROOT = Path(__file__).resolve().parents[1]


def company_kind(rows: list[dict]) -> str:
    """bank | nbfc | company, from what the filings contained."""
    have = {r["metric"] for r in rows if r["status"] == "ok"}
    if have & {"gnpa_pct", "advances"}:
        return "bank"
    if any(r["status"] == "not_applicable" and "lender" in (r.get("note") or "")
           for r in rows):
        return "nbfc"
    return "company"


def group_for(rows: list[dict], nse_sector: str) -> str:
    return sector_mod.group_of(nse_sector, kind=company_kind(rows))


def read_inputs(config: Path = uf.CONFIG) -> tuple[dict, dict, list[str]]:
    """(research rows by symbol, sector outlook by group, problems)."""
    research, outlook, problems = {}, {}, []
    for spec, key, out in ((uf.RESEARCH, "symbol", research),
                           (uf.SECTOR_OUTLOOK, "sector", outlook)):
        p = uf.find(spec.stem, config)
        if p is None:
            continue
        try:
            _, rows = uf.read(p)
        except Exception as exc:                 # noqa: BLE001
            problems.append(f"{p.name}: {exc}")
            continue
        for r in rows:
            k = str(r.get(key) or "").strip()
            k = k.upper() if key == "symbol" else k.lower()
            if k:
                out[k] = r
    return research, outlook, problems


def config_hash(cfg: dict) -> str:
    return hashlib.sha1(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:10]


def card_dict(card: sc.Scorecard) -> dict:
    d = dataclasses.asdict(card)
    for c, comp in zip(d["components"], card.components):
        c["earned"], c["available"] = comp.earned, comp.available
    return d


def build(holdings: list[dict] | None = None, rcon=None, cfg: dict | None = None,
          today: dt.date | None = None, save: bool = True,
          ref: dict | None = None) -> tuple[list[sc.Scorecard], list[str]]:
    """Cards for every company share in the holdings file, and notes."""
    holdings = pf.read_csv_holdings() if holdings is None else holdings
    cfg = cfg or sc.load_config()
    ref = ref if ref is not None else mk.reference()
    research, outlook, notes = read_inputs()
    own = rcon is None
    rcon = rcon or rdb.connect()
    mcon = sqlite3.connect(ROOT / "data" / "market.db") \
        if (ROOT / "data" / "market.db").exists() else None
    isins = mk.isin_map(mcon) if mcon else {}
    from book import valuations as bv
    try:
        vals, vnotes = bv.build(holdings, ref=ref)
        notes += vnotes
        vals = {v.symbol: dataclasses.asdict(v) for v in vals}
    except Exception as exc:                     # noqa: BLE001
        vals = {}
        notes.append(f"valuation not available ({exc}): its 15 points not assessed")
    cards = []
    try:
        for h in holdings:
            s = sec.classify(h["tradingsymbol"], h.get("isin") or "")
            if not h.get("isin") and s.series == "EQ":
                s = sec.classify(h["tradingsymbol"], isins.get(s.base, ""))
            if s.kind != sec.EQUITY:
                continue
            rows = rdb.metrics(rcon, s.base)
            if not rows:
                notes.append(f"{s.base}: no holding data yet (Holding data › Refresh)")
            override = str((research.get(s.base) or {}).get("sector_group") or "").strip().lower()
            group = override if override in sector_mod.LABEL else \
                group_for(rows, ref["sector"].get(s.base, ""))
            card = sc.score(s.base, rows, group, mk.cap_band(s.base, s.kind, ref), cfg,
                            research.get(s.base), outlook.get(group),
                            h.get("bucket") or "", today,
                            sector_mod.MANUAL.get(group, []), vals.get(s.base))
            cards.append(card)
            if save:
                rdb.save_scorecard(rcon, card.summary(), card_dict(card), config_hash(cfg))
    finally:
        if mcon:
            mcon.close()
        if own:
            rcon.close()
    return cards, notes


def export_json(cards: list[sc.Scorecard], path: Path | str) -> Path:
    """Every card as structured JSON (for review, or for the Phase 7 assistant)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"generated": dt.datetime.now().isoformat(timespec="seconds"),
                                "note": "descriptive scorecard, not backtested",
                                "cards": [card_dict(c) for c in cards]},
                               default=str, indent=2))
    return path


def fill_in_files(cards: list[sc.Scorecard], config: Path = uf.CONFIG) -> list[str]:
    """Create or extend config/research_inputs and config/sector_outlook with a
    row for every scored company and sector group. Existing rows and values are
    kept; only missing keys are added. Returns what changed."""
    done = []
    for spec, key, wanted in (
            (uf.RESEARCH, "symbol", [c.symbol for c in cards]),
            (uf.SECTOR_OUTLOOK, "sector", sorted({c.group for c in cards}))):
        path = uf.find(spec.stem, config) or config / f"{spec.stem}.xlsx"
        rows, cols = [], spec.names
        if path.exists():
            cols0, rows = uf.read(path)
            cols = cols0 + [c for c in spec.names if c not in cols0]
        have = {str(r.get(key) or "").strip().upper() for r in rows}
        new = [{key: k} for k in wanted if k.upper() not in have]
        if new or not path.exists():
            uf.write(path, spec, rows + new, cols)
            done.append(f"{path.name}: {len(new)} row(s) added")
    return done
