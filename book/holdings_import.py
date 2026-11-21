"""Broker holdings file -> a previewed, validated, audited holdings file.

Three steps, and the file you maintain is only touched by the last:

  parse     find the header row wherever it sits, match columns by common
            names, merge duplicate lines, label every security's type
            (book/securities.py). Nothing is skipped silently: REITs, InvITs,
            ETFs and bonds are kept with their type.
  preview   validate against the market data and diff against your current
            holdings file: new, removed and changed positions, plus every
            issue found (missing quantity or cost, invested amount that does
            not match quantity x cost, unknown symbol, ISIN now listed under
            another symbol, not traded recently, a recent split or bonus,
            a security type to verify).
  commit    only on your explicit confirmation: back up the old file, write
            the new one, and record what changed in research.db's audit log.

Your broker knows your quantities; only you know why you own something, so
bucket, notes and purchase_date are carried over for every symbol you still
hold.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from book import portfolio as pf
from book import securities as sec

# Console splits the position across several columns; the holding is their sum.
# Pledged shares are still yours -- leaving them out would understate the book.
QUANTITY_PARTS = ("quantity available", "quantity discrepant",
                  "quantity pledged (margin)", "quantity pledged (loan)")
LT_ALIASES = pf.COLUMN_ALIASES["lt_quantity"] + ("quantity long term",)
INVESTED_ALIASES = ("invested", "invested value", "invested amount", "buy value",
                    "total invested", "investment value", "amount invested")

# Kite shows other equity series as SYMBOL-BE etc. The framework keys on the
# bare NSE symbol, so the suffix has to go or the stock silently never matches.
EQUITY_SERIES = re.compile(r"-(BE|BZ|BL|BT|SM|ST)$")

# Hand-maintained columns that survive a re-import.
KEPT = ("bucket", "notes", "purchase_date")
INVESTED_TOLERANCE = 0.01        # 1%: rounding in the broker's own figures

ERROR, WARNING, INFO = "error", "warning", "info"


@dataclass
class Issue:
    level: str
    symbol: str
    code: str
    message: str


def clean_symbol(raw) -> str:
    sym = str(raw or "").strip().strip('"').upper()
    return EQUITY_SERIES.sub("", sym)


def _norm(h) -> str:
    return str(h or "").strip().strip('"').lower()


def _find(header: list[str], aliases) -> int | None:
    for a in aliases:
        if a in header:
            return header.index(a)
    return None


def locate_header(grid: list[list]) -> tuple[int, dict] | None:
    """Index of the header row and the column positions it implies."""
    for i, row in enumerate(grid):
        header = [_norm(c) for c in row]
        sym = _find(header, pf.COLUMN_ALIASES["symbol"])
        if sym is None:
            continue
        parts = [header.index(p) for p in QUANTITY_PARTS if p in header]
        qty = _find(header, pf.COLUMN_ALIASES["quantity"])
        if not parts and qty is None:
            continue
        return i, {
            "symbol": sym,
            "quantity": parts or [qty],
            "avg_price": _find(header, pf.COLUMN_ALIASES["avg_price"]),
            "lt_quantity": _find(header, LT_ALIASES),
            "st_quantity": _find(header, pf.COLUMN_ALIASES["st_quantity"]),
            "isin": _find(header, ("isin", "isin code")),
            "sector": _find(header, ("sector",)),
            "invested": _find(header, INVESTED_ALIASES),
        }
    return None


def _cell(row: list, idx: int | None) -> float | None:
    if idx is None or idx >= len(row):
        return None
    if row[idx] is None or str(row[idx]).strip() == "":
        return None
    return pf._to_float(row[idx])


def _text(row: list, idx: int | None) -> str:
    if idx is None or idx >= len(row) or row[idx] is None:
        return ""
    return str(row[idx]).strip()


def parse(grid: list[list]) -> tuple[list[dict], list[Issue]]:
    """Rows of a broker export -> (holdings rows, issues found while reading).

    Each row: symbol, quantity, lt/st_quantity, avg_price, isin, kind, label,
    review, invested (the file's own figure, if it has one).
    """
    found = locate_header(grid)
    if not found:
        return [], [Issue(ERROR, "", "no_header",
                          "No holdings table found: looked for a header row with "
                          "Instrument/Symbol and Qty./Quantity.")]
    start, cols = found
    merged: dict[str, dict] = {}
    issues: list[Issue] = []
    for row in grid[start + 1:]:
        if cols["symbol"] >= len(row):
            continue
        sym = clean_symbol(row[cols["symbol"]])
        # A blank symbol ends the table (Console puts totals / notes below it).
        if not sym:
            if merged:
                break
            continue
        if sym in ("TOTAL", "GRAND TOTAL"):
            break
        parts = [_cell(row, i) for i in cols["quantity"]]
        qty = sum(p or 0.0 for p in parts)
        if qty <= 0:
            issues.append(Issue(WARNING, sym, "no_quantity",
                                "no quantity (blank or zero): line left out"))
            continue
        isin = _text(row, cols["isin"]).upper()
        s = sec.classify(sym, isin, _text(row, cols["sector"]))
        avg = _cell(row, cols["avg_price"])
        lt = _cell(row, cols["lt_quantity"])
        st = _cell(row, cols["st_quantity"])
        if lt is not None and st is None:
            st = max(qty - lt, 0.0)
        elif st is not None and lt is None:
            lt = max(qty - st, 0.0)
        invested = _cell(row, cols["invested"])

        # The same stock can appear twice (an EQ line and a BE line).
        # Combine into one position at the blended cost.
        if s.symbol in merged:
            m = merged[s.symbol]
            total = m["quantity"] + qty
            if m["avg_price"] is not None and avg is not None:
                m["avg_price"] = (m["avg_price"] * m["quantity"] + avg * qty) / total
            else:
                m["avg_price"] = None
            m["quantity"] = total
            if lt is not None:
                m["lt_quantity"] = (m["lt_quantity"] or 0.0) + lt
                m["st_quantity"] = (m["st_quantity"] or 0.0) + st
            if invested is not None and m["invested"] is not None:
                m["invested"] += invested
            issues.append(Issue(INFO, s.symbol, "duplicate",
                                "appears on more than one line: merged at the "
                                "blended average cost"))
            continue
        merged[s.symbol] = {"symbol": s.symbol, "quantity": qty, "lt_quantity": lt,
                            "st_quantity": st, "avg_price": avg, "isin": isin,
                            "kind": s.kind, "label": s.label, "review": s.review,
                            "invested": invested}
    return list(merged.values()), issues


def convert(grid: list[list]) -> tuple[list[dict], list[tuple[str, str]]]:
    """(every holding, [(symbol, type) for the ones that are not equity shares])."""
    rows, _ = parse(grid)
    for r in rows:
        if r["avg_price"] is None:
            r["avg_price"] = 0.0
    return rows, [(r["symbol"], r["label"]) for r in rows if r["kind"] != sec.EQUITY]


def read_grid(path: Path) -> list[list]:
    """Every row of the file as a list of cells, preamble included."""
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as fh:
            return list(csv.reader(fh))
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    # Console's workbook has Equity and Mutual Funds sheets; only Equity counts.
    names = [n for n in wb.sheetnames if n.strip().lower() == "equity"] or wb.sheetnames
    grid: list[list] = []
    for name in names:
        ws = wb[name]
        # Console's file declares every sheet as 1x1, and read-only mode trusts
        # that and yields a single empty cell. Ignore it and read what is there.
        ws.reset_dimensions()
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        if locate_header(rows):
            grid = rows
            break
    wb.close()
    return grid


# ------------------------------------------------------------- validation

@dataclass
class Context:
    """What the market data says, for validation. Every part is optional:
    an empty context validates only what the file itself can show."""
    known: set[str] = field(default_factory=set)            # listed symbols
    isin_symbol: dict[str, str] = field(default_factory=dict)
    last_traded: dict[str, str] = field(default_factory=dict)
    stale_before: str | None = None                          # session date
    actions: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    today: dt.date = field(default_factory=dt.date.today)


def market_context(mcon, recent_days: int = 60) -> Context:
    """Context from market.db: instrument map, last trade dates, recent
    splits and bonuses."""
    ctx = Context()
    ctx.isin_symbol = {i: s for s, i in mcon.execute(
        "SELECT symbol, isin FROM instruments") if i}
    ctx.last_traded = dict(mcon.execute(
        "SELECT symbol, MAX(date) FROM market GROUP BY symbol"))
    for sym, d in mcon.execute("SELECT symbol, MAX(date) FROM market_t2t GROUP BY symbol"):
        if d > ctx.last_traded.get(sym, ""):
            ctx.last_traded[sym] = d
    ctx.known = set(ctx.last_traded)
    days = [r[0] for r in mcon.execute(
        "SELECT date FROM market_days WHERE rows > 0 ORDER BY date DESC LIMIT 11")]
    ctx.stale_before = days[-1] if len(days) == 11 else None
    since = (ctx.today - dt.timedelta(days=recent_days)).isoformat()
    for sym, ex, subj in mcon.execute(
            "SELECT symbol, ex_date, subject FROM market_actions WHERE ex_date >= ?",
            (since,)):
        ctx.actions.setdefault(sym, []).append((ex, subj))
    return ctx


def validate(rows: list[dict], ctx: Context) -> list[Issue]:
    issues = []
    priced_kinds = (sec.EQUITY, sec.ETF, sec.PARTLY_PAID)
    for r in rows:
        sym, base = r["symbol"], sec.split_symbol(r["symbol"])[0]
        if r["review"]:
            issues.append(Issue(WARNING, sym, "verify_type",
                                f"verify the security type: {r['review']}"))
        if r["kind"] not in (sec.EQUITY,):
            issues.append(Issue(INFO, sym, "non_equity",
                                f"{r['label']}: kept, reported in its own sleeve, "
                                "not scored"))
        if r["avg_price"] in (None, 0, 0.0):
            issues.append(Issue(WARNING, sym, "no_cost",
                                "no average cost: P/L and tax cannot be worked out"))
        elif r.get("invested") is not None:
            implied = r["avg_price"] * r["quantity"]
            if implied and abs(r["invested"] - implied) / implied > INVESTED_TOLERANCE:
                issues.append(Issue(
                    WARNING, sym, "invested_mismatch",
                    f"invested ₹{r['invested']:,.2f} in the file, but quantity x "
                    f"average cost is ₹{implied:,.2f}"))
        if r["lt_quantity"] is not None and r["lt_quantity"] > r["quantity"] + 1e-9:
            issues.append(Issue(ERROR, sym, "lt_exceeds_qty",
                                "long-term quantity is more than the quantity held"))
        isin = r.get("isin") or ""
        if isin and ctx.isin_symbol:
            listed = ctx.isin_symbol.get(isin)
            if listed and listed != base:
                issues.append(Issue(
                    WARNING, sym, "symbol_changed",
                    f"ISIN {isin} trades on NSE as {listed}: a ticker change, merger "
                    "or the wrong symbol?"))
        if r["kind"] in priced_kinds and ctx.known:
            if base not in ctx.known:
                issues.append(Issue(WARNING, sym, "unknown_symbol",
                                    "not found in the NSE market data: check the "
                                    "symbol (or it may be BSE-only)"))
            elif ctx.stale_before and ctx.last_traded.get(base, "") < ctx.stale_before:
                issues.append(Issue(WARNING, sym, "not_traded",
                                    f"last traded {ctx.last_traded[base]}: suspended "
                                    "or delisted?"))
        for ex, subj in ctx.actions.get(base, []):
            issues.append(Issue(INFO, sym, "corporate_action",
                                f"{subj} (ex-date {ex}): check the broker's quantity "
                                "and cost already reflect it"))
    return issues


# ----------------------------------------------------------------- preview

@dataclass
class Preview:
    source: str
    digest: str
    rows: list[dict]
    issues: list[Issue]
    new: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: list[dict] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == ERROR]

    @property
    def ok(self) -> bool:
        return bool(self.rows) and not self.errors

    @property
    def has_changes(self) -> bool:
        return bool(self.new or self.removed or self.changed)


def _close(a, b, tol: float = 1e-6) -> bool:
    if a in (None, "") and b in (None, ""):
        return True
    try:
        return abs(float(a or 0) - float(b or 0)) <= tol * max(1.0, abs(float(b or 0)))
    except (TypeError, ValueError):
        return False


def preview(grid: list[list], current: list[dict], ctx: Context | None = None,
            source: str = "", trade: list[str] = ()) -> Preview:
    """current: holdings as book.portfolio.read_csv_holdings returns them."""
    rows, issues = parse(grid)
    issues += validate(rows, ctx or Context())
    previous = {h["tradingsymbol"]: h for h in current}
    for r in rows:
        old = previous.get(r["symbol"], {})
        for k in KEPT:
            r[k] = old.get(k, "")
        # A file without a long/short-term split must not erase the one you
        # have: same quantity, keep it; different quantity, say it is lost.
        if r["lt_quantity"] is None and old.get("lt_quantity") is not None:
            if _close(r["quantity"], old.get("quantity")):
                r["lt_quantity"], r["st_quantity"] = old["lt_quantity"], old.get("st_quantity")
            else:
                issues.append(Issue(WARNING, r["symbol"], "lt_split_lost",
                                    "this file has no long/short-term split and the "
                                    "quantity changed, so the split on file is dropped; "
                                    "tax estimates fall back to purchase_date"))
    rows.sort(key=lambda r: r["symbol"])
    marked, missing = mark_trade(rows, trade)
    for m in missing:
        issues.append(Issue(WARNING, m, "trade_missing",
                            "asked to mark as trade, but not in this file"))
    digest = hashlib.sha256(repr(grid).encode()).hexdigest()[:16]
    p = Preview(source, digest, rows, issues)
    now = {r["symbol"]: r for r in rows}
    p.new = sorted(set(now) - set(previous))
    p.removed = sorted(set(previous) - set(now))
    for sym in sorted(set(now) & set(previous)):
        r, h = now[sym], previous[sym]
        diffs = {}
        for new_k, old_k in (("quantity", "quantity"), ("avg_price", "average_price"),
                             ("lt_quantity", "lt_quantity")):
            if not _close(r.get(new_k), h.get(old_k)):
                diffs[new_k] = (h.get(old_k), r.get(new_k))
        if sym in marked and h.get("bucket") != "trade":
            diffs["bucket"] = (h.get("bucket"), "trade")
        if diffs:
            p.changed.append({"symbol": sym, **diffs})
        else:
            p.unchanged.append(sym)
    for sym in p.removed:
        kept = [k for k in KEPT if previous[sym].get(k)]
        note = f" (its {', '.join(kept)} will be dropped)" if kept else ""
        p.issues.append(Issue(WARNING, sym, "removed",
                              f"in your holdings file but not in this one: treated "
                              f"as sold{note}"))
    return p


def mark_trade(rows: list[dict], symbols) -> tuple[list[str], list[str]]:
    """Set bucket=trade on the named holdings. Returns (marked, missing).

    A new holding otherwise arrives with a blank bucket, and the framework
    never suggests trade on its own -- so a position bought to trade would be
    held as a satellite and never rotated, which defeats the reason it was
    bought.
    """
    wanted = {clean_symbol(s) for s in symbols}
    marked = []
    for r in rows:
        if r["symbol"] in wanted:
            r["bucket"] = "trade"
            marked.append(r["symbol"])
    return sorted(marked), sorted(wanted - set(marked))


# ------------------------------------------------------------------ commit

def _fmt(v) -> str:
    if v is None or v == "":
        return ""
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else f"{v:.2f}"
    return str(v)


def write_holdings(rows: list[dict], path: Path) -> None:
    """config/holdings.xlsx keeps its "How to fill" sheet; a .csv gets the
    help as comments (book/userfiles.py)."""
    from book import userfiles as uf
    uf.write(path, uf.HOLDINGS, [{c: _fmt(r.get(c)) for c in pf.HOLDINGS_COLUMNS}
                                 for r in rows], pf.HOLDINGS_COLUMNS)


def commit(p: Preview, out: Path, rcon=None, actor: str = "user") -> Path | None:
    """Write the previewed holdings. Refuses a preview with errors. Returns
    the backup path (None when there was no previous file)."""
    if not p.ok:
        raise ValueError("preview has errors; nothing written: "
                         + "; ".join(f"{i.symbol} {i.message}" for i in p.errors))
    backup = None
    if out.exists():
        backup = out.with_name(out.name + ".bak")
        shutil.copy2(out, backup)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_holdings(p.rows, out)
    if rcon is not None:
        from book import research_db as rdb
        rdb.log(rcon, "import_holdings", str(out.name), {
            "source": p.source, "source_digest": p.digest,
            "positions": len(p.rows), "new": p.new, "removed": p.removed,
            "changed": p.changed, "backup": backup.name if backup else None,
            "warnings": [f"{i.symbol}: {i.message}" for i in p.issues
                         if i.level == WARNING]}, actor=actor)
    return backup


def report(p: Preview) -> list[str]:
    """The preview as plain text lines, for the command line."""
    kinds: dict[str, int] = {}
    for r in p.rows:
        kinds[r["label"]] = kinds.get(r["label"], 0) + 1
    lines = [f"{p.source}: {len(p.rows)} positions ("
             + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) + ")"]
    if p.new:
        lines.append(f"  new:       {', '.join(p.new)}")
    if p.removed:
        lines.append(f"  removed:   {', '.join(p.removed)}")
    for c in p.changed:
        parts = [f"{k} {v[0]} -> {v[1]}" for k, v in c.items() if k != "symbol"]
        lines.append(f"  changed:   {c['symbol']}: " + "; ".join(parts))
    if not p.has_changes:
        lines.append("  no changes against your current holdings file")
    for level in (ERROR, WARNING, INFO):
        group = [i for i in p.issues if i.level == level]
        if group:
            lines.append(f"  {level}s ({len(group)}):")
            lines += [f"    {i.symbol:<16} {i.message}" for i in group]
    return lines
