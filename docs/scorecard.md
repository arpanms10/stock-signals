# Scorecard

**Portfolio › Scorecard** gives each company share you hold a 100-point
scorecard. The points come from its filed record (see
[holding data](holding_data.md)) and from your own research inputs. Every
point shows its value, its rule and its source.

**It is descriptive, not backtested.** The weights and thresholds in
`config/scorecard.yaml` are conventions. The score summarises what has been
filed; it doesn't predict returns. The page lists holdings alphabetically, not
by score, and nothing ranks or trades on the score unless a pre-registered
backtest passes.

From the command line:

```bash
.venv/bin/python scripts/score_holdings.py              # print every card
.venv/bin/python scripts/score_holdings.py --save       # also keep a dated copy in research.db
.venv/bin/python scripts/score_holdings.py --export data/results/scorecards.json
.venv/bin/python scripts/score_holdings.py --fill-in    # create the research input files
```

## The 100 points

| Component | Points | From |
|---|---:|---|
| Revenue growth | 10 | 3- and 5-year CAGR; years of growth in the last 5 |
| PAT / EPS growth | 10 | 3-year PAT and EPS CAGR (EPS adjusted for splits and bonuses); net margin change; exceptional items as a share of PBT |
| Return on capital | 10 | ROCE latest and 3-year average; 3-year average ROE |
| Balance sheet | 10 | Debt/equity; net debt/EBITDA; interest cover |
| Cash-flow quality | 10 | Operating cash flow/PAT over 3 years; years of positive free cash flow; capex/operating cash flow |
| Competitive advantage | 15 | 7 computed: years ROCE beat the sector hurdle; EBITDA margin stability. 8 from your evidence |
| Management and governance | 10 | 4 computed: promoter holding trend; pledge. 6 from your evidence |
| Valuation | 15 | Own-band percentile, peer multiple, margin of safety, implied vs delivered growth ([valuation](valuation.md)) |
| Industry outlook | 10 | Your sector outlook, with evidence |

Each computed criterion scores linearly between a `zero` and a `full`
threshold. For example, revenue CAGR scores 0 points at 0% and all 4 points at
15%.

## Missing data

- **No data means not assessed, not zero.** A criterion without data drops
  out of the points available.
- **Score** = points earned / points assessed × 100.
- **Assessed %** = points assessed / the points that apply to the business.
  - Points that don't apply (cash flow for a bank) are left out.
- **No tier below 60% assessed.** The page says "Not enough data" instead.
- **A loss year scores zero.** Growth across a loss year gets zero points; it
  is a fact, not missing data.

With no research inputs filled in, a company with a full filing history can
reach about 76% assessed, and a bank about 64%. Some bank filings on file
don't go back far enough for 5-year growth or EPS CAGR. Filling in your
research inputs raises both.

## Sector models

Lenders are scored differently. Industrial leverage and cash-flow measures
don't describe a bank.

- **Bank:**
  - Return on capital uses ROE and return on assets.
  - Balance sheet uses gross NPA, net NPA and credit cost (provisions /
    advances).
  - Cash flow is not applicable.
- **NBFC:**
  - Return on capital uses ROE and return on assets.
  - Balance sheet uses assets/equity and credit cost.
  - Cash flow is not applicable.
- **Insurance:**
  - Return on capital uses ROE.
  - Balance sheet and cash flow are not applicable. (Solvency is the real
    measure, and it isn't in the filings on file.)

For other sectors, only the thresholds change:

| Sector | What changes |
|---|---|
| IT, FMCG | Higher ROCE and ROE bars, stricter cash conversion |
| Cement, metals, chemicals, energy (cyclical) | Lower growth-consistency and ROCE bars, more margin swing allowed |
| Power, telecom | More debt allowed |
| Real estate | Less debt allowed, lumpy cash flow allowed |
| Capital goods | Allows for working capital |
| Pharma | Lower ROCE bar |

The sector group comes from NSE's industry classification and from the
filings (a bank files NPA ratios). If NSE has no sector for a company, it gets
the General model. You can set the right one with `sector_group` in the
research inputs.

The detail view also lists the evidence worth collecting for each sector
(order book, USFDA observations, CASA and so on), taken from the sector
checklist.

## Your research inputs

Competitive advantage (8 points), governance (6) and industry outlook (10)
need judgment that no free source publishes. They come from two files in
`config/`:

- `research_inputs.xlsx` has one row per company:
  - `moat_points` with `moat_evidence`;
  - `governance_points` with `governance_evidence`;
  - the yes/no governance flags;
  - `sector_group` and `satellite_category` overrides;
  - `as_of`.
- `sector_outlook.xlsx` has one row per sector group: `outlook_points` with
  `evidence`.

**Create / update fill-in files** on the page, or `--fill-in`, adds a row per
holding and per sector and keeps what you have written. Rules:

- Points without evidence are ignored.
- Points above the maximum are capped.
- A row older than a year is marked as stale.

## Governance override

A high total never hides a serious governance concern (spec §5.8). Each flag
caps the quality tier, whatever the score.

| Flag | Source | Caps the tier at |
|---|---|---|
| Auditor resigned mid-term | research inputs | Watchlist |
| Qualified / adverse audit opinion | research inputs | Watchlist |
| Live regulatory action | research inputs | Watchlist |
| Promoter pledge ≥ 25% of promoter holding | NSE shareholding filings | Watchlist |
| Pledge up ≥ 10 pts in about 2 years | NSE shareholding filings | Watchlist |
| Promoter pledge ≥ 10% | NSE shareholding filings | Satellite candidate |
| Promoter stake down ≥ 5 pts in about 2 years | NSE shareholding filings | Satellite candidate |
| Related-party concern | research inputs | Satellite candidate |

The pledge figure is fetched with the holding data. It is read from the latest
shareholding filing and the oldest one on file.

## Two axes

**Fundamental quality tier**, from the score (spec §7):

| Score | Tier |
|---|---|
| 85 and above | High-conviction core |
| 75–84 | Core candidate |
| 65–74 | Satellite candidate |
| 55–64 | Watchlist |
| Below 55 | Exit candidate |

The governance override is applied after the tier.

**Suggested portfolio role:** where the company fits in your book. It is
**Core** only if every condition holds:

- the tier is Core candidate or better;
- there are no governance flags;
- it is large or mid cap;
- debt/equity is at most 1 (non-lenders);
- it trades at least ₹5 cr a day;
- every fiscal year on file is a profit, with at least 4 on file.

Otherwise it is **Satellite**. The page lists the conditions that failed and
suggests a satellite category: cyclical, mid-cap growth or small-cap growth,
or your own override. An Exit-candidate tier gets **Review** instead.

The role is compared with your bucket column, but only as a suggestion. You
decide the bucket in the holdings file. Trade-bucket holdings are not
compared.

## History

**Save today's scorecards** (or `--save`) keeps one dated card per company
per day in `research.db` (`scorecard` table), with a hash of the config that
produced it. The detail view lists the saved cards, so a later review can see
what changed and whether the rules changed.
