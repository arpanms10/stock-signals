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

To keep the history small, full chains are only stored on certain days:

- `fo_spot`: every day, for every underlying: close, estimated high/low,
  near monthly expiry, lot, straddle, straddle IV, skew-wing IVs and the next
  monthly's IV. Outcomes, IV percentile and realised vol come from here.
- `fo_chain`: the near-monthly chain on days 20, 10 and 5 sessions before
  expiry, plus each weekly index expiry's chain 4, 2 and 1 sessions before
  it, within ±15% of spot.
- `fo_index_ohlc`: daily index high/low/close since 2019.
- `fo_results`: announced results dates.

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
6. **Sessions count exchange holidays** (see 7 below), so an offset day is
   the same number of trading sessions before expiry in every cycle.

## Added in the second round

7. **Sessions count NSE's holidays now.** `/api/holiday-master?type=trading`
   gives the F&O holidays per year, cached. Counting weekdays alone said
   "13 sessions" to 27-Oct-2026, when Diwali (20-Oct) made it 12. The
   historical ingest uses the same calendar, so offset days line up.
8. **The bhavcopy has no IV, so IV is backed out of closing prices.** A
   vectorised Black-Scholes bisection at the two skew wings, and the
   straddle formula for ATM. To keep live and history comparable, the live
   view computes the same straddle IV and skew from quotes, instead of
   using NSE's IV column. Live NIFTY straddle IV (13.0%) sits close to NSE's
   ATM IV (13.3%).
9. **There is no underlying high/low in the F&O file.** For stocks it's
   estimated as the near-month future's high/low minus that day's closing
   basis. Basis changes little within a day, but this is an estimate. For
   indices the real high/low comes from the index's own history
   (niftyindices, via jugaad-data).
10. **Results dates come from free text.** NSE's board-meeting endpoint
    gives a purpose and a description. Most results meetings are filed as a
    generic "Board Meeting Intimation" whose description mentions financial
    results, so the match is on either field. It finds about 25,000 meetings
    for 2024–26, peaking in the four results seasons. Only *announced*
    meetings are known; a company that hasn't announced yet looks like it
    has no results before expiry.
11. **Pre-2024 history is indices only, with OI in shares.** The old format
    has no underlying price or lot size. Spot and high/low come from the
    index history, and every OI read is a ratio or argmax within one chain,
    so shares versus contracts doesn't change the result. Absolute OI isn't
    comparable across the 2024 boundary.

12. **A failed download is not "no data".** The F&O ingest had the same
    flaw as the equity one (see [data](../data.md)). A failed request now
    comes back as `None` and is retried on the next run. A 404 is recorded
    as an empty day only once it is a week old. The F&O history had one
    such hole: 2021-03-30.

---

See also: [validation](validation.md) · the equity-side traps in
[data](../data.md).
