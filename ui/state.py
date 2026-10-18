"""What every dashboard page shares: the cached snapshot, the data-age
checks, the job runner, and the column helpers the stock tables use.

Pages import from here rather than from each other, so a page only pays for
what it reads: the Mutual funds page never builds the stock snapshot, and
opening it no longer waits for the whole market to be ranked.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from ui import service

DEFAULT_UNIVERSE = 500


@st.cache_data(show_spinner="Loading market data, scoring, ranking...")
def _load(universe_size: int):
    return service.build(universe_size=universe_size)


def universe_size() -> int:
    return int(st.session_state.get("universe_size", DEFAULT_UNIVERSE))


def snapshot():
    """The full stock picture for the current universe size (slow; cached)."""
    return _load(universe_size())


def clear() -> None:
    """Forget every cached result so the next read recomputes."""
    _load.clear()
    freshness.clear()


@st.cache_data(ttl=300, show_spinner=False)
def freshness() -> list[dict]:
    """Age of prices, market data and fundamentals; cheap enough for every page."""
    try:
        return service.freshness_checks()
    except Exception as exc:          # noqa: BLE001 -- the header must still draw
        return [{"kind": "market", "label": "stale", "latest": None,
                 "message": f"Could not read data freshness ({exc})."}]


def ranking_settings(where=None) -> None:
    """Universe size and Recompute. They only change the ranked universe, so
    they sit on the pages that show it rather than on every page."""
    where = where or st
    cols = where.columns([3, 1], vertical_alignment="bottom")
    size = cols[0].slider("Universe size (by liquidity)", 50, 500, universe_size(), 50,
                          key="universe_size_slider",
                          help="How many of the most liquid NSE stocks to rank. "
                               "NIFTY 500 is roughly the top 500.")
    if size != universe_size():
        st.session_state["universe_size"] = size
        st.rerun()
    if cols[1].button("Recompute", width="stretch", key="recompute"):
        _load.clear()
        st.rerun()


def run_and_reload(label: str, args: list[str], where=None) -> None:
    """Run a refresh script with live output, then reload everything.

    Recompute is folded in: a successful refresh clears the cache and reruns
    the page, so what you see afterwards is the refreshed data. A failure
    stays on screen with its output instead of a misleading "Done".
    Keep this tab open while it runs -- a long refresh is cut short if the
    page reruns underneath it.
    """
    where = where or st
    where.write(f"Running `{' '.join(args)}`... keep this tab open.")
    box = where.empty()
    lines: list[str] = []
    for line in service.run_script(args):
        lines.append(line)
        box.code("\n".join(lines[-14:]), language=None)
    code = service.exit_code(lines)
    if code == 0:
        clear()
        st.session_state["refreshed"] = label
        st.session_state.setdefault("last_run", {})[label] = dt.datetime.now()
        st.rerun()
    else:
        where.error(f"{label} failed (exit code {code}). The output above says why; "
                    "nothing was reloaded.")


def refreshed_notice() -> None:
    if done := st.session_state.pop("refreshed", None):
        st.success(f"{done}: done, and the page has reloaded with the new data.")


# Shown next to the Quality bar, filled only when there is no score -- a blank
# bar on its own reads as "scored badly" or "broken".
QUALITY_NOTE = st.column_config.TextColumn(
    "Quality note",
    help="Why there is no quality score. Not fetched: no fundamentals on file "
         "yet -- use 'Refresh fundamentals'. Too little data: fetched, but too "
         "few figures came back to score.")


RESULTS_COL = st.column_config.TextColumn(
    "Results", help="This quarter's results verdict and filing date, or the "
                    "announced results date. Judged against the company's own "
                    "recent quarters -- see the Quarterly results page.")


@st.cache_data(ttl=900, show_spinner=False)
def results_badges(symbols: tuple[str, ...]) -> dict[str, str]:
    """Results column for any table; empty strings if nothing is on file."""
    from results import engine as results_engine
    try:
        return results_engine.badges(list(symbols), dt.date.today())
    except Exception:
        return {}


def with_results(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "symbol" not in df:
        return df
    b = results_badges(tuple(sorted(df["symbol"].astype(str))))
    return df.assign(results=[b.get(s, "") for s in df["symbol"]])


def with_quality_note(df: pd.DataFrame) -> pd.DataFrame:
    """The note column, empty where there is a score.

    Empty rather than None, which Streamlit renders as a grey "None" on every
    scored row. Snapshots cached by an older version lack the column."""
    if df.empty:
        return df
    df = df.copy()
    if "quality_note" not in df:
        df["quality_note"] = [None if q == q and q is not None else "Not fetched"
                              for q in df["quality"]]
    df["quality_note"] = df["quality_note"].fillna("")
    return df


def page_title(title: str, subtitle: str = "") -> None:
    """Every page opens the same way: title, then what its data is as of."""
    st.markdown(f"## {title}")
    if subtitle:
        st.caption(subtitle)
