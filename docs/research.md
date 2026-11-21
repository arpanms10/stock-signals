# Research assistant

Ask questions about your portfolio in Claude Code:

```text
/research Which companies are expensive relative to their growth?
```

The assistant is Claude Code with the `/research` skill
(`.claude/skills/research/SKILL.md`). It reads one file, the **research
pack**, and answers only from it. There is no API key, and the app sends
nothing anywhere. **Research › Research assistant** shows the pack, the
questions it is laid out for, and every saved answer with its check.

## Who does what

| Job | Who |
|---|---|
| Every score, fair value, risk figure, plan and tax estimate | The app's deterministic engines (scorecard, valuation, risk, plan, tax) |
| Collecting them, with source, period, kind and freshness | `book/research_pack.py` → `data/research/pack.json` |
| Interpreting, explaining, synthesising | Claude Code, citing a pack path for every figure |
| Checking that every cited figure is really in the pack | `strategy/answer_check.py` (`scripts/research.py check`) |

## The pack

Rebuild it with `scripts/research.py pack` or the page's button. It takes
about 10 seconds and is about 1 MB. Pass `--monthly N` only if you want the
plan to assume new money.

| Section | Contents |
|---|---|
| `holdings/<SYMBOL>/position` | Quantity, cost, price, weight, bucket, and where the role came from |
| `…/scorecard` | Score, tier, suggested role and reasons, every component and criterion with value, points, rule and source; flags |
| `…/valuation` | Method, current multiples, own band, peers, reverse DCF, scenarios (marked estimated) |
| `…/metrics/<metric>:<period>` | Key figures, each with source, period end, kind (reported / calculated / estimated), confidence, filing link and freshness |
| `…/quarters/<metric>/<quarter end>` | The last five quarters of revenue, PAT, EPS, margins, and NII, provisions and NPAs for lenders |
| `…/risk`, `…/plan`, `…/tax` | Themes, flags and liquidity; the no-new-money gate and cap; unrealised gains split into short- and long-term |
| `…/history`, `…/changes` | Saved scorecards and what changed since the previous one |
| `/risk` | Historical risk, correlation, themes, overlaps, flags |
| `/plan/your_buckets`, `/plan/suggested_roles` | The Core–Satellite plan by your labels and by the scorecard's suggestion |
| `…/events`, `/review` | Thesis-check and watch events with their filing links, open thesis checks, and what changed since the previous review ([monitoring](monitoring.md)) |
| `/tax` | This year's gains, the exemption left, sales with unknown cost, harvest options |
| `rules`, `questions` | The rules below, and where each common question's evidence lives |

The pack covers operating equity: REITs aren't scored, so they aren't in it.

## Answers and the check

Every answer is JSON following `config/research_answer.schema.json`.

- **Findings:** each claim is a finding with a `kind` (reported, calculated,
  estimated or opinion) and evidence `{path, value}`.
- **Derived figures:** a figure the assistant computes is stated as
  `{derived: {op, of: [paths]}, value}` and recomputed by the checker.

The checker turns the spec's §14.1 rules into tests:

| Rule | Check |
|---|---|
| Don't invent figures | Each path exists and holds the cited value (to its rounding). Derived figures are recomputed. Every number in a claim or the summary matches its evidence (as %, lakh or crore). |
| Opinions aren't facts | An opinion finding may carry no figures |
| Old data isn't current | A cited figure marked stale must be listed in `stale_data_used` |
| Don't ignore governance | A cited holding with governance flags must have them named |
| No certainty about returns | "Will rise", "guaranteed", "target price" and similar are rejected |
| No unsupported recommendations | "You should buy/sell" and "buy now" are rejected. Ranking language gets a warning (the score isn't backtested) |
| Insufficient data | `INSUFFICIENT DATA` must list what's missing |
| One pack, one answer | The answer names the pack it cites. After a rebuild it's marked as answered from an older pack |

Passing answers are saved in `data/research/answers/`, which is never
committed.

## Limits

- **Judgement isn't checked.** The checker verifies figures and the fixed
  phrasings, not reasoning. A well-cited answer can still draw a weak
  conclusion, so read the findings.
- **Gaps in the pack.** Anything the pack lacks gets INSUFFICIENT DATA:
  consensus estimates, deal wins, management commentary, and the
  `manual_todo` items the scorecard lists. That is deliberate.
- **Changes need history.** "What changed since the last review" needs at
  least two saved scorecards or reviews (`scripts/run_review.py` saves both).
