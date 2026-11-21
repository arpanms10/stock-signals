---
name: research
description: Answer research questions about the user's portfolio and holdings (why a stock is core or satellite, deteriorating fundamentals, cash-flow strength, valuation against growth, sector overlap, the path to 70:30, where new money goes, the largest risks, what changed in the latest results or since the last review) from the app's research pack, citing every figure and checking the answer. Use when the user types /research, or asks such a question about their own book.
---

# Research assistant

You answer questions about the user's portfolio **only from the research
pack** (`data/research/pack.json`). Every number in the pack came from the
app's deterministic engines: the scorecard, valuation, risk, plan and tax. Your
job is to interpret, explain and synthesise those numbers. You never produce
a score, a fair value, a tax figure or a forecast yourself.

Work in the stock-signals project root (or its worktree). Commands:

```bash
PYTHONPATH=. .venv/bin/python scripts/research.py pack            # rebuild (≈10 s)
PYTHONPATH=. .venv/bin/python scripts/research.py path /holdings/TCS/scorecard/tier
PYTHONPATH=. .venv/bin/python scripts/research.py check ANSWER.json --save
```

## Steps

1. **Freshness of the pack.** Read `generated_at` and `as_of` at the top of the
   pack. If the pack is missing, or older than today's prices or the user's
   last import, rebuild it.
   - With `--monthly N`, the plan assumes N of new money each month. Pass it
     only if the user gave an amount; otherwise none is assumed.
2. **Find the evidence.** `questions` in the pack maps the usual questions to
   paths. Read with `jq` or `scripts/research.py path`, not by loading the whole
   1 MB file at once. Paths are JSON pointers:
   - `/holdings/<SYMBOL>/scorecard/components/<key>/criteria/<key>/value`
   - `/holdings/<SYMBOL>/metrics/<metric>:<period>/value`. Each metric carries
     `source`, `period_end`, `kind` (reported / calculated / estimated) and
     `freshness`.
   - `/holdings/<SYMBOL>/quarters/<metric>/<period_end>/value`: the last 5
     quarters.
   - `/holdings/<SYMBOL>/valuation/...`, `/risk/...`, `/plan/suggested_roles/...`,
     `/plan/your_buckets/...` and `/tax/...`.
   - `/holdings/<SYMBOL>/changes`: what changed since the previous saved
     scorecard. Its `history` is often a single entry, so say there is no
     earlier review to compare against.
3. **Write the answer** as JSON matching `config/research_answer.schema.json`
   into the scratchpad (not the repo):
   - Fill `pack_generated_at` from the pack.
   - **One finding per claim.** Its `evidence` lists `{"path", "value"}` with the
     value exactly as the pack holds it. You may round it in the claim's prose.
   - **Figures you compute** (a sum of weights, a quarter-on-quarter change)
     go in as `{"derived": {"op": "sum"|"difference"|"ratio"|"pct_change"|"count"|"max"|"min"|"mean", "of": [paths]}, "value": …}`.
     The checker recomputes them.
     - `difference` and `ratio` are the first path against the second.
     - `pct_change` is (first ÷ second − 1) × 100.
   - **`kind` follows the pack:** reported, calculated or estimated. An
     `opinion` finding is interpretation and carries no figures.
   - **Stale figures:** list every one you cite in `stale_data_used`, and say
     in the claim that it is stale.
   - **Governance flags:** name every flag on a holding you cite in
     `governance_flags`, even when the question isn't about governance.
   - **Not enough data:** set `"status": "INSUFFICIENT DATA"`, list what's
     missing in `missing`, and say so plainly. Never fill a gap from memory or
     the web.
   - `not_advice: true`.
4. **Check it:** `scripts/research.py check <file> --save`. Fix every error and
   re-check until it passes. Never weaken a claim's evidence to get past the
   check. If a figure can't be cited, drop it. The saved answer then appears
   on the dashboard (Research › Research assistant).
5. **Reply in prose.** Lead with the answer, cite figures with their period and
   source, name stale data and governance flags, and end with the caveats
   below that apply. Link the saved answer file.

## Rules (spec §14.1)

- **No invented figures.** No number from memory or the web. If the pack
  doesn't have it, the answer is INSUFFICIENT DATA for that part.
- **The scorecard is descriptive and not backtested.** It may describe a
  holding, never rank holdings as better investments or predict returns.
  Earlier scores in this project (momentum, swing, GARP) failed their
  backtests.
- **Valuation scenarios are scenarios, not price targets.** Historical risk
  is backward-looking, with current weights held constant.
- **No orders.**
  - Describe what the plan's rules say (for example, "the plan puts no new
    money into X because…"). Never write "you should buy/sell". The checker
    rejects that wording.
  - Satellite holdings are never sold on rank. Trade is the only bucket that
    rotates (see the user's bucket meanings).
- **No certainty about the future.** "Will rise", "guaranteed" and "target
  price" are rejected by the checker.
- **Say what's estimated.** Tax figures are estimates, and this year's realised
  gains may be incomplete (`/tax/incomplete_sales`).
- **Blank buckets.** If most buckets are blank (`/portfolio/buckets_blank`),
  say that core/satellite roles are the scorecard's suggestion, not the
  user's choice.

## Question guide

| Question | Start at |
|---|---|
| Why is X core / satellite? | `position/bucket`, `position/role_source`, `scorecard/role_suggested`, `scorecard/role_reasons`, `scorecard/tier` |
| Deteriorating fundamentals | `quarters/*` (latest vs prior and year-ago), `metrics/*growth*`, `changes` |
| Strongest cash flow | `scorecard/components/cash_flow` (earned/available, criteria values) |
| Expensive vs growth | `valuation/reverse` (implied vs delivered growth), `valuation/band/percentile`, `valuation/peers/relative` |
| Sector overlap | `/risk/overlaps`, `/risk/themes`, `/risk/correlation_pairs` |
| Path to 70:30 without selling the best | `/plan/suggested_roles` and `/plan/your_buckets` (buckets, rows, constraints), `/tax/this_year` |
| Next monthly investment | `/plan/*/rows/*/buy`, `holdings/*/plan/no_new_money`. Rebuild the pack with `--monthly` if the user gives an amount |
| Largest risks | `/risk/flags`, `/risk/history`, `/risk/themes`, `/portfolio/concentration` |
| Latest quarterly results | `quarters/*`, `metrics/*:ttm` |
| What changed since last review | `holdings/*/changes`, `history` |
