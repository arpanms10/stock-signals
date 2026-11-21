# Core–Satellite platform: plan against the master spec

Started 2026-11-21. Maps the pasted master prompt onto what
stock-signals already does, and proposes how to build the rest.

## 1. What already exists (reuse, don't rebuild)

| Spec section | Already in the repo | Gap |
|---|---|---|
| §3 Ingestion | `scripts/import_holdings.py`, `book/userfiles.py`, `book/portfolio.py` (xlsx/csv, Kite/Console export, LT/ST split) | REITs/InvITs/bonds/ETFs are **skipped** on import; no preview/validation report; no audit trail; no snapshots |
| §4 Market data | `data/market.db` (survivorship-free bhavcopy, splits/bonus/demergers/ticker changes), `signals.db`, freshness chip | Market cap / free float per holding, beta, dividends, per-value provenance |
| §4 Fundamentals | NSE XBRL results since 2020 + shareholding + pledge (`data/sources/nse_fundamentals.py`), Yahoo ratios | Only ~5 yrs quarterly from NSE; 5-yr annual statements only via Yahoo (~4 yrs); no provenance per metric |
| §5 Scoring | Quality score (`scoring/fundamentals.py`): growth, returns, balance sheet, cash, governance; lender + NBFC rubrics | Not the 100-pt layout; no moat / mgmt / industry / valuation components |
| §6 Sector models | `analysis/sector.py` checklists (lender, IT, FMCG, pharma, cement, capital goods…) with "manual" items named | Descriptive only, not scored |
| §7 Classification | `strategy/buckets.py` core / satellite / trade (by horizon, set by you) | No quality-tier axis; all 32 holdings currently have a blank bucket (= satellite) |
| §8 Portfolio analysis | `strategy/allocation.py` (drift, concentration, correlation clusters), sector cap 25% in the advisor | Market-cap split, portfolio vol/beta/drawdown, 70:30 view, REIT sleeve |
| §10 Recommendations | `strategy/advisor.py` Buy/Add/Trim/Exit/Hold with reasons, cash-constrained, decision log scored at 21 days | Spec's 8-label vocabulary; valuation-separated reasoning |
| §11 Valuation | P/E, PEG in `analysis/fundamental.py` and `picker/` | Historical-band, peer-relative, scenarios, margin of safety |
| §12 Tax | `strategy/tax.py`: STCG 20%, LTCG 12.5% over ₹1.25L, LT-first delivery, near-LTCG wait, tranches | Lot-level (FIFO) cost basis, 3/6/12-month plans, contribution sensitivity |
| §13 Monitoring | `results/` engine (quarterly results, reaction), `nse_events.py` | Announcements/pledge/auditor/promoter change → thesis-change flags |
| §15 UI | Streamlit dashboard, Today / Stocks / Analyze pages | Overview, optimisation/simulation and research-history pages |

Your book today: 32 holdings, including **3 REITs** (EMBASSY-RR,
MINDSPACE-RR, NXST-RR) that the spec wants as a separate sleeve.

## 2. Where the spec conflicts with this project

1. **Stack.** Spec suggests Spring Boot + React + Postgres + OpenAI, but §16.1
   and §21.4 say keep the existing stack. Proposal: stay Python + Streamlit +
   SQLite, local-only. A new `data/research.db` holds the spec's entities
   (snapshots, metrics with provenance, scores, recommendations, audit log).
   "Secure auth / multi-user / rate limiting" (§18) don't apply to a
   single-user local app; noted as not applicable rather than built.
2. **Unvalidated scores driving actions.** The project rule is that nothing
   ranks until a backtest passes. GARP (t=0.3) and QARP (lagged) failed
   point-in-time; the Quality score has never been shown to pick winners. The
   spec's 100-pt score → ACCUMULATE / EXIT would be an untested ranking.
   Proposal: the score is a **descriptive scorecard and role classifier**; it
   may drive sizing caps and red-flag exits (as today), but ACCUMULATE vs
   HOLD stays a quality + valuation + underweight rule, stated as such, until
   a pre-registered backtest of the computable components passes.
3. **Data the free sources don't have.** Moat evidence (15 pts), management
   credibility, industry outlook (10 pts), order books, USFDA observations,
   deal wins, REIT occupancy/WALE/NAV, forward estimates. No fabrication
   allowed (§4.3, §14.1), so these come from a **research inputs file** you
   fill (value + source + date per item) or stay unscored. The score is shown
   as e.g. "58/75 computable, 25 pts unscored" — never imputed.
