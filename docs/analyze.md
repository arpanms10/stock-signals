# Analyze: the Fundamental and Technical Analysts

How to look at any one NSE stock: what the two analysts read, how they reach a
verdict, and what they can't tell you.

[← back to the README](../README.md)

---

The dashboard's **Analyze a stock** page (Research menu) looks at one stock at a time. That can be any
NSE-listed company, not only those in the NIFTY 500, your holdings or the
watchlist.

1. Type a **ticker** (`INFY`, `NSE:INFY` and `INFY.NS` also work), a **company
   name** (`Infosys`, `state bank`) or an **ISIN** (`INE009A01021`).
2. Pick **Both**, **Fundamental** or **Technical**, then press **Analyze**.
3. If the name matches more than one company (`tata`, `bajaj fin`), you're
   asked which one. The tab never picks one for you, because analysing the
   wrong company would still look convincing.

A renamed company still resolves by its old ticker, because the ISIN doesn't
change. For example, `ZOMATO` opens `ETERNAL`.

**Both analysts describe the stock; neither predicts its price.** What the
analysts are good for is the checklist: is the
business sound, is anything about to go wrong, are you buying into an uptrend
or a falling knife, and where would the stop go.

## Fundamental Analyst

`analysis/fundamental.py`, using the same data sources and Quality rubric as
the rest of the framework ([strategy](strategy.md)).

| | |
|---|---|
| **Data** | NSE filings first: quarterly results (six quarters), promoter holding and pledge. Yahoo fills in ROE, ROCE, debt/equity, cash flow and valuation. |
| **Score** | The Quality Score, 0–100, from growth, profitability, balance sheet, cash quality and governance. Banks are scored on asset quality, profitability, efficiency, provisioning and capital. NBFCs are scored without debt/equity or cash flow. |
| **Stance** | Strong (≥ 70), Sound (≥ 55), Mixed (≥ 40), Weak. **Any red flag lowers it one step.** |
| **Red flags** | Promoter pledge ≥ 25% or rising; interest cover < 2; debt/equity > 2; cash flow under half of profit; gross NPA > 6%; net NPA > 2%. Leverage and cash flow aren't checked for lenders, because borrowing is what a lender's business is built on. |
| **Strengths / concerns** | Score components at 70 or above, and at 35 or below, each with the figures behind it. |
| **Quarterly table** | Revenue, profit and net margin for each quarter, with year-on-year growth when the same quarter a year earlier is on file. |
| **Valuation** | P/E, P/B, dividend yield and market cap. The P/E is read in absolute bands (< 15 inexpensive, < 30 moderate, < 50 rich) and against profit growth, as a PEG. No free source has sector P/E figures, so it isn't compared with peers. |

Fetched data is kept for a week in `data/cache/analysis/`, because results only
change once a quarter. Tick **Refetch fundamentals** to fetch it again sooner.
The first fetch for a bank can take a minute or more, because NSE's bank
filings are slow. Lookups here **don't change `config/fundamentals.csv`**:
that sheet belongs to your watchlist and holdings, and looking up a stock
shouldn't change what the advisor scores.

Stocks with little or no data:

- **SME and recently listed stocks** often have no figures in either source.
  The report says *Not enough data* and doesn't guess.
- **Companies Yahoo reports in USD**, such as Infosys, lose the absolute
  figures that only Yahoo provides (annual profit, cash flow). Cash quality
  is then not scored, and that doesn't count against the stock.

## Technical Analyst

`analysis/technical.py`, using the same indicators and timing score as the
Market tab ([strategy](strategy.md)) and the swing rules from the Swing tab
([swing](swing.md)).

| | |
|---|---|
| **Data** | Daily prices from `data/signals.db`. A stock that isn't there yet, or is behind the NIFTY 500, is fetched from NSE: three years of history the first time (a few seconds), then only the missing days. |
| **Stance** | **Uptrend**: close above the 200 DMA, 50 DMA above the 200 DMA, and the 200 DMA rising. **Downtrend**: all three reversed. **Sideways**: anything in between. |
| **Qualifier** | In an uptrend: *stretched* (RSI ≥ 70 or more than 15% above the 50 DMA), *losing momentum* (MACD below its signal line and price below the 50 DMA) or *strong* (ADX ≥ 25). In a downtrend: *oversold* or *trying to turn*. |
| **Timing score** | The framework's 0–100 blend of trend, momentum, relative strength, volume and volatility, with each part shown separately. |
| **Readings** | Each indicator in a sentence, marked 🟢 / 🔴 / ⚪: moving averages, golden or death cross, ADX and ±DI, RSI zone, MACD, ATR, Bollinger squeeze, volume, up/down volume, **delivery %** (an NSE-specific measure of conviction), and distance from the 52-week high. |
| **Support / resistance** | Up to three levels each side, nearest first. They come from swing pivots over the past year (a high or low that is the extreme of five bars either side), grouped together when they sit within about 0.6× the daily ATR of each other. The 50 and 200 DMA and the 52-week high and low are added. "×3" means price turned there three times. |
| **Returns** | 1, 3, 6 and 12 months, compared with the NIFTY 500. |
| **Swing setup** | If today is a pullback or breakout setup, shows the Swing tab's entry, stop and targets. When the Swing tab wouldn't suggest it, the report says why (most often that the stock isn't in an uptrend). |

The chart shows the past year's close with the 50 and 200 DMA, and dashed
lines for support (green) and resistance (red).

## Code

| | |
|---|---|
| `analysis/lookup.py` | Ticker, name or ISIN → symbol. The lookup combines NSE's equity master, the NIFTY 500 list (for sectors) and `market.db`'s ISIN map, so it still works offline. |
| `analysis/fundamental.py` | `fetch()` (cached) and `analyse()` (no network calls, tested on fixed data) |
| `analysis/technical.py` | `load_frame()` (fetches when needed) and `analyse()` (no network calls, tested on synthetic prices) |
| `ui/analyze_tab.py` | The tab |
| `fetch_fundamentals.fetch_rows()` | The per-stock fetch, used by both the sheet refresh and this analyst |
