"""Sector checklist: the long-term questions that differ by sector.

The Quality Score asks every company the same things (growth, returns,
leverage, cash conversion, governance). That's right as far as it goes, but
what decides a bank is asset quality, what decides a cement maker is the
cycle and its debt, and what decides an IT company is margins and cash. So
each sector gets its own short list:

  checks   computed from the figures on file (NSE filings, Yahoo), each with
           a rule-of-thumb reading -- good / watch / weak -- and why it matters
           in that sector. The thresholds are conventions, not backtested.
  manual   what matters but no free source publishes in a usable form
           (order book, VNB, ARPU, USFDA observations ...): named, with where
           to look, rather than guessed.
  value_on which multiple to read the price by: P/B for lenders, EV/EBITDA for
           cyclicals (P/E looks cheapest at the top of the cycle), P/E against
           growth for the rest.

Describes; does not rank or predict. Pure: no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass, field

GOOD, WATCH, WEAK = "good", "watch", "weak"

# NSE "Industry" (the Nifty 500 list) -> group.
NSE_GROUPS = {
    "information technology": "it",
    "fast moving consumer goods": "fmcg",
    "healthcare": "pharma",
    "automobile and auto components": "auto",
    "capital goods": "capital_goods",
    "construction": "capital_goods",
    "construction materials": "cement",
    "metals & mining": "metals",
    "chemicals": "chemicals",
    "oil gas & consumable fuels": "energy",
    "power": "power",
    "telecommunication": "telecom",
    "realty": "real_estate",
    "consumer durables": "consumer",
    "consumer services": "consumer",
    "textiles": "consumer",
}
# Yahoo industry keywords, checked first: they split Financial Services.
YAHOO_INDUSTRY = [
    ("banks", "bank"), ("insurance", "insurance"), ("credit services", "nbfc"),
    ("mortgage", "nbfc"), ("asset management", "amc"), ("capital markets", "amc"),
    ("information technology", "it"), ("software", "it"),
    ("drug", "pharma"), ("medical", "pharma"), ("biotech", "pharma"),
    ("auto", "auto"), ("building materials", "cement"),
    ("steel", "metals"), ("aluminum", "metals"), ("metals & mining", "metals"),
    ("copper", "metals"), ("coal", "energy"), ("oil & gas", "energy"),
    ("utilities", "power"), ("telecom", "telecom"), ("real estate", "real_estate"),
    ("chemicals", "chemicals"), ("household & personal", "fmcg"),
    ("packaged foods", "fmcg"), ("beverages", "fmcg"), ("tobacco", "fmcg"),
    ("confectioners", "fmcg"), ("engineering & construction", "capital_goods"),
    ("specialty industrial machinery", "capital_goods"),
    ("electrical equipment", "capital_goods"), ("aerospace & defense", "capital_goods"),
]
YAHOO_SECTOR = {"technology": "it", "consumer defensive": "fmcg", "healthcare": "pharma",
                "utilities": "power", "communication services": "telecom",
                "real estate": "real_estate", "energy": "energy",
                "basic materials": "metals", "consumer cyclical": "consumer",
                "industrials": "capital_goods"}

LABEL = {"bank": "Bank", "nbfc": "Non-bank lender", "insurance": "Insurance",
         "amc": "Asset manager / capital markets", "it": "IT services",
         "fmcg": "FMCG", "pharma": "Pharma and healthcare", "auto": "Auto",
         "capital_goods": "Capital goods and construction", "cement": "Cement",
         "metals": "Metals and mining", "chemicals": "Chemicals",
         "energy": "Oil, gas and coal", "power": "Power and utilities",
         "telecom": "Telecom", "real_estate": "Real estate",
         "consumer": "Consumer discretionary", "other": "General"}

CYCLICAL = {"cement", "metals", "chemicals", "energy"}

MANUAL = {
    "bank": ["Slippages and restructured book (quarterly investor presentation)",
             "Loan growth against deposit growth -- the credit/deposit ratio",
             "Unsecured retail and MFI share of loans"],
    "nbfc": ["Asset-liability maturity mismatch (ALM statement in the annual report)",
             "Cost of funds and borrowing mix -- bank lines, NCDs, commercial paper",
             "Stage 2 and stage 3 assets, AUM growth by product (investor presentation)"],
    "insurance": ["VNB margin and embedded value growth (investor presentation)",
                  "13th and 61st month persistency", "Solvency ratio (IRDAI disclosure)",
                  "Combined ratio, for general insurers"],
    "amc": ["AUM share and equity mix (AMFI monthly data)",
            "Yield on AUM and SIP flows"],
    "it": ["Constant-currency revenue growth and guidance",
           "Deal wins (TCV) and the top-client share of revenue",
           "Attrition and utilisation (quarterly fact sheet)"],
    "fmcg": ["Volume growth against price growth (management commentary)",
             "Ad and promotion spend as a share of sales",
             "Rural against urban demand, distribution reach"],
    "pharma": ["USFDA inspections: Form 483s, warning letters, import alerts",
               "R&D as a share of sales; ANDA and specialty pipeline",
               "US price erosion and the India / US revenue mix"],
    "auto": ["Monthly volumes and market share (SIAM, company releases)",
             "Dealer inventory days; EV readiness",
             "Financing arm's book, if consolidated"],
    "capital_goods": ["Order book against revenue (book-to-bill) and order inflow",
                      "Execution pace and working-capital days (receivables)",
                      "Government against private order mix"],
    "cement": ["EBITDA per tonne and cost per tonne (investor presentation)",
               "Capacity utilisation and expansion plans"],
    "metals": ["EBITDA per tonne; realisation against cost",
               "Capacity, captive raw material, and the global price cycle"],
    "chemicals": ["Capacity utilisation and new capacity coming",
                  "Customer concentration; China pricing pressure"],
    "energy": ["Refining (GRM) and marketing margins; government pricing",
               "Reserve life, for upstream producers"],
    "power": ["Plant load factor; regulated return on equity",
              "Receivables from state discoms", "Capex pipeline and its funding"],
    "telecom": ["ARPU and subscriber additions (TRAI monthly data)",
                "Capex intensity; spectrum dues"],
    "real_estate": ["Pre-sales (bookings) and collections",
                    "Unsold inventory in months; land bank"],
    "consumer": ["Same-store sales growth, store additions",
                 "Inventory days; brand and pricing power"],
    "other": [],
}


@dataclass
class Check:
    label: str
    value: str
    reading: str | None        # good / watch / weak / None (no threshold)
    why: str


@dataclass
class Checklist:
    group: str
    label: str
    value_on: str
    checks: list[Check] = field(default_factory=list)
    manual: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)   # wanted, not on file


def group_of(nse_sector: str = "", yahoo_sector: str = "", yahoo_industry: str = "",
             kind: str = "company") -> str:
    if kind == "bank":
        return "bank"
    ind = (yahoo_industry or "").lower()
    for key, g in YAHOO_INDUSTRY:
        if key in ind:
            return g
    nse = (nse_sector or "").strip().lower()
    if nse in NSE_GROUPS:
        return NSE_GROUPS[nse]
    if nse == "financial services" or (yahoo_sector or "").lower() == "financial services":
        return "nbfc" if kind == "nbfc" else "amc"
    return YAHOO_SECTOR.get((yahoo_sector or "").lower(), "other")


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def _band(x, good, weak, higher_better=True):
    """good if past `good`, weak if past `weak` the wrong way, else watch."""
    if x is None:
        return None
    if higher_better:
        return GOOD if x >= good else WEAK if x < weak else WATCH
    return GOOD if x <= good else WEAK if x > weak else WATCH


def figures(cur: dict, info: dict) -> dict:
    """Every figure a check can use, in one flat dict (percent as percent)."""
    g = lambda k: _f(cur.get(k))  # noqa: E731
    i = lambda k: _f(info.get(k))  # noqa: E731
    pct = lambda v: None if v is None else 100 * v  # noqa: E731
    ebitda, debt, cash = i("ebitda"), i("totalDebt"), i("totalCash")
    net_debt = None if debt is None else debt - (cash or 0)
    pat = i("netIncomeToCommon")
    fcf = i("freeCashflow")
    cfo, pat_a = g("cfo"), g("pat_annual")
    return {
        "roe": g("roe"), "roce": g("roce"), "debt_equity": g("debt_equity"),
        "interest_cover": g("interest_cover"),
        "cfo_pat": cfo / pat_a if cfo is not None and pat_a and pat_a > 0 else None,
        "gnpa": g("gnpa_pct"), "nnpa": g("nnpa_pct"), "pcr": g("pcr_pct"),
        "roa": g("roa_pct") if g("roa_pct") is not None else pct(i("returnOnAssets")),
        "car": g("car_pct"), "casa": g("casa_pct"), "credit_cost": g("credit_cost_pct"),
        "cost_income": g("cost_income_pct"),
        "gross_margin": pct(i("grossMargins")), "ebitda_margin": pct(i("ebitdaMargins")),
        "op_margin": pct(i("operatingMargins")),
        "ev_ebitda": i("enterpriseToEbitda"),
        "net_debt_ebitda": (net_debt / ebitda if net_debt is not None and ebitda
                            and ebitda > 0 else None),
        "fcf_pat": fcf / pat if fcf is not None and pat and pat > 0 else None,
        "revenue_growth": pct(i("revenueGrowth")),
        "pb": i("priceToBook"), "pe": i("trailingPE"),
        "dividend_yield": (None if i("dividendYield") is None else
                           i("dividendYield") * 100 if i("dividendYield") < 0.5
                           else i("dividendYield")),
        "payout": pct(i("payoutRatio")),
    }


# Each spec: (key, label, format, (good, weak, higher_better) or None, why).
P = "{:.1f}%"
X = "{:.2f}×"
SPECS = {
    "bank": [
        ("roa", "Return on assets", "{:.2f}%", (1.0, 0.6, True), "a bank earning under 1% on its assets struggles to grow capital"),
        ("gnpa", "Gross NPA", "{:.2f}%", (2.5, 5.0, False), "bad loans before provisions"),
        ("pcr", "Provision coverage", "{:.0f}%", (70, 50, True), "how much of the bad book is already written down"),
        ("car", "Capital adequacy", P, (16, 13, True), "the buffer before it must raise equity"),
        ("casa", "CASA share of deposits", P, (40, 30, True), "cheap deposits are the lasting edge in banking"),
        ("cost_income", "Cost / income", "{:.0f}%", (45, 55, False), "operating efficiency"),
        ("pb", "Price / book", X, None, "lenders are valued on book, not earnings"),
    ],
    "nbfc": [
        ("roa", "Return on assets", "{:.2f}%", (2.0, 1.0, True), "NBFCs need a higher RoA than banks to cover costlier funding"),
        ("gnpa", "Gross stage-3 / NPA", "{:.2f}%", (3.0, 6.0, False), "bad loans before provisions"),
        ("roe", "Return on equity", P, (15, 10, True), "returns on the equity that backs the book"),
        ("pb", "Price / book", X, None, "lenders are valued on book, not earnings"),
    ],
    "insurance": [
        ("roe", "Return on equity", P, (15, 10, True), "profitability of the float"),
        ("pb", "Price / book", X, None, "a stand-in for price / embedded value, which is not on file"),
    ],
    "amc": [
        ("op_margin", "Operating margin", P, (40, 25, True), "an asset-light model should run high margins"),
        ("roe", "Return on equity", P, (25, 15, True), "little capital is needed to grow AUM"),
        ("payout", "Dividend payout", "{:.0f}%", None, "excess cash should come back to holders"),
    ],
    "it": [
        ("op_margin", "Operating margin", P, (20, 14, True), "pricing power and utilisation show up here first"),
        ("fcf_pat", "Free cash flow / profit", X, (0.8, 0.6, True), "services businesses should turn profit into cash"),
        ("roe", "Return on equity", P, (25, 15, True), "asset-light: returns should be high"),
        ("revenue_growth", "Revenue growth (latest)", P, (10, 0, True), "in rupees, so currency moves are mixed in"),
        ("payout", "Dividend payout", "{:.0f}%", None, "mature IT returns most of its cash"),
    ],
    "fmcg": [
        ("gross_margin", "Gross margin", P, (45, 35, True), "brand strength: what is left after raw materials"),
        ("op_margin", "Operating margin", P, (18, 10, True), "after ad spend and distribution"),
        ("roce", "Return on capital", P, (30, 18, True), "good FMCG compounds on little capital"),
        ("cfo_pat", "Cash flow / profit", X, (0.9, 0.7, True), "low working capital should mean near-full conversion"),
        ("revenue_growth", "Revenue growth (latest)", P, (8, 3, True), "price and volume together -- see below for the split"),
    ],
    "pharma": [
        ("gross_margin", "Gross margin", P, (55, 40, True), "specialty and branded products carry higher margins than generics"),
        ("ebitda_margin", "EBITDA margin", P, (22, 15, True), "after R&D and SG&A"),
        ("net_debt_ebitda", "Net debt / EBITDA", X, (1.0, 2.5, False), "acquisitions are the usual way pharma takes on debt"),
        ("roce", "Return on capital", P, (18, 12, True), "R&D that is not paying back shows here"),
    ],
    "auto": [
        ("ebitda_margin", "EBITDA margin", P, (12, 8, True), "commodity costs and pricing power"),
        ("net_debt_ebitda", "Net debt / EBITDA", X, (0.5, 2.0, False), "the cycle punishes leverage; a lending arm can distort this"),
        ("roce", "Return on capital", P, (18, 12, True), "capital-heavy plants need high utilisation"),
        ("ev_ebitda", "EV / EBITDA", X, None, "cyclical earnings: value on EBITDA, not one year's P/E"),
    ],
    "capital_goods": [
        ("cfo_pat", "Cash flow / profit", X, (0.8, 0.5, True), "profit stuck in receivables is the classic failure here"),
        ("op_margin", "Operating margin", P, (12, 7, True), "execution and pricing on fixed-price contracts"),
        ("debt_equity", "Debt / equity", X, (0.5, 1.0, False), "working capital is often debt-funded"),
        ("roce", "Return on capital", P, (18, 12, True), ""),
    ],
    "cement": [
        ("ev_ebitda", "EV / EBITDA", X, None, "value on this, not P/E: P/E looks cheapest at the top of the cycle"),
        ("net_debt_ebitda", "Net debt / EBITDA", X, (1.0, 3.0, False), "capacity is built on debt; a downturn tests it"),
        ("ebitda_margin", "EBITDA margin", P, (20, 13, True), "a stand-in for EBITDA per tonne"),
        ("roce", "Return on capital", P, (15, 10, True), "through the cycle, not at one point"),
    ],
    "metals": [
        ("ev_ebitda", "EV / EBITDA", X, None, "value on this, not P/E: P/E looks cheapest at the top of the cycle"),
        ("net_debt_ebitda", "Net debt / EBITDA", X, (1.0, 3.0, False), "commodity prices swing EBITDA; debt does not swing with it"),
        ("ebitda_margin", "EBITDA margin", P, (20, 12, True), "where the cost curve puts the company"),
        ("interest_cover", "Interest cover", X, (5, 2.5, True), "the margin of safety in a downturn"),
    ],
    "chemicals": [
        ("ebitda_margin", "EBITDA margin", P, (20, 12, True), "specialty over commodity shows here"),
        ("net_debt_ebitda", "Net debt / EBITDA", X, (1.0, 2.5, False), "capex cycles are often debt-funded"),
        ("roce", "Return on capital", P, (18, 12, True), "new capacity must earn its cost"),
        ("ev_ebitda", "EV / EBITDA", X, None, "cyclical: value on EBITDA"),
    ],
    "energy": [
        ("ev_ebitda", "EV / EBITDA", X, None, "cyclical: value on EBITDA, not P/E"),
        ("net_debt_ebitda", "Net debt / EBITDA", X, (1.5, 3.0, False), "refining and upstream are capital-heavy"),
        ("dividend_yield", "Dividend yield", "{:.2f}%", None, "state-owned producers are often owned for the payout"),
        ("roce", "Return on capital", P, (15, 10, True), ""),
    ],
    "power": [
        ("debt_equity", "Debt / equity", X, (1.5, 2.5, False), "regulated assets carry debt; too much eats the allowed return"),
        ("interest_cover", "Interest cover", X, (3, 1.8, True), ""),
        ("roe", "Return on equity", P, (14, 10, True), "regulated returns are around 15.5% on equity"),
        ("dividend_yield", "Dividend yield", "{:.2f}%", None, ""),
        ("pb", "Price / book", X, None, "regulated utilities are valued on book"),
    ],
    "telecom": [
        ("net_debt_ebitda", "Net debt / EBITDA", X, (2.0, 3.5, False), "spectrum and network debt"),
        ("ebitda_margin", "EBITDA margin", P, (45, 35, True), "scale economics show here"),
        ("ev_ebitda", "EV / EBITDA", X, None, "the sector's usual multiple"),
    ],
    "real_estate": [
        ("debt_equity", "Debt / equity", X, (0.3, 0.8, False), "unsold inventory funded by debt is how developers fail"),
        ("cfo_pat", "Cash flow / profit", X, None, "lumpy: revenue is recognised at handover, cash comes at booking"),
        ("pb", "Price / book", X, None, "a rough stand-in for price / NAV"),
    ],
    "consumer": [
        ("op_margin", "Operating margin", P, (12, 6, True), ""),
        ("roce", "Return on capital", P, (18, 12, True), "store or plant capital must earn its cost"),
        ("cfo_pat", "Cash flow / profit", X, (0.8, 0.5, True), "inventory build-up shows here"),
        ("revenue_growth", "Revenue growth (latest)", P, (12, 5, True), ""),
    ],
    "other": [],
}
VALUE_ON = {"bank": "price / book", "nbfc": "price / book", "insurance": "price / embedded value",
            "amc": "P/E against AUM growth", "power": "price / book and dividend yield",
            "real_estate": "price / NAV", "telecom": "EV / EBITDA"}


def checklist(group: str, cur: dict, info: dict) -> Checklist:
    f = figures(cur, info)
    value_on = VALUE_ON.get(group, "EV / EBITDA, at mid-cycle" if group in CYCLICAL
                            else "P/E against growth")
    out = Checklist(group, LABEL.get(group, group), value_on, manual=list(MANUAL.get(group, [])))
    for key, label, fmt, band, why in SPECS.get(group, []):
        x = f.get(key)
        if x is None:
            out.missing.append(label)
            continue
        reading = _band(x, *band) if band else None
        out.checks.append(Check(label, fmt.format(x), reading, why))
    return out