4. **Buckets.** Your Core/Satellite means holding *horizon* (Core 10y+,
   Satellite 1–10y, never sold on rank; Trade rotates). The spec's means
   *quality/role*. Proposal: keep your label as the portfolio role (you set
   it; the engine suggests), add the spec's quality tier as the second axis
   (§7 asks for both), and measure 70:30 on your labels. Trade counts inside
   Satellite for the 70:30 maths.

**Decided 2026-11-21:** keep Python/Streamlit/SQLite; score is descriptive
until its backtest passes; 70:30 measured on your bucket labels (engine
suggests, you confirm); Phase 7 assistant = Claude Code over exported
structured JSON, no API key.

## 3. Phases (one commit each, feature branch `core-satellite`, worktree)

**P1 · Ingestion & overview.** *(done: [overview](overview.md))* Security-type detection (EQ / REIT `-RR` /
InvIT `-IV` / ETF `INF` / bond / SGB / rights / suspended) instead of
skipping; REIT/InvIT sleeve; import preview + validation report (duplicates,
unknown symbol/ISIN, qty×avg vs invested mismatch, symbol changes); never
overwrite without confirm; `research.db` with portfolio snapshots + audit log;
Overview page (value, P/L, 70:30 vs target, REIT sleeve, market-cap and sector
split, top-5/10). Decimal for money.

**P2 · Data with provenance.** *(done: [holding data](holding_data.md); cap band still the NSE-index proxy, AMFI file unreachable)* `FinancialMetric` rows (value, period, unit,
source, retrieved_at, reported|calculated|estimated, confidence) wrapping the
existing NSE/Yahoo fetchers; market cap & cap bucket (SEBI AMFI list: top 100
large, 101–250 mid, rest small, from the half-yearly AMFI file); beta, 52-wk,
avg volume from market.db; dividends where Yahoo has them; freshness per value.

**P3 · Scorecard.** *(done: [scorecard](scorecard.md); valuation points from P4; judgment items from config/research_inputs + sector_outlook)* 100-pt framework per §5 with configurable weights in
`config/scorecard.yaml`; each component's methodology documented and unit-
tested; sector-specific models (bank, NBFC, insurance, IT, FMCG, industrials,
pharma, real estate/hotels, cyclicals) reusing `sector.py` + lender rubric;
governance override (pledge rise, auditor resignation, qualified opinion,
promoter selling) that caps the tier regardless of score; two-axis
classification; research inputs file for the manual components.

**P4 · Valuation.** *(done: [valuation](valuation.md); feeds the scorecard's 15 valuation points)* Multiple vs the stock's own 5-yr band (from market.db
prices + filed EPS/BV), vs sector peers, and a **reverse DCF** (growth the
price implies) as the main read; bear/base/bull fair values from explicit
config assumptions, DCF only where FCF is stable and positive, P/B for
lenders, NAV/yield for REITs; margin of safety. Labelled "scenario, not a
target".

**P5 · Risk & optimisation.** Portfolio vol, beta, max drawdown, correlation
of top holdings (historical, labelled as such); position caps 8/4/2%; sector
25%; constrained optimiser (contributions-first, turnover + tax penalty,
quality/valuation tilt) producing target weights with per-constraint
explanation; simulation of changes without touching holdings.

**P6 · Tax-aware transition.** Lot-level FIFO from a broker tradebook import
(falls back to the LT/ST split); rates checked against the Finance Act in
force; 3/6/12-month plans; monthly-amount sensitivity only if you give an
amount (otherwise none assumed); brokerage/STT configurable.

**P7 · Research assistant.** Depends on decision 3 below.

**P8 · Monitoring & reviews.** NSE announcements, shareholding/pledge deltas,
auditor changes, results → "thesis check" events with source link; review
report; snapshot-to-snapshot diff of scores, roles and recommendations.

**Validation step (after P3/P4).** Pre-registered backtest of the computable
score components on the PIT top-500 universe, 2020+ filings (no bear market in
the window — stated). Outcome decides whether the score may rank.

## 4. Out of scope / pending

Broker API sync (Kite on hold, cost), live prices, forward consensus
estimates, market-share and moat data, Postgres/Docker/auth/multi-user.
Each is behind a provider stub marked "pending", never mock data.
