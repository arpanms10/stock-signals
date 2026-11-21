"""Financial statements with provenance: every value says where it came from.

Two sources, in the order the platform trusts them:

  NSE XBRL filings   the company's own quarterly results as filed with the
                     exchange (results/filings.py): P&L every quarter, balance
                     sheet and cash flow with the September and March results.
                     NSE carries them from 2020, so about five fiscal years.
                     Confidence high.
  Yahoo Finance      annual statements, four to five years: fills what the
                     quarterly filings do not carry (current assets and
                     liabilities, working capital, investing and financing
                     cash flow) and cross-checks the rest. Secondary, so
                     confidence medium until it agrees with NSE.

Then calculated rows -- fiscal-year sums of four filed quarters, TTM, margins,
ROE, ROCE, leverage, cash conversion, growth and CAGR -- each naming its
inputs in `note`. A metric that does not apply (debt/equity for a bank) is
stored as not_applicable; one that could not be found as missing. Neither is
ever stored as zero.

Pure functions over plain rows; the fetching lives in fetch_* at the bottom.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from decimal import Decimal, InvalidOperation

NSE = "NSE XBRL filing"
YAHOO = "Yahoo Finance"
CALC = "calculated"
CR = "₹ cr"
PCT = "%"
CRORE = Decimal(10_000_000)

HIGH, MEDIUM, LOW = "high", "medium", "low"
OK, MISSING, NA = "ok", "missing", "not_applicable"

# Filing field -> (metric, unit). pat/eps resolved separately (several tags).
Q_FIELDS = {"revenue": CR, "other_income": CR, "pbt": CR, "tax": CR,
            "finance_costs": CR, "depreciation": CR, "exceptional": CR,
            "interest_earned": CR, "interest_expended": CR, "ppop": CR,
            "provisions": CR, "gnpa_pct": PCT, "nnpa_pct": PCT}
INSTANT_FIELDS = {"assets": CR, "cash": CR, "deposits": CR, "advances": CR,
                  "loans": CR}

YAHOO_INCOME = {"Total Revenue": ("revenue", CR), "EBITDA": ("ebitda", CR),
                "EBIT": ("ebit", CR), "Net Income Common Stockholders": ("pat", CR),
                "Diluted EPS": ("eps", "₹"), "Interest Expense": ("finance_costs", CR),
                "Pretax Income": ("pbt", CR), "Tax Provision": ("tax", CR)}
YAHOO_BALANCE = {"Total Assets": ("assets", CR), "Total Debt": ("debt", CR),
                 "Net Debt": ("net_debt", CR), "Stockholders Equity": ("equity", CR),
                 "Cash And Cash Equivalents": ("cash", CR),
                 "Working Capital": ("working_capital", CR),
                 "Current Assets": ("current_assets", CR),
                 "Current Liabilities": ("current_liabilities", CR)}
YAHOO_CASH = {"Operating Cash Flow": ("cfo", CR), "Investing Cash Flow": ("cfi", CR),
              "Financing Cash Flow": ("cff", CR), "Capital Expenditure": ("capex", CR),
              "Free Cash Flow": ("fcf", CR)}
PER_SHARE = {"eps"}

# Metrics that mean nothing for a lender (it borrows to lend).
LENDER_NA = {"debt_equity", "net_debt", "roce_pct", "current_ratio",
             "interest_cover", "ebitda_margin_pct", "cash_conversion"}

CROSS_CHECK_TOLERANCE = Decimal("3")
RUPEE_UNITS = {CR, "₹", PCT, "x", "cr shares"}     # % difference still called agreement


def D(v) -> Decimal | None:
    if v is None:
        return None
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None
    return None if not d.is_finite() else d


def row(symbol, metric, period, period_end, value, unit, source, *, method="reported",
        confidence=HIGH, basis="", ref="", note="", status=None) -> dict:
    v = D(value)
    return {"symbol": symbol, "metric": metric, "period": period,
            "period_end": str(period_end), "source": source, "value": v,
            "unit": unit, "basis": basis, "method": method,
            "status": status or (OK if v is not None else MISSING),
            "confidence": confidence, "ref": ref, "note": note}


def fy_end(d: dt.date) -> dt.date:
    """The 31 March that ends the fiscal year containing d."""
    return dt.date(d.year + (d.month > 3), 3, 31)


def _div(a, b, scale=Decimal(1)) -> Decimal | None:
    a, b = D(a), D(b)
    if a is None or b is None or b == 0:
        return None
    return (a / b * scale).quantize(Decimal("0.0001"))


def _first(nums: dict, *keys):
    """The first non-zero of several tags for one figure, else the first
    present. Some filings carry e.g. profit-attributable-to-owners as 0
    beside the real profit; a filed 0 is kept only when nothing else is."""
    present = [nums[k] for k in keys if nums.get(k) is not None]
    return next((v for v in present if v != 0), present[0] if present else None)


# ---------------------------------------------------------------- NSE

def _debt(n: dict):
    if n.get("borrowings") is not None:
        return n["borrowings"] + (n.get("debt_securities") or 0)
    parts = [n.get("borrowings_noncurrent"), n.get("borrowings_current"),
             n.get("debt_securities")]
    if all(p is None for p in parts):
        return None
    return sum(p or 0 for p in parts)


def share_factor(end: dt.date, actions) -> Decimal:
    """How many of today's shares one share at `end` became: the product of
    split/bonus factors with an ex-date after `end`."""
    f = Decimal(1)
    for ex, factor in actions or ():
        if ex > end:
            f *= D(factor)
    return f


def from_filings(symbol: str, filings: list[dict], actions=None) -> list[dict]:
    """Rows from results/filings.history() output (newest first).

    actions: [(ex_date, factor)] splits and bonuses (market.db). EPS is put
    on today's share count, so growth is not a bonus issue in disguise.
    """
    out: list[dict] = []
    is_bank = any(f.get("kind") == "bank" for f in filings)
    by_fy: dict[dt.date, dict[dt.date, dict]] = defaultdict(dict)
    for f in filings:
        end: dt.date = f["to_date"]
        basis, ref = f.get("basis", ""), f.get("xbrl", "")
        kw = dict(basis=basis, ref=ref)
        q = lambda metric, v, unit=CR, **x: out.append(  # noqa: E731
            row(symbol, metric, "quarter", end, v, unit, NSE, **{**kw, **x}))
        for field, unit in Q_FIELDS.items():
            if f.get(field) is not None:
                q(field, f[field], unit)
        pat = _first(f, "pat_owners", "pat", "pat_bank_owners", "pat_bank")
        eps = _first(f, "eps", "eps_bank")
        q("pat", pat)
        factor = share_factor(end, actions)
        if eps is not None and factor != 1:
            eps = D(eps) / factor
            q("eps", eps, "₹", method="calculated",
              note=f"filed EPS / {factor}: adjusted to today's share count "
                   "for later splits or bonuses")
        else:
            q("eps", eps, "₹")
        pbt = _first(f, "pbt", "pbt_bank")
        ebit = ebitda = None
        if not is_bank and pbt is not None and f.get("finance_costs") is not None:
            ebit = pbt + f["finance_costs"]
            q("ebit", ebit, method="calculated", note="pbt + finance_costs")
            if f.get("depreciation") is not None:
                ebitda = ebit + f["depreciation"]
                q("ebitda", ebitda, method="calculated",
                  note="pbt + finance_costs + depreciation")
        rev = f.get("revenue") if not is_bank else f.get("interest_earned")
        if rev:
            if pat is not None:
                q("net_margin_pct", _div(pat, rev, 100), PCT, method="calculated",
                  note="pat / " + ("revenue" if not is_bank else "interest earned"))
            if ebitda is not None:
                q("ebitda_margin_pct", _div(ebitda, rev, 100), PCT, method="calculated",
                  note="ebitda / revenue")
        if pbt and f.get("tax") is not None:
            q("tax_rate_pct", _div(f["tax"], pbt, 100), PCT, method="calculated",
              note="tax / pbt")
        if is_bank and f.get("interest_earned") is not None \
                and f.get("interest_expended") is not None:
            q("nii", f["interest_earned"] - f["interest_expended"], method="calculated",
              note="interest earned - interest expended")
        # Balance sheet: half-yearly, at the quarter end.
        equity = _first(f, "equity_owners", "equity")
        if equity is None and f.get("bank_capital") is not None:
            equity = f["bank_capital"] + (f.get("bank_reserves") or 0)
        inst = lambda metric, v, unit=CR, **x: out.append(  # noqa: E731
            row(symbol, metric, "instant", end, v, unit, NSE, **{**kw, **x}))
        if equity is not None:
            inst("equity", equity)
            for field, unit in INSTANT_FIELDS.items():
                if f.get(field) is not None:
                    inst(field, f[field], unit)
            debt = _debt(f)
            if debt is not None:
                inst("debt", debt, method="calculated",
                     note="borrowings (current + non-current) + debt securities")
                if not is_bank:
                    if f.get("cash") is not None:
                        inst("net_debt", debt - f["cash"], method="calculated",
                             note="debt - cash and equivalents")
                    inst("debt_equity", _div(debt, equity), "x", method="calculated",
                         note="debt / equity")
        if f.get("shares_cr") is not None:
            inst("shares_cr", f["shares_cr"], "cr shares", method="calculated",
                 note="paid-up capital / face value")
        # Cash flow, 1 April to the quarter end.
        months = f.get("ytd_months")
        if months in (6, 12) and f.get("cfo_ytd") is not None:
            period = "fy" if months == 12 else "half"
            pe = end
            out.append(row(symbol, "cfo", period, pe, f["cfo_ytd"], CR, NSE, **kw))
            if f.get("capex_ytd") is not None:
                out.append(row(symbol, "capex", period, pe, abs(f["capex_ytd"]), CR,
                               NSE, **kw))
                out.append(row(symbol, "fcf", period, pe, f["cfo_ytd"] - abs(f["capex_ytd"]),
                               CR, NSE, method="calculated", note="cfo - capex", **kw))
        by_fy[fy_end(end)][end] = {"pat": pat, "eps": eps, "revenue": f.get("revenue"),
                                   "ebitda": ebitda, "ebit": ebit,
                                   "interest_earned": f.get("interest_earned"),
                                   "basis": basis}

    # Fiscal-year sums of four filed quarters, on one basis.
    for fye, qs in by_fy.items():
        if len(qs) != 4 or len({v["basis"] for v in qs.values()}) != 1:
            continue
        basis = next(iter(qs.values()))["basis"]
        for metric, unit in (("revenue", CR), ("pat", CR), ("ebitda", CR), ("ebit", CR),
                             ("eps", "₹"), ("interest_earned", CR)):
            vals = [v[metric] for v in qs.values()]
            if any(x is None for x in vals):
                continue
            out.append(row(symbol, metric, "fy", fye, sum((D(v) for v in vals), Decimal(0)), unit, NSE,
                           method="calculated", basis=basis,
                           note="sum of the four filed quarters"))
    # Trailing twelve months: the latest four consecutive quarters.
    ends = sorted({f["to_date"] for f in filings}, reverse=True)[:4]
    if len(ends) == 4 and (ends[0] - ends[3]).days < 300:
        latest = {f["to_date"]: f for f in filings}
        for metric, keys, unit in (("revenue", ("revenue",), CR),
                                   ("pat", ("pat_owners", "pat", "pat_bank_owners",
                                            "pat_bank"), CR),
                                   ("eps", ("eps", "eps_bank"), "₹")):
            vals = [_first(latest[e], *keys) for e in ends]
            if metric == "eps" and all(v is not None for v in vals):
                vals = [D(v) / share_factor(e, actions) for v, e in zip(vals, ends)]
            if all(v is not None for v in vals):
                out.append(row(symbol, metric, "ttm", ends[0], sum((D(v) for v in vals), Decimal(0)), unit, NSE,
                               method="calculated", basis=latest[ends[0]].get("basis", ""),
                               note=f"sum of quarters {ends[3]} to {ends[0]}"))
    return out


# -------------------------------------------------------------- Yahoo

def from_yahoo(symbol: str, statements: dict, currency: str = "INR",
               dividends=None, as_of: dt.date | None = None,
               lender: bool = False) -> list[dict]:
    """statements: {'income': df, 'balance': df, 'cash': df} as yfinance gives
    them (rows = line items, columns = period ends), amounts in rupees."""
    out = []
    foreign = currency not in ("INR", "", None)
    note = f"reported in {currency}: not compared with rupee figures" if foreign else ""
    conf = MEDIUM if not foreign else LOW
    for key, mapping in (("income", YAHOO_INCOME), ("balance", YAHOO_BALANCE),
                         ("cash", YAHOO_CASH)):
        df = statements.get(key)
        if df is None or getattr(df, "empty", True):
            continue
        for col in df.columns:
            end = col.date() if hasattr(col, "date") else col
            period = "instant" if key == "balance" else "fy"
            for item, (metric, unit) in mapping.items():
                if item not in df.index:
                    continue
                v = D(df.at[item, col])
                if v is None:
                    continue
                if metric not in PER_SHARE:
                    v = v / CRORE
                if foreign:
                    unit = f"{currency} cr" if metric not in PER_SHARE else currency
                if metric == "capex":
                    v = abs(v)
                c, nt = conf, note
                if lender and metric in ("revenue", "ebitda", "ebit"):
                    # Yahoo's "revenue" for a bank is its own construct, not
                    # interest earned or total income as filed.
                    c, nt = LOW, _join(note, "provider's definition for a lender")
                out.append(row(symbol, metric, period, end, v, unit, YAHOO,
                               confidence=c, note=nt))
    if dividends is not None and len(dividends):
        as_of = as_of or dt.date.today()
        cut = as_of - dt.timedelta(days=365)
        recent = [(d.date() if hasattr(d, "date") else d, D(v))
                  for d, v in dividends.items()]
        last12 = [(d, v) for d, v in recent if d > cut and v is not None]
        if last12:
            out.append(row(symbol, "dps_ttm", "ttm", max(d for d, _ in last12),
                           sum(v for _, v in last12), "₹", YAHOO, confidence=MEDIUM,
                           method="calculated",
                           note=f"{len(last12)} payout(s) in the 12 months to {as_of}"))
    return out


# --------------------------------------------------------- cross-check

def cross_check(rows: list[dict], tolerance: Decimal = CROSS_CHECK_TOLERANCE) -> list[str]:
    """Compare NSE fiscal-year sums with Yahoo's annual figures for the same
    year. Agreement lifts Yahoo's row to high confidence; disagreement is
    noted on both and returned."""
    idx = {(r["metric"], r["period"], r["period_end"], r["source"]): r for r in rows}
    issues = []
    doubted: set[str] = set()
    for (metric, period, end, source), y in idx.items():
        if source != YAHOO or period != "fy" or metric not in ("revenue", "pat") \
                or y["unit"] != CR:
            continue
        n = idx.get((metric, "fy", end, NSE))
        if not n or n["value"] is None or y["value"] is None or not n["value"]:
            continue
        diff = abs(y["value"] - n["value"]) / abs(n["value"]) * 100
        diff = diff.quantize(Decimal("0.1"))
        if diff <= tolerance:
            y["confidence"] = HIGH
            y["note"] = _join(y["note"], f"agrees with NSE filings within {diff}%")
            n["note"] = _join(n["note"], f"agrees with Yahoo within {diff}%")
        else:
            msg = (f"{y['symbol']} {metric} FY{end[:4]}: NSE {n['value']:.0f} vs Yahoo "
                   f"{y['value']:.0f} ₹ cr ({diff}% apart; NSE basis {n['basis'] or '?'})")
            y["note"] = _join(y["note"], f"differs from NSE by {diff}%")
            n["note"] = _join(n["note"], f"differs from Yahoo by {diff}%")
            issues.append(msg)
            doubted.add(metric)
    # One disagreement casts doubt on that provider's whole series for the
    # metric (a different basis or definition), not just the one year.
    for r in rows:
        if r["source"] == YAHOO and r["metric"] in doubted and r["confidence"] != HIGH:
            r["confidence"] = LOW
            if "differs" not in r["note"]:
                r["note"] = _join(r["note"], "series disagrees with NSE in another year")
    return issues


def _join(a: str, b: str) -> str:
    return f"{a}; {b}" if a else b


# ------------------------------------------------------------ ratios

def annual_view(rows: list[dict]) -> dict[str, dict[str, dict]]:
    """{fy_end: {metric: row}}: per fiscal year, the best row for each field.
    NSE beats Yahoo; flows are fy rows, stocks are instants on 31 March."""
    rank = {NSE: 0, YAHOO: 1}
    out: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in rows:
        if r["status"] != OK or r["unit"] not in RUPEE_UNITS:
            continue
        if r["period"] == "fy" or (r["period"] == "instant" and r["period_end"][5:] == "03-31"):
            cur = out[r["period_end"]].get(r["metric"])
            if cur is None or rank.get(r["source"], 9) < rank.get(cur["source"], 9):
                out[r["period_end"]][r["metric"]] = r
    return dict(sorted(out.items()))


def _src(*rs) -> str:
    names = sorted({f"{r['metric']} ({'NSE' if r['source'] == NSE else 'Yahoo'})"
                    for r in rs})
    return "from " + ", ".join(names)


def ratios(symbol: str, rows: list[dict], lender: bool = False) -> list[dict]:
    """Calculated fiscal-year ratios, growth and CAGR."""
    years = annual_view(rows)
    ends = list(years)
    out = []

    def add(metric, end, value, unit, inputs, note_extra="", conf=None):
        confs = {r["confidence"] for r in inputs}
        c = conf or (HIGH if confs == {HIGH} else MEDIUM if LOW not in confs else LOW)
        out.append(row(symbol, metric, "fy", end, value, unit, CALC, method="calculated",
                       confidence=c, note=_join(_src(*inputs), note_extra)))

    for i, end in enumerate(ends):
        y = years[end]
        prev = years[ends[i - 1]] if i and _one_year(ends[i - 1], end) else {}
        g = y.get
        if lender:
            for m in sorted(LENDER_NA):
                out.append(row(symbol, m, "fy", end, None, "", CALC, method="calculated",
                               status=NA, confidence=HIGH,
                               note="not meaningful for a lender"))
        rev, pat, ebit, ebitda = g("revenue"), g("pat"), g("ebit"), g("ebitda")
        eq, debt, cfo, fin = g("equity"), g("debt"), g("cfo"), g("finance_costs")
        if rev and pat:
            add("net_margin_pct", end, _div(pat["value"], rev["value"], 100), PCT,
                [pat, rev])
        if not lender and rev and ebitda:
            add("ebitda_margin_pct", end, _div(ebitda["value"], rev["value"], 100), PCT,
                [ebitda, rev])
        if pat and eq:
            peq = prev.get("equity")
            base = (eq["value"] + peq["value"]) / 2 if peq else eq["value"]
            add("roe_pct", end, _div(pat["value"], base, 100), PCT,
                [pat, eq] + ([peq] if peq else []),
                "average of opening and closing equity" if peq else "closing equity")
        if not lender and ebit and eq and debt:
            cap = eq["value"] + debt["value"]
            add("roce_pct", end, _div(ebit["value"], cap, 100), PCT, [ebit, eq, debt],
                "capital employed approximated as equity + debt", conf=None)
        if not lender and debt and eq:
            add("debt_equity", end, _div(debt["value"], eq["value"]), "x", [debt, eq])
        if not lender and ebit and fin and fin["value"]:
            add("interest_cover", end, _div(ebit["value"], fin["value"]), "x", [ebit, fin])
        if not lender and g("current_assets") and g("current_liabilities"):
            add("current_ratio", end, _div(g("current_assets")["value"],
                                           g("current_liabilities")["value"]), "x",
                [g("current_assets"), g("current_liabilities")])
        if not lender and cfo and pat and pat["value"] > 0:
            add("cash_conversion", end, _div(cfo["value"], pat["value"]), "x", [cfo, pat],
                "operating cash flow / PAT")
        for metric in ("revenue", "pat", "eps"):
            cur, old = _same_source(rows, metric, ends[i - 1], end) if prev else (None, None)
            if cur and old and old["value"] > 0:
                add(f"{metric}_growth_pct", end,
                    _div(cur["value"] - old["value"], old["value"], 100), PCT, [cur, old])

    # CAGR over 3 and 5 years, one source per series.
    if ends:
        last = ends[-1]
        for metric in ("revenue", "pat", "eps"):
            for n in (3, 5):
                start = f"{int(last[:4]) - n}{last[4:]}"
                a, b = years.get(start, {}).get(metric), years[last].get(metric)
                if not (a and b):
                    out.append(row(symbol, f"{metric}_cagr_{n}y_pct", "fy", last, None, PCT,
                                   CALC, method="calculated", confidence=HIGH,
                                   note=f"needs FY{start[:4]} and FY{last[:4]}"))
                    continue
                b, a = _same_source(rows, metric, start, last)    # (later, earlier)
                if not (a and b):
                    out.append(row(symbol, f"{metric}_cagr_{n}y_pct", "fy", last, None,
                                   PCT, CALC, method="calculated", confidence=HIGH,
                                   note="no single source covers both years"))
                    continue
                if a["value"] <= 0 or b["value"] <= 0:
                    out.append(row(symbol, f"{metric}_cagr_{n}y_pct", "fy", last, None, PCT,
                                   CALC, method="calculated", status=NA, confidence=HIGH,
                                   note="not meaningful across a loss year"))
                    continue
                cagr = (float(b["value"] / a["value"]) ** (1 / n) - 1) * 100
                add(f"{metric}_cagr_{n}y_pct", last, round(cagr, 2), PCT, [a, b],
                    f"FY{start[:4]} to FY{last[:4]}")
    return out


def _same_source(rows: list[dict], metric: str, a_end: str, b_end: str):
    """(row at b_end, row at a_end) from one source -- a growth rate across
    two sources measures their differences, not the company. NSE first.
    Yahoo EPS is not used: its share basis across bonuses is not stated."""
    by: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in rows:
        if r["metric"] == metric and r["period"] == "fy" and r["status"] == OK \
                and r["period_end"] in (a_end, b_end) and r["unit"] in RUPEE_UNITS:
            by[r["source"]][r["period_end"]] = r
    for src in (NSE, YAHOO):
        if metric == "eps" and src == YAHOO:
            continue
        pair = by.get(src, {})
        if a_end in pair and b_end in pair:
            return pair[b_end], pair[a_end]
    return None, None


def _one_year(a: str, b: str) -> bool:
    return int(b[:4]) - int(a[:4]) == 1


# ------------------------------------------------------------ fetching

def fetch_nse(symbol: str, quarters: int = 28, log=None) -> list[dict]:
    from results import filings as rf
    return rf.history(symbol, quarters=quarters, log=log, prefer_consolidated=True)


def fetch_yahoo(symbol: str) -> tuple[dict, str, object]:
    import yfinance as yf
    t = yf.Ticker(f"{symbol}.NS")
    statements = {"income": t.income_stmt, "balance": t.balance_sheet,
                  "cash": t.cashflow}
    try:
        currency = (t.info or {}).get("financialCurrency") or "INR"
    except Exception:                       # noqa: BLE001
        currency = "INR"
    try:
        dividends = t.dividends
    except Exception:                       # noqa: BLE001
        dividends = None
    return statements, currency, dividends


# ------------------------------------------------------------ freshness

RESULTS_LAG_DAYS = 60        # quarterly results are due within 45 days (60 for Q4)
RECHECK_DAYS = 14            # a value not re-retrieved for longer is flagged too


def _quarter_ends_before(d: dt.date) -> dt.date:
    for m, day in ((12, 31), (9, 30), (6, 30), (3, 31)):
        e = dt.date(d.year, m, day)
        if e <= d:
            return e
    return dt.date(d.year - 1, 12, 31)


# Trailing payouts end on the last ex-date: a company that paid in May has no
# newer period to file, so only the retrieval age says whether it is current.
PAYOUT_METRICS = {"dps_ttm"}


def freshness(r: dict, today: dt.date, latest_session: str | None) -> tuple[str, str]:
    """(fresh | stale | missing | n/a, why) for one stored row."""
    if r["status"] == NA:
        return "n/a", r.get("note", "")
    if r["status"] != OK:
        return "missing", r.get("note", "") or "not found"
    end = str(r["period_end"])
    if r["metric"] in PAYOUT_METRICS:
        pass                    # period_end is the last payout, not a results date
    elif r["period"] == "point":
        if latest_session and end < latest_session:
            return "stale", f"as of {end}; market data runs to {latest_session}"
    elif r["period"] in ("quarter", "ttm", "instant", "half"):
        due = _quarter_ends_before(today - dt.timedelta(days=RESULTS_LAG_DAYS))
        # Balance sheets are filed half-yearly: allow one more quarter.
        if r["period"] in ("instant", "half"):
            due = _quarter_ends_before(due - dt.timedelta(days=1))
        if end < due.isoformat():
            return "stale", f"period ends {end}; results to {due} should be filed by now"
    elif r["period"] == "fy":
        due = fy_end(today - dt.timedelta(days=RESULTS_LAG_DAYS) - dt.timedelta(days=366))
        if end < due.isoformat():
            return "stale", f"FY ending {end}; FY{due.year} results should be out"
    got = str(r.get("retrieved_at") or "")[:10]
    if got and (today - dt.date.fromisoformat(got)).days > RECHECK_DAYS:
        return "stale", f"not re-checked since {got}"
    return "fresh", f"period {end}, retrieved {got or '?'}"


def latest_rows(rows: list[dict]) -> list[dict]:
    """The newest row of each series (metric, period, source). Freshness is a
    question about these; older periods are history, not stale data."""
    best: dict[tuple, dict] = {}
    for r in rows:
        k = (r["metric"], r["period"], r["source"])
        if k not in best or str(r["period_end"]) > str(best[k]["period_end"]):
            best[k] = r
    return list(best.values())
