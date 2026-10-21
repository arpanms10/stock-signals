# Strategy

Cross-sectional momentum, fundamentals, and the lender rubric.

[← back to the README](../README.md)

---

One command answers the four questions in the order they matter -- what the
market is doing, what needs attention in your book, what to own, what to change:

```bash
PYTHONPATH=. .venv/bin/python scripts/run_guide.py --universe nifty200 \
    --capital 1000000 --total-capital 3000000 --rebalance
```

## Cross-sectional momentum

```bash
PYTHONPATH=. .venv/bin/python scripts/run_momentum.py --universe nifty200 --shuffle 5
PYTHONPATH=. .venv/bin/python scripts/run_momentum.py --universe nifty200 \
    --exclude-universe nifty50            # out-of-sample run
```

Momentum never asks "should I be in this stock?" -- it asks "which are
strongest?" and holds the top slice, always, rotating monthly. 9-1 momentum
(9 months excluding the most recent, because short-horizon returns reverse;
12-1 is shown alongside for comparison), divided by realised volatility,
gated on being above the 200 DMA. There is no market-regime rule. To test it
on past data, see [validation](validation.md).

## Fundamentals: NSE primary, Yahoo for the gaps

```bash
PYTHONPATH=. .venv/bin/python scripts/fetch_fundamentals.py            # all symbols
PYTHONPATH=. .venv/bin/python scripts/fetch_fundamentals.py --yahoo-only  # fast, no pledge
```

jugaad-data does **not** cover fundamentals -- prices, bhavcopy, indices and
derivatives only. NSE's own endpoints do, in two layers: a JSON API carrying
metadata and a link, and the XBRL filing carrying the numbers.

| source | supplies | why it wins there |
|---|---|---|
| **NSE** | revenue, PAT, interest cover, promoter %, **pledge %** | Authoritative, always in rupees, and the only source for pledge |
| **Yahoo** | ROE, ROCE, debt/equity, P/E, CFO | Ratios NSE does not publish directly |

Three traps this navigates, each of which would silently corrupt the sheet:

1. **Currency.** Yahoo reports Indian IT companies in USD. Converting dollars
   as rupees would understate revenue roughly 85-fold, and mixing a rupee CFO
   with a dollar PAT would make CFO/PAT wrong by the same factor. Absolute
   values come from NSE, or from Yahoo only when it reports INR.
2. **Period basis.** NSE files quarterly; Yahoo's cash flow is annual. Pairing
   them reports a ~4x inflated CFO/PAT as excellent cash conversion. The sheet
   carries an explicit `period` column and the score refuses to compute cash
   quality unless both sides agree.
3. **Pledge extraction.** A shareholding filing repeats its pledge tags across
   ~240 contexts -- per promoter entity, once for the group, then padded with
   zeros. Taking the last occurrence can return 0 for a company that genuinely
   has a pledge, so it is computed from the group total against promoter
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

Watches holdings rather than trades: stop levels, trailing stops, score
collapse, drawdown from peak, sector concentration, correlation clusters.
Everything is a **review prompt, not an instruction**.

---

See also: [validation](validation.md) for how to backtest these rules.
