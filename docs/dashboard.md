# Dashboard

The local UI: its pages, its badge system, and its target frames.

[← back to the README](../README.md)

---

```bash
.venv/bin/python run_ui.py
```

Opens at `http://localhost:8501`. The UI imports the framework directly -- there
is no API layer and no second process. Streamlit runs Python in-process, so an
HTTP boundary between the UI and the engine would add serialisation, a port and
a second copy of the logic to keep in sync, in exchange for nothing.

## Pages

A bar across the top holds four menus. Each page loads only its own data, so
Mutual funds or Analyze opens in seconds instead of waiting for the whole
market to be ranked.

| Menu | Page | What it shows |
|---|---|---|
| | **Today** (home) | Everything that wants a decision, from every page: exits, trims and stops reached under *Act now*; adds, slipping funds and targets reached under *Review*; results due this week; the market regime. Each row opens the page it came from |
| Portfolio | **Stocks** | *Holdings*: book value, P&L, core / satellite / legacy, sector exposure, then one card per holding with its action, trade plan, reasons, flags and tax. *Watching*: the watch list measured from the day each name was flagged. **Suggest buckets** is here |
| | **Mutual funds** | Your MF book against each fund's category ([mutual fund evaluation](mf_evaluation.md)) |
| Ideas | **Momentum** | Top band on both lookbacks, with universe size and Recompute under *Ranking settings* |
| | **Swing**, **Quarterly results**, **GARP / QARP** | As before; see [swing](swing.md), [results](results.md), [picker](picker.md) |
| Market | **Market overview** | The ranked universe, filterable, and the entry levels for any stock in it |
| | **F&O (experimental)** | Expected range to expiry; see [F&O](f_o/README.md) |
| Research | **Analyze a stock** | Any NSE listing by ticker, name or ISIN ([analyze](analyze.md)) |
| | **Compare funds** | Any funds side by side, each against its own category |

Under the bar, on every page: a **search box** (a stock opens Analyze, a fund
opens Compare funds) and the **data chip**, which turns amber or red when a
source is behind and opens to say which, with the button that fixes it.
Anything stale also gets a warning line on every page.

Every page title is followed by an **evidence line**: how far its signals held
up in a backtest (tested / weak / no edge / experimental), so the strength of
a verdict is visible before reading it.

## Badges

| | meaning |
|---|---|
| **Add** / **Buy** green | buy or add |
| **Trim** amber | trim -- the position is too big, not bad. You keep it. |
| **Exit** red | exit -- the thesis is broken. Sell it all. |
| **Exit !** red + ring | same, but urgent -- a hard balance-sheet red flag, not a judgement call |
| **Watch** blue | worth a look, not an action yet |
| **Hold** grey | hold |

Stocks and mutual funds use the same words and colours. Trim and exit are
different words as well as different colours: they are different decisions,
and a shade of red is not enough to carry that -- nor does it survive
colourblindness.

## Two target frames, deliberately separate

A holding bought cheaply that has since run up has entry targets *below* today's
price. Shown on a row recommending a buy, that reads as "sell lower than you
buy" -- which is how the bug was found.

- **Buying today** uses today's price: `₹1,423 to buy · stop ₹1,379 · T1 ₹1,489 · T2 ₹1,555`
- **Holding a position** uses your entry, marking targets already achieved:
  `₹1,423 now · stop ₹1,408 · T1 ₹1,283 ✓ passed · T2 ₹1,349 ✓ passed`
- **Exits** show no targets at all -- you are leaving.

A target below the current price is never rendered with a negative percentage.

## Running scripts from the UI

Every refresh and backtest is on the **Data and jobs** page, opened from the
data chip: grouped by how often each needs running, with when its data was
last updated and a button to run it. **Refresh everything stale** runs the
stale ones in order. A page's own data also has a button on that page
(Refresh prices on Stocks, Refresh NAVs on Mutual funds, Check for new results,
Refresh filings).

Jobs run as subprocesses with output streaming into the page, then the page
reloads. Subprocess rather than in-process because these are long and chatty:
you get real progress, and a hung network fetch cannot take the dashboard down
with it. Keep the tab open while one runs.

## Configuration and privacy

`.streamlit/config.toml` sets:

- `toolbarMode = "minimal"` -- hides Streamlit's **Deploy** button. That button
  offers to publish to Streamlit Community Cloud, where apps are PUBLIC by
  default, and this one renders holdings, average costs and P&L. It does not
  belong on a personal finance dashboard.
- `address = "localhost"` -- loopback only, not reachable from the network.
- `gatherUsageStats = false`.

## One gotcha

**Streamlit does not reliably hot-reload imported modules** -- only the main
script. If you edit `ui/components.py` or `ui/service.py` and the change does
not appear, restart the app rather than pressing Recompute. Edits to
`ui/app.py` itself reload normally.

The full pipeline (200 symbols, indicators, ranking, scoring) takes ~30s and is
cached. **Recompute** clears the cache; the data-refresh buttons clear it too.

---

See also: [advisor](advisor.md) for the logic behind each verdict.
