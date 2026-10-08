# F&O data

Where the option chains come from, and the NSE traps that fail silently.

[← F&O overview](README.md) · [← back to the README](../../README.md)

---

## Live: NSE's JSON endpoints

`data/sources/nse_derivatives.py`, through the shared session in
`data/sources/nse_client.py`.

| endpoint | gives |
|---|---|
| `/api/option-chain-contract-info?symbol=` | expiry dates and strikes |
| `/api/option-chain-v3?type=Indices\|Equity&symbol=&expiry=` | the chain for **one** expiry |
| `/api/NextApi/apiClient/GetQuoteApi?functionName=getSymbolDerivativesData&symbol=` | every futures contract, with previous close and change in OI |
| `/api/underlying-information` | the F&O universe: 6 indices, ~213 stocks |
| `nsearchives…/content/fo/fo_mktlots.csv` | lot sizes by contract month |

The underlying list and lot sizes are cached in `data/cache/` for a week.

## Live traps

1. **The old option-chain endpoints are gone, and one of them fails quietly.**
   `/api/option-chain-indices` answers 404, which is at least loud.
   `/api/option-chain-equities` answers **200 with an empty `{}`**, so a
   parser reads a valid response with no strikes and reports a stock with no
   open interest. Found 2026-10-08. Everything here uses `option-chain-v3`, and
   an empty chain raises an error instead of being treated as "no OI".
2. **Cookies come from the option-chain page itself.** The homepage now answers
   403 to a scripted client; the JSON endpoints work once
   `https://www.nseindia.com/option-chain` has been fetched. `nse_derivatives`
   does that before every call.
3. **OI is in contracts.** Checked identifier by identifier between the chain
   and the quote API; both agree. Multiply by the lot size for shares.
4. **Zero means "missing" for prices and IV.** A strike that has not traded
   reports `lastPrice: 0` and `impliedVolatility: 0`. Those become NaN.
   Averaged in, a zero IV understates ATM IV, and a zero premium halves a
   straddle. OI and volume of 0 are real zeros and stay as 0. NSE does
   publish an IV for some untraded strikes, computed from quotes, and those
   are kept.
5. **Monthly vs weekly.** NIFTY has weekly expiries; stocks and the other
   indices only have monthlies. The monthly is the last expiry in its calendar
   month, and that is the default.

## History: the F&O bhavcopy

`data/fo_bhavcopy.py` → `data/fo_history.db` (git-ignored; rebuild with
`run_fno_backtest.py --ingest`).

The live chain is a snapshot with no history, so the validation needs another
source. NSE's daily F&O bhavcopy (UDiFF format) has every contract's
end-of-day OI, change in OI, volume and close, plus the underlying's close and
the lot size.

To keep the history small, only two things are stored:

- `fo_spot`: every day, each underlying's close, near monthly expiry and lot.
  This is where outcomes come from.
- `fo_chain`: the near-monthly chain, kept only on days 20, 10 and 5 sessions
  before expiry, within ±15% of spot.

## History traps

1. **The UDiFF file starts in January 2024.** Earlier dates return 404. The
   older `foDDMMMYYYYbhav.csv` goes back years but has no underlying price,
   and the local databases have no NIFTY 50 or BANK NIFTY closes to fill it
   in. So the index sample is about 33 monthly expiries per index. See
   [validation](validation.md).
2. **OI is in shares here.** The live API reports contracts, so the bhavcopy
   figures are divided by the lot size. A stored chain then reads the same as
   a live one: NIFTY's 22,000 put wall held about 74k contracts in both on
   7–8 October 2026.
3. **Prices are not adjusted.** RELIANCE closed at 3,130 in June 2024, before
   its 1:1 bonus. A split or bonus inside an expiry cycle moves spot and
   strikes together, and looks like a crash. The validation drops a cycle if
   the lot size changed during it or spot moved more than 2× either way. It
   never tries to guess an adjustment factor.
4. **The monthly calendar comes from the futures.** Futures only exist on
   monthly expiries, so each day's futures rows give the monthly expiry
   dates directly, including expiries moved for a holiday. No date
   arithmetic is needed.
5. **An untraded option's close is not a price.** It is carried forward or
   theoretical, so it becomes NaN, the same as an untraded live LTP. When
   the ATM strike didn't trade, there is no straddle, and the range falls
   back to the walls alone. The file also has no IV or bid/ask, so the
   historical straddle uses closes where the live one uses the bid/ask mid.
6. **Sessions are counted as weekdays.** An exchange holiday can shift an
   offset day by one, or skip it for that cycle. That's harmless for this
   kind of validation, and it lets ingestion run in a single pass.

---

See also: [validation](validation.md) · the equity-side traps in
[data](../data.md).
