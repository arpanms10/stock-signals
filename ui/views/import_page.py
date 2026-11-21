"""Portfolio › Import holdings: broker file -> preview -> confirm -> write.

Nothing is written until you tick the confirmation and press the button. The
old file is kept as .bak and the import is recorded in the audit log
(data/research.db).
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from book import holdings_import as hi
from book import portfolio as pf
from book import research_db as rdb
from book import userfiles as uf
from ui import state

ROOT = Path(__file__).resolve().parents[2]
LEVEL_ICON = {hi.ERROR: "⛔ error", hi.WARNING: "⚠️ warning", hi.INFO: "ℹ️ info"}


@st.cache_data(ttl=3600, show_spinner=False)
def _context() -> hi.Context:
    db = ROOT / "data" / "market.db"
    if not db.exists():
        return hi.Context()
    con = sqlite3.connect(db)
    try:
        return hi.market_context(con)
    finally:
        con.close()


def render() -> None:
    out = uf.path_for("holdings")
    state.page_title("Import holdings",
                     f"From your broker's holdings download into {out.name}")
    st.caption("Any broker's .csv or .xlsx (Kite, Console and most others). REITs, "
               "InvITs, ETFs and bonds are kept and labelled; your bucket, notes "
               "and purchase dates carry over. You see every change before "
               "anything is written.")
    up = st.file_uploader("Broker holdings file", type=["csv", "xlsx", "xlsm"])
    if up is None:
        return
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / up.name
        path.write_bytes(up.getvalue())
        grid = hi.read_grid(path)
    current = pf.read_csv_holdings(out) if out.exists() else []
    trade = st.multiselect(
        "Mark as trade (bought to trade, rotated on momentum rank)",
        [r["symbol"] for r in hi.parse(grid)[0]], key=f"trade_{up.name}")
    p = hi.preview(grid, current, _context(), source=up.name, trade=trade)

    kinds = pd.Series([r["label"] for r in p.rows]).value_counts()
    cols = st.columns(4)
    cols[0].metric("Positions", len(p.rows))
    cols[1].metric("New", len(p.new))
    cols[2].metric("Changed", len(p.changed))
    cols[3].metric("Removed", len(p.removed))
    st.caption(" · ".join(f"{n} {k}" for k, n in kinds.items()))

    if p.issues:
        st.markdown("**Validation**")
        st.dataframe(pd.DataFrame([{"Level": LEVEL_ICON[i.level], "Symbol": i.symbol,
                                    "Finding": i.message} for i in p.issues]),
                     hide_index=True, width="stretch")
    if p.changed:
        st.markdown("**Changed**")
        st.dataframe(pd.DataFrame([{"Symbol": ch["symbol"], **{
            k: f"{v[0]} → {v[1]}" for k, v in ch.items() if k != "symbol"}}
            for ch in p.changed]), hide_index=True, width="stretch")
    with st.expander("The file as it will be written"):
        st.dataframe(pd.DataFrame([{k: r.get(k) for k in
                                    ("symbol", "label", "quantity", "lt_quantity",
                                     "st_quantity", "avg_price", "isin", "bucket",
                                     "notes")} for r in p.rows]),
                     hide_index=True, width="stretch")

    if not p.ok:
        st.error("Not importable until the errors above are fixed in the file.")
        return
    if out.exists() and not p.has_changes:
        st.success(f"Nothing to change: {out.name} already matches this file.")
        return
    sure = st.checkbox(f"I've reviewed the changes. Replace {out.name} "
                       f"(the current file is kept as {out.name}.bak).")
    if st.button("Write holdings file", type="primary", disabled=not sure):
        con = rdb.connect()
        try:
            backup = hi.commit(p, out, con)
        finally:
            con.close()
        st.cache_data.clear()
        st.success(f"Wrote {len(p.rows)} positions to {out.name}"
                   + (f"; previous file kept as {backup.name}" if backup else "")
                   + ". Recorded in the audit log.")
