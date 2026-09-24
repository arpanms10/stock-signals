# Portfolio advisor

Verdicts on your own book: what to buy, trim or exit, funded and taxed.

[← back to the README](../README.md)

---

```bash
PYTHONPATH=. .venv/bin/python run_advisor.py --total-capital 2000000 --cash 50000
```

A verdict per holding with its reasoning, stop, targets and quantity.

## Cash constraint

Adds are funded from the sells in the same plan, ranked by quality and then by
how far the position sits below target. What the plan cannot fund becomes
**WATCH** rather than sitting in the list implying it is actionable, and a
position that can be part-funded is -- half a holding in the best business you
own beats none of it. `--cash` adds money on hand to the budget.

Without this the advisor emitted every under-weight name and left the
arithmetic to the reader.

## Concentration

The sector cap applies to **every bucket**, not just satellite. Concentration
is a property of the book: eight financials that each look like a fine business
are still one bet on Indian credit. Trims start with the weakest names in the
sector and stop the moment it is compliant, and no single holding gives up more
than half its value for a sector reason -- beyond that a sizing fix has become
an exit in disguise.

An earlier version only trimmed satellites, and separately the legacy
rank-verdict returned before the sector check ever ran. Between them, the
largest risk the framework could identify produced no action at all.

## Tax on the whole plan

Per-holding figures apply the rate flat, which overstates tax. The **annual
LTCG exemption belongs to the year, not to any one sale**, so it is applied
across the plan once every sell is known. On a plan realising Rs 65,087 of
long-term gains, that took the estimate from Rs 8,136 to zero.


# Decision log

Every run records its verdicts -- and, separately, the top-ranked names you do
**not** own, which is what the ranking is actually asserting.

```bash
PYTHONPATH=. .venv/bin/python run_advisor.py            # logs automatically
PYTHONPATH=. .venv/bin/python run_advisor.py --no-log   # skip
```

Advice is scored once it is 21 days old -- judging a call made three days ago
measures noise, not the rule that produced it. The sign convention is that
**positive always means the advice was right**: a rise after a BUY and a fall
after an EXIT both score positive, so the two cannot cancel out in an average.

Three kinds are kept apart:

| kind | actions | why separate |
|---|---|---|
| directional | BUY, ADD, EXIT | a real right-or-wrong call |
| ranking | WATCH_RANK | the momentum ranking's own assertion |
| sizing | TRIM | no view on direction; its outcome is opportunity cost, so averaging it into a hit rate would report a rising market as bad judgement |

The summary says the sample is too small until **30 directional calls**, because
a hit rate over eleven calls is a number about luck.

---

See also: [strategy](strategy.md) for how the ranking works, [dashboard](dashboard.md) for the same verdicts in the UI.
