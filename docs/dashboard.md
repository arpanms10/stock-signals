# Dashboard

The two-section local UI, its badge system, and its target frames.

[← back to the README](../README.md)

---

```bash
.venv/bin/python run_ui.py
```

Opens at `http://localhost:8501`. The UI imports the framework directly -- there
is no API layer and no second process. Streamlit runs Python in-process, so an
HTTP boundary between the UI and the engine would add serialisation, a port and
a second copy of the logic to keep in sync, in exchange for nothing.

## Two sections

**My Holdings** -- what you own and what to do about it. Book value, P&L,
core/satellite/legacy split, sector exposure with cap warnings, then one card
per holding: the action, the trade plan, the reasoning, fundamental flags, and
for sells the realised gain, estimated tax, tax saved and tranche schedule.

**Market** -- the ranked universe. Sortable and filterable by sector, top band,
or held-only. Selecting a stock shows its entry levels: the price at which each
rule would fire, with the stop and targets that would apply *there*.

## Badges

| | meaning |
|---|---|
| **B** green | buy or add |
| **T** amber | trim -- the position is too big, not bad. You keep it. |
| **S** red | exit -- the thesis is broken. Sell it all. |
| **S!** red + ring | same, but urgent -- a hard balance-sheet red flag, not a judgement call |
| **H** grey | hold |

Trim and exit carry different letters as well as different colours. They are
different decisions, and a shade of red is not enough to carry that -- nor does
it survive colourblindness.

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

Four sidebar buttons -- refresh prices, refresh fundamentals, update
full-market data, suggest buckets. They run as subprocesses with output
streaming into the sidebar. Subprocess rather than in-process because these are
long and chatty: you get real progress, and a hung network fetch cannot take the
dashboard down with it.

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
