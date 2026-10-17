"""GARP and QARP: two ways of buying good businesses without overpaying.

GARP -- growth at a reasonable price (Lynch). Profit growing fast AND
lastingly, bought at a P/E not much above that growth: PEG = core P/E over
growth, with a cap. Ranked half on growth, half on how low the PEG is.

QARP -- quality at a reasonable price (Buffett, Munger; AQR's quality
factor). A business that earns well, steadily and in cash, on little debt,
bought when it is not near its own most expensive. Ranked 60% quality,
40% price.

Every component is a percentile rank within the universe on that date,
so a score of 80 means "better than 80% of peers on this", whatever the
level of the market. Banks and NBFCs are ranked only against each other on
their own measures: ROE and margins mean different things for a lender.

A component that is missing is left out of that stock's average rather than
counted as average or as bad. The eligibility gates are where missing data
does bite: a stock cannot pass a PEG gate without a PEG.
"""
from __future__ import annotations

import pandas as pd

# Column -> True when higher is better.
COMPANY_QUALITY = {"roe": True, "roce": True, "cfo_pat": True,
                   "interest_cover": True, "de": False, "margin": True,
                   "margin_vol": False, "other_income_share": False,
                   "consistency": True}
LENDER_QUALITY = {"roe": True, "roa": True, "gnpa": False, "credit_cost": False,
                  "consistency": True}
GROWTH = {"growth": True, "topline_growth": True, "consistency": True}
QARP_PRICE = {"pe_hist_pct": False, "pe_vs_sector": False, "fcf_yield": True}


def _rank(df: pd.DataFrame, cols: dict[str, bool]) -> pd.Series:
    """Mean percentile rank (0-1) over the columns a row has."""
    parts = []
    for col, higher in cols.items():
        if col in df and df[col].notna().sum() >= 5:
            r = df[col].rank(pct=True, ascending=higher)
            parts.append(r)
    if not parts:
        return pd.Series(float("nan"), index=df.index)
    return pd.concat(parts, axis=1).mean(axis=1, skipna=True)


def score(metrics: pd.DataFrame, cfg: dict, sectors: dict[str, str] | None = None
          ) -> pd.DataFrame:
    """Add quality / growth / price ranks, eligibility and the two scores.

    `cfg` is the `picker:` block of config/scoring.yaml.
    """
    df = metrics.copy()
    if df.empty:
        return df
    # A date where no stock has filed a balance sheet yet has no ROE column
    # at all; every column a rule reads must exist, if only as blanks.
    for col in {*COMPANY_QUALITY, *LENDER_QUALITY, *GROWTH, *QARP_PRICE,
                "peg", "pe", "de", "interest_cover"} - {"pe_vs_sector"}:
        if col not in df:
            df[col] = float("nan")
    sectors = sectors or {}
    df["sector"] = df["symbol"].map(sectors).fillna("")
    # P/E against sector peers on the same date; a sector needs a few names
    # for its median to mean anything.
    med = df[df["pe"] > 0].groupby("sector")["pe"].agg(["median", "size"])
    ok = med[med["size"] >= cfg["min_sector_peers"]]["median"]
    df["pe_vs_sector"] = [round(pe / ok[s], 2) if s in ok and pe and pe > 0 else None
                          for s, pe in zip(df["sector"], df["pe"])]

    df["quality"] = float("nan")
    df["growth_rank"] = float("nan")
    df["peg_rank"] = float("nan")
    df["price_rank"] = float("nan")
    for lender, qcols in ((False, COMPANY_QUALITY), (True, LENDER_QUALITY)):
        m = df["kind"].isin(["bank", "nbfc"]) == lender
        g = df[m]
        if g.empty:
            continue
        df.loc[m, "quality"] = _rank(g, qcols)
        df.loc[m, "growth_rank"] = _rank(g, GROWTH)
        df.loc[m, "peg_rank"] = _rank(g, {"peg": False})
        df.loc[m, "price_rank"] = _rank(g, QARP_PRICE)

    g = cfg["garp"]
    df["garp_ok"] = ((df["growth"] >= g["min_growth"])
                     & (df["peg"] > 0) & (df["peg"] <= g["max_peg"])
                     & (df["topline_growth"] > 0)
                     & (df["interest_cover"].fillna(99) >= g["min_interest_cover"])
                     & (df["de"].fillna(0) <= g["max_de"]))
    df["garp_score"] = (100 * (g["w_growth"] * df["growth_rank"]
                               + (1 - g["w_growth"]) * df["peg_rank"])).round(1)

    q = cfg["qarp"]
    df["qarp_ok"] = ((df["quality"] >= q["min_quality"])
                     & (df["pe"] > 0)
                     & (df["pe_hist_pct"].fillna(0) <= q["max_pe_hist_pct"])
                     & (df["growth"].fillna(0) >= q["min_growth"]))
    df["qarp_score"] = (100 * (q["w_quality"] * df["quality"]
                               + (1 - q["w_quality"]) * df["price_rank"])).round(1)
    for col in ("quality", "growth_rank", "peg_rank", "price_rank"):
        df[col] = (100 * df[col]).round(0)
    return df


def picks(scored: pd.DataFrame, method: str, n: int) -> pd.DataFrame:
    """Top `n` eligible names by that method's score."""
    if scored.empty:
        return scored
    ok = scored[scored[f"{method}_ok"].fillna(False).astype(bool)]
    return ok.sort_values(f"{method}_score", ascending=False).head(n)
