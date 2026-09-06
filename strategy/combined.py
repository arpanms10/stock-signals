"""Combined rank: momentum ranking gated by fundamental quality.

Momentum answers "what is working?" and is entirely blind to whether the
business behind it is sound -- it will happily rank a fraud that is going up.
The quality gate is the check on that: a stock below the floor is removed from
candidacy however strong its price action.

Deliberately a *gate*, not a blend. Averaging a quality score into a momentum
rank produces a number that is neither, and makes it impossible to say why a
stock was chosen. A gate keeps both signals legible: momentum decides the
order, quality decides eligibility.
"""
from __future__ import annotations

import pandas as pd

import fundamentals as fu
from strategy import momentum as mom


def combined_rank(panel: pd.DataFrame, on_date, cfg: dict,
                  quality: dict[str, fu.Quality] | None = None) -> pd.DataFrame:
    """Momentum ranking with a quality gate applied. Best first."""
    ranked = mom.rank_on(panel, on_date, cfg)
    if ranked.empty or not quality:
        if not ranked.empty:
            ranked["quality"] = None
            ranked["quality_note"] = "no fundamentals on file"
        return ranked

    m = cfg.get("momentum_strategy", {})
    floor = m.get("quality_floor", 50.0)
    allow_unknown = m.get("allow_unknown_quality", True)

    ranked["quality"] = [
        quality[s].score if s in quality else None for s in ranked["symbol"]]
    ranked["quality_note"] = [
        "; ".join(quality[s].flags) if s in quality and quality[s].flags else ""
        for s in ranked["symbol"]]
    keep = [fu.passes_gate(quality.get(s, fu.Quality(s, None, {}, [])),
                           floor, allow_unknown) for s in ranked["symbol"]]
    out = ranked[pd.Series(keep, index=ranked.index)].reset_index(drop=True)
    out["rank"] = out.index + 1
    return out


def unknown_quality_names(ranked: pd.DataFrame) -> list[str]:
    """Holdings riding on no fundamentals at all.

    Surfaced every run rather than buried: if the gate is letting names through
    only because nothing is known about them, that is a fact about your
    portfolio you should be reminded of, not a silent default.
    """
    if ranked.empty or "quality" not in ranked.columns:
        return []
    # pandas turns a None in a numeric column into NaN, so `is None` silently
    # matches nothing -- exactly the kind of quiet failure this function exists
    # to prevent.
    return [r.symbol for r in ranked.itertuples() if pd.isna(r.quality)]
