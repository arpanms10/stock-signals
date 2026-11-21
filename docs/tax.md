# Tax & transition

**Portfolio › Tax & transition** turns your broker tradebooks into tax lots,
totals this financial year's gains, and times each trim of the Core–Satellite
plan ([Risk & plan](risk.md)) to the cheapest date. Estimates, not tax advice;
nothing is ordered and your holdings file is not changed.

Settings: [config/tax.yaml](../config/tax.yaml). Code: `strategy/capgains.py`,
`strategy/lots.py`, `strategy/transition.py` (pure), `book/tradebook.py`,
`book/tax_view.py`.

## Rates

Dated in tax.yaml, so a sale is taxed under the regime in force on its date.

| From | Short-term | Long-term | Exemption a year |
|---|---|---|---|
| 23 Jul 2024 | 20% | 12.5% | ₹1,25,000 |
| 1 Apr 2018 | 15% | 10% | ₹1,00,000 |

Plus 4% cess and any surcharge you set (it depends on your income). Checked
on 21 Nov 2026: Finance Act 2026 left these unchanged; Budget 2026 raised STT
on futures and options only, not on delivery (0.1% on each side). The
Income-tax Act 2025 replaces the 1961 Act from 1 April 2026; the old section
names (111A, 112A) are used here for familiarity. Re-check after each Budget.

## Tradebooks and lots

Download Console › Reports › Tradebook (Equity) for any date ranges and add
them on the page (or drop them in `config/tradebooks/`). Overlapping files are
merged by trade ID. The folder is never committed.

For each holding the trades are replayed in date order:

- **Buys** become lots with a date and a cost.
- **Sales** consume the oldest shares first (FIFO). This is mandatory for demat
  holdings, so which shares a sale delivers is never a choice.
- **Splits** (market.db) multiply the shares and divide the cost, and keep the
  date.
- **Bonus shares** are a new lot at nil cost, dated the ex-date. Their
  holding period starts at allotment.
- **Renamed stocks** (ZOMATO to ETERNAL) and ISIN matches count under today's
  symbol.

**Opening lot.** Shares you held before the first tradebook are one opening
lot.

- **Size:** whatever makes the replay end at today's quantity.
- **Cost:** whatever makes the total cost match your average cost.
- **Term:** long-term, unless the broker counts more short-term shares than
  the dated lots explain. That happens with an IPO allotment, a transfer in or
  a demerger. Those shares are then short-term, with the date unknown.

**Without trades.** A holding with no trades in the tradebooks uses the
broker's long/short split instead. The page shows the source of every
holding's lots and any mismatch with the broker.

## This financial year

The page shows the net short- and long-term gains realised this year, the tax
on them, and the long-term exemption left.

Set-off follows the rules:

- A short-term loss offsets short-term gains first, then long-term gains.
- A long-term loss offsets only long-term gains.
- The exemption applies to the long-term gain that is left.

**Incomplete year.** A sale whose purchase isn't in your tradebooks (an IPO
allotment, or shares bought before them) has no known cost. It is listed, not
guessed. Enter the year's totals from Console › Reports › Tax P&L under
`realised_override` in tax.yaml.

## The transition

For each horizon (3, 6 and 12 months) the Risk & plan engine runs over that
horizon. More months of new money mean fewer trims. Each trim may be made on
one of these dates:

- **Now.**
- **When it turns long-term:** the day the shares FIFO would deliver cross 12
  months, when their gain drops from 20% to 12.5%.
- **From 1 April:** a new year's exemption, and a new year to set losses
  against.

Every combination of dates is tried and the cheapest kept, counting the gains
you've already realised this year. A trim waits only if waiting saves at least
₹500 (`min_saving_to_wait`), because a trim corrects drift and drift has a
cost too. Prices are held at today's.

**Costs of selling:** STT, the exchange charge, SEBI fees, the DP charge per
stock per day, and GST. Defaults are Zerodha's delivery charges.

**Sensitivity.** The monthly amount is optional: none is assumed. If you
enter one, a sensitivity table shows half and double that amount.

## Options this year (not part of the plan)

- **Booking gains tax-free:** selling and buying back long-term shares, up
  to the unused exemption. You pay no tax now and the cost basis is higher for
  the future. It costs a round trip, and the shares bought back start a new
  12 months.
- **Unrealised losses:** what's available to set off against gains.

Selling and buying back the same day can be challenged as a sham transaction.
Many investors leave a gap.

## Not done

- **Grandfathering** of shares bought before 1 February 2018 (cost = the
  higher of cost and the 31 January 2018 price). Opening lots use your average
  cost.
- **Other brokers.** Only Zerodha's tradebook format has been tested. The
  reader matches common column names, so other brokers may work.
- **Slab-rate income and the 87A rebate.** These don't apply to these
  special-rate gains.
