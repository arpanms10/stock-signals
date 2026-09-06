"""Holdings bridge.

Kite MCP is an interactive, OAuth-authenticated interface -- it cannot be the
data layer for an unattended nightly job (tokens expire every morning). So
holdings arrive as a snapshot written to data/holdings.json when you and Claude
are talking, and the engine reads that file. Hand-editable by design, so
nothing here depends on the connector being up.

Expected shape (this is exactly what Kite's get_holdings returns, trimmed):

    [{"tradingsymbol": "RELIANCE", "quantity": 50,
      "average_price": 1180.5, "purchase_date": "2025-04-11"}]

purchase_date is optional -- Kite does not reliably return it for older
holdings, and inventing one would put a trailing stop in the wrong place.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd

from signals import state as st
from signals.engine import initial_stop

HOLDINGS_PATH = Path(__file__).parent / "data" / "holdings.json"
HOLDINGS_CSV = Path(__file__).parent / "config" / "holdings.csv"

HOLDINGS_COLUMNS = ["symbol", "quantity", "lt_quantity", "st_quantity",
                    "avg_price", "purchase_date", "bucket", "notes"]

HOLDINGS_HELP = """\
# Your actual positions. Opens in Excel.
#
#   symbol         NSE symbol, e.g. RELIANCE
#   quantity       number of shares held
#   avg_price      your average cost per share, in rupees. This is what makes
#                  stops and P&L real rather than hypothetical.
#   lt_quantity    shares held over 12 months (long-term capital gains)
#   st_quantity    shares held under 12 months (short-term)
#                  From your broker's holdings statement. More accurate than a
#                  purchase date, because a position accumulated over years is
#                  many lots and one date cannot describe them. Give either
#                  column and the remainder is inferred.
#   purchase_date  optional, YYYY-MM-DD. Improves trailing-stop accuracy; when
#                  absent a bounded lookback is used instead of a guess.
#   bucket         core | satellite | (blank = let the framework suggest one)
#                  Core is held through momentum signals; satellite rotates.
#   notes          yours
"""


# Kite's own holdings export uses different headers. Accepting it directly
# means you can download from the broker and drop the file in, rather than
# retyping 40 positions into a template.
COLUMN_ALIASES = {
    "symbol": ("symbol", "instrument", "tradingsymbol", "scrip", "stock"),
    "quantity": ("quantity", "qty.", "qty", "shares", "holding qty"),
    "avg_price": ("avg_price", "avg. cost", "avg cost", "average price",
                  "avg. price", "buy avg", "average_price"),
    "purchase_date": ("purchase_date", "date", "buy date"),
    # A holdings statement splits the position by tax status directly, which is
    # more accurate than a purchase date: shares accumulated over years are
    # many lots, and one date cannot describe them.
    "lt_quantity": ("lt_quantity", "long term qty", "long term quantity",
                    "longterm qty", "lt qty", "long-term qty",
                    "long term qty.", "ltq"),
    "st_quantity": ("st_quantity", "short term qty", "short term quantity",
                    "shortterm qty", "st qty", "short-term qty",
                    "short term qty.", "stq"),
    "bucket": ("bucket", "type"),
    "notes": ("notes", "remark", "remarks"),
}


def _map_columns(fieldnames: list[str]) -> dict[str, str]:
    """Match whatever headers the file has onto the fields we need."""
    found: dict[str, str] = {}
    lowered = {(f or "").strip().lower().strip('"'): f for f in fieldnames or []}
    for target, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lowered:
                found[target] = lowered[alias]
                break
    return found


def _to_float(v) -> float:
    """Numbers may arrive with commas or currency padding."""
    if v is None:
        return 0.0
    t = str(v).strip().strip('"').replace(",", "").replace("\u20b9", "")
    try:
        return float(t)
    except ValueError:
        return 0.0


def read_csv_holdings(path: Path | str = HOLDINGS_CSV) -> list[dict]:
    """Holdings from CSV -- our template or a broker export. Same shape out."""
    import csv

    path = Path(path)
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig") as fh:
        reader = csv.DictReader(r for r in fh if not r.lstrip().startswith("#"))
        cols = _map_columns(reader.fieldnames or [])
        if "symbol" not in cols or "quantity" not in cols:
            return []
        out = []
        for r in reader:
            sym = str(r.get(cols["symbol"], "")).strip().strip('"').upper()
            if not sym:
                continue
            qty = _to_float(r.get(cols["quantity"]))
            if qty <= 0:
                continue
            lt = _to_float(r.get(cols.get("lt_quantity"))) if "lt_quantity" in cols else None
            st = _to_float(r.get(cols.get("st_quantity"))) if "st_quantity" in cols else None
            # If only one side is given, the rest of the holding is the other.
            if lt is not None and st is None:
                st = max(qty - lt, 0.0)
            elif st is not None and lt is None:
                lt = max(qty - st, 0.0)
            out.append({
                "tradingsymbol": sym,
                "quantity": qty,
                "lt_quantity": lt,
                "st_quantity": st,
                "average_price": _to_float(r.get(cols.get("avg_price"))),
                "purchase_date": str(r.get(cols.get("purchase_date"), "") or "").strip(),
                "bucket": str(r.get(cols.get("bucket"), "") or "").strip().lower(),
                "notes": str(r.get(cols.get("notes"), "") or "").strip(),
            })
    return out


def write_csv_template(symbols: list[str], path: Path | str = HOLDINGS_CSV) -> Path:
    import csv

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        fh.write(HOLDINGS_HELP)
        w = csv.DictWriter(fh, fieldnames=HOLDINGS_COLUMNS)
        w.writeheader()
        for s in symbols:
            w.writerow({"symbol": s})
    return path


def load_holdings() -> list[dict]:
    """CSV first, then the Kite JSON snapshot.

    CSV wins because it is what you maintain by hand; the snapshot is a
    convenience that may be stale between conversations.
    """
    rows = read_csv_holdings()
    if rows:
        return rows
    return read_snapshot()


def read_snapshot(path: Path | str = HOLDINGS_PATH) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    if isinstance(data, dict):                      # tolerate {"holdings": [...]}
        data = data.get("holdings", data.get("data", []))
    return [h for h in data if h.get("tradingsymbol") or h.get("symbol")]


def write_snapshot(holdings: list[dict], path: Path | str = HOLDINGS_PATH) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(holdings, indent=2))
    return len(holdings)


def _entry_date(h: dict, frame: pd.DataFrame, cfg: dict) -> tuple[dt.date, bool]:
    """Purchase date if Kite gave one, else a bounded fallback.

    The trailing stop needs 'highest close since entry'. With no purchase date
    we use a bounded lookback instead of guessing -- which makes the stop
    slightly conservative rather than silently wrong.
    """
    raw = h.get("purchase_date") or h.get("entry_date")
    if raw:
        try:
            return dt.date.fromisoformat(str(raw)[:10]), True
        except ValueError:
            pass
    days = cfg["risk"]["trail_lookback_days"]
    latest = frame["date"].iloc[-1] if not frame.empty else dt.date.today()
    return latest - dt.timedelta(days=days), False


def import_positions(con, cfg: dict, frames: dict[str, pd.DataFrame],
                     path: Path | str = HOLDINGS_PATH) -> tuple[int, list[str]]:
    """Turn a holdings snapshot into open positions with correct stops.

    The average cost is the point of this: stops and trailing stops compute
    against what you actually paid, so exit signals are real on the first run.
    """
    notes: list[str] = []
    holdings = read_snapshot(path)
    imported = 0
    for h in holdings:
        sym = (h.get("tradingsymbol") or h.get("symbol") or "").upper()
        qty = float(h.get("quantity") or h.get("qty") or 0)
        avg = float(h.get("average_price") or h.get("avg_price") or 0)
        if not sym or qty <= 0 or avg <= 0:
            continue
        frame = frames.get(sym, pd.DataFrame())
        entry_date, exact = _entry_date(h, frame, cfg)
        if not exact:
            notes.append(f"{sym}: no purchase date from Kite; trailing stop uses a "
                         f"{cfg['risk']['trail_lookback_days']}-day lookback")

        atr, highest = 0.0, avg
        if not frame.empty:
            since = frame[frame["date"] >= entry_date]
            if not since.empty:
                highest = max(avg, float(since["close"].max()))
            atr = float(frame["atr14"].iloc[-1] or 0)

        pos = st.Position(sym, qty, avg, entry_date, highest_close=highest,
                          stop_price=initial_stop(avg, atr, cfg), source="kite")
        # Re-run the trailing logic so an old, profitable holding arrives with
        # its stop already ratcheted up rather than sitting at the entry stop.
        if not frame.empty:
            from signals.engine import update_trailing
            pos = update_trailing(pos, frame.iloc[-1], cfg)
            pos.highest_close = highest
            if pos.pnl_pct(float(frame["close"].iloc[-1])) >= cfg["risk"]["trail_activate_pct"]:
                cand = highest - cfg["risk"]["trail_atr_mult"] * atr
                if cand > pos.stop_price:
                    pos.stop_price, pos.trailing = round(cand, 2), True
        st.save_position(con, pos)
        imported += 1
    return imported, notes


def sync_watchlist(path: Path | str = HOLDINGS_PATH) -> list[str]:
    """Add anything you hold to the watchlist.

    Holdings alone can only ever produce sell signals -- the engine cannot
    suggest buying something it is not tracking -- so the watchlist is
    holdings plus candidates, never holdings alone.
    """
    import watchlist as wl
    added = []
    for h in read_snapshot(path):
        sym = (h.get("tradingsymbol") or h.get("symbol") or "").upper()
        if not sym:
            continue
        changed, _ = wl.add(sym, notes="held (Kite)")
        if changed:
            added.append(sym)
    return added
