# Strategy

Cross-sectional momentum, fundamentals, and the lender rubric.

[← back to the README](../README.md)

---

One command answers the four questions in the order they matter -- what the
market is doing, what needs attention in your book, what to own, what to change:

```bash
PYTHONPATH=. .venv/bin/python run_guide.py --universe nifty200 \
    --capital 1000000 --total-capital 3000000 --rebalance
```

## Cross-sectional momentum (the strategy change)

```bash
PYTHONPATH=. .venv/bin/python run_momentum.py --universe nifty200 --shuffle 5
PYTHONPATH=. .venv/bin/python run_momentum.py --universe nifty200 \
    --exclude-universe nifty50            # out-of-sample run
```

v1 tried to time entries and exits and lost because it sat out 70% of the
time. Momentum never asks "should I be in this stock?" -- it asks "which are
strongest?" and holds the top slice, always, rotating monthly. 9-1 momentum
(9 months excluding the most recent, because short-horizon returns reverse;
12-1 is shown alongside for comparison),
divided by realised volatility, gated on being above the 200 DMA. The skipped
month and NSE's own Nifty200 Momentum 30 scoring were both tested against
alternatives; see [validation](validation.md).

*Superseded.* An early NIFTY 50 run showed 15.19% CAGR, but that universe was
today's index members applied backwards -- survivorship bias worth roughly 14
points. The survivorship-free figure is **11.7%** on the 9-month ranking
(10.9% to 15.9% depending on which days the rebalance falls, 12.5% on
average; the 12-month ranking averages the same), against 10.4% for the
NIFTY 500; see [validation](validation.md). No market-regime rule: holding
fewer stocks in RISK-OFF was tested and dropped.

## Fundamentals: NSE primary, Yahoo for the gaps

```bash
PYTHONPATH=. .venv/bin/python fetch_fundamentals.py            # all symbols
PYTHONPATH=. .venv/bin/python fetch_fundamentals.py --yahoo-only  # fast, no pledge
```

jugaad-data does **not** cover fundamentals -- prices, bhavcopy, indices and
derivatives only. NSE's own endpoints do, in two layers: a JSON API carrying
metadata and a link, and the XBRL filing carrying the numbers.

| source | supplies | why it wins there |
|---|---|---|
| **NSE** | revenue, PAT, interest cover, promoter %, **pledge %** | Authoritative, always in rupees, and the only source for pledge |
| **Yahoo** | ROE, ROCE, debt/equity, P/E, CFO | Ratios NSE does not publish directly |

Three traps this navigates, each of which silently corrupted the sheet before
it was caught:

1. **Currency.** Yahoo reports Indian IT companies in USD. Converting dollars
   as rupees understated HCLTECH's revenue 85-fold, and mixing a rupee CFO with
   a dollar PAT would have made CFO/PAT wrong by the same factor. Absolute
   values now come from NSE, or from Yahoo only when it reports INR.
2. **Period basis.** NSE files quarterly; Yahoo's cash flow is annual. Pairing
   them reports a ~4x inflated CFO/PAT as excellent cash conversion. The sheet
   carries an explicit `period` column and the score refuses to compute cash
   quality unless both sides agree.
3. **Pledge extraction.** A shareholding filing repeats its pledge tags across
   ~240 contexts -- per promoter entity, once for the group, then padded with
   zeros. Taking the last occurrence returned 0 for a company that genuinely
   has a pledge. It is now computed from the group total against promoter
   holding, the way India quotes it (pledge as % of *promoter* stake, not of
   total shares -- the two differ by roughly the promoter stake).

`pledged: False` (the filing declares no encumbrance) and `pledged: None`
(unreadable) are kept distinct throughout. "No pledge" and "we don't know" are
different facts about a holding.

## Fundamentals sheet

`config/fundamentals.csv` -- one row per stock per quarter, hand-entered from
results you already read. Six numbers matter most: CFO vs PAT (profits that
never become cash), promoter pledge %, debt/equity, interest cover, ROCE, and
P/E against the stock's own history. Missing values are dropped from the score
rather than treated as zero, and every run names the stocks riding on no
fundamentals at all -- "unknown" must never read as "acceptable".

Lenders are flagged and suppressed rather than scored: debt/equity and CFO are
meaningless for a bank.

## Risk monitor

The v1 exit engine pointed at holdings instead of trades. Stop levels, trailing
stops, score collapse, drawdown from peak, sector concentration, correlation
clusters. Everything is a **review prompt, not an instruction** -- acting
mechanically on these signals is exactly what lost money in v1.

---

See also: [validation](validation.md) for what these rules actually returned.
