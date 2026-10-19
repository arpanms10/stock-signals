# Getting started

From nothing to your own dashboard, in plain steps. No coding needed: you
copy a few commands into a terminal window, and the setup asks everything
else.

[← back to the README](README.md)

---

## What this is, and what it isn't

**stock-signals** is a free tool for people who invest in Indian shares (NSE)
and mutual funds. It runs on your own computer and does three things:

- **Looks after what you already own.** For each holding it says
  *hold*, *add*, *trim* (it has grown too big) or *exit* (something is
  genuinely wrong, like rising promoter pledge), with the reason in words.
- **Shows ideas, with how well they've worked.** Every idea comes with the
  result of testing it on past data, including when the answer is "this
  didn't beat the index".
- **Keeps the data fresh on its own** once you let it schedule a few jobs.

What it **isn't**: a broker or a trading bot. It never places an order and
never sees your broker login. It's decision support, not investment advice.
You decide, and you place any order yourself.

## What you need

| | |
|---|---|
| A computer | macOS, Windows 10/11 or Linux, with about **2 GB free** |
| Python 3.11 or newer | Free. See below if you're not sure you have it |
| An internet connection in India | NSE blocks many networks abroad and most cloud servers |
| About an hour for the first download | It can run while you do other things, and can be stopped and resumed |

### Installing Python (skip if you have 3.11 or newer)

Check by typing this in a terminal (*Terminal* on Mac, *PowerShell* on
Windows):

```bash
python3 --version
```

On Windows use `py --version` instead. If it says 3.11, 3.12, 3.13 or 3.14,
you're set. If it says 3.9 or 3.10, or "not found":

- **Mac or Windows:** download the latest Python from
  [python.org/downloads](https://www.python.org/downloads/) and install it.
  On Windows, tick **"Add python.exe to PATH"** on the first screen of the
  installer.
- **Linux:** `sudo apt install python3.12 python3.12-venv` (Ubuntu/Debian), or
  your distribution's equivalent.

Macs come with an older Python 3.9 built in. Leave it alone and install a
newer one beside it; after installing, use `python3.12` (or whichever version
you installed) in the commands below.

## 1. Download the project

**Easiest:** on the project's GitHub page, press **Code → Download ZIP**, then
unzip it somewhere you'll find it again, such as your Documents folder.

**If you use git:**

```bash
git clone https://github.com/arpanms10/stock-signals.git
```

## 2. Run the setup

Open a terminal **in the project folder**:

- **Mac:** right-click the folder in Finder → *New Terminal at Folder*.
- **Windows:** open the folder, click the address bar, type `powershell` and
  press Enter.

Then run:

```bash
python3 setup_project.py
```

On Windows use `py setup_project.py`; if you installed a specific version,
`python3.12 setup_project.py` works too. The setup explains each step and
asks before doing anything:

1. **Checks your computer:** Python version, free space, and whether NSE
   answers from your connection.
2. **Installs what it needs** into a private folder (`.venv`), so nothing else
   on your computer changes. That takes 2–5 minutes.
3. **Creates empty databases**, and **three Excel files for you to fill in.**
4. **Downloads market data.** You choose how much:

   | Choice | Time | What works afterwards |
   |---|---|---|
   | **Starter** (suggested) | about 1–1.5 hours | Today, Stocks, Momentum, Market, Mutual funds, Analyze |
   | **Everything** | 2–4 hours | All of the above, plus F&O, GARP / QARP and every backtest |
   | **Pick** | you choose | Each dataset is listed with its time and the pages it feeds |
   | **None now** | — | Run `python3 setup_project.py --data starter` later |

   You can stop a download any time with **Ctrl+C**. Run the setup again and
   it continues from where it stopped.
5. **Offers to schedule jobs** that keep the data fresh (see
   [Keeping it fresh](#keeping-it-fresh)). Say no if you'd rather not; you
   can add them later.
6. **Checks everything** and tells you what to do next.

You can run the setup again whenever you like. It never overwrites a file you
already have. To see where things stand without changing anything:

```bash
python3 setup_project.py --check
```

## 3. Fill in your files

The setup creates these in the `config` folder. Open them in Excel, Numbers,
LibreOffice or Google Sheets. Each has a **How to fill** sheet saying what each
column means and where to find it.

| File | What goes in it | Required? |
|---|---|---|
| `holdings.xlsx` | The shares you own: symbol and quantity at least, ideally your average price too | For the Stocks and Today pages |
| `mf_holdings.xlsx` | Your mutual funds, as **percentages** of your fund portfolio. No amounts | For the Mutual funds page |
| `watching.xlsx` | Shares you want to keep an eye on | Optional: the app adds to it when you press *Watch* |

**Shortcut for holdings:** download your holdings from your broker (Zerodha:
Console → Portfolio → Holdings → Download; Kite: Holdings → Download), put
the file in the `config` folder, and run:

```bash
.venv/bin/python scripts/import_kite_holdings.py
```

On Windows: `.venv\Scripts\python scripts\import_kite_holdings.py`.

**Your data stays on your computer.** These files are listed in
`.gitignore`, so they're never uploaded, even if you share or publish your
copy of the project. If you use an AI assistant to help, fill the files
yourself rather than pasting your holdings into a chat.

## 4. Open the dashboard

```bash
.venv/bin/python run_ui.py
```

On Windows: `.venv\Scripts\python run_ui.py`. Your browser opens
`http://localhost:8501`. It runs only on your computer and isn't reachable
from the internet. Close the terminal window (or press Ctrl+C) to stop it.

### A first tour

| Menu | Page | Start here if you want to... |
|---|---|---|
| **Today** | | See everything that needs a decision, and how far Nifty and your core holdings might move |
| Portfolio | **Stocks** | See each holding's verdict, its reason, a trade plan and the tax angle |
| | **Mutual funds** | See how each of your funds compares with others in its category |
| Ideas | **Momentum** | See the strongest shares, the one idea here that has beaten the index |
| | Swing, Quarterly results, GARP / QARP | Read other ideas, each with its tested hit rate |
| Market | Market overview, F&O | Browse the whole market, or option-based ranges |
| Research | **Analyze a stock** | Check any NSE share: fundamentals, a sector checklist, the chart |

Every page opens with an **evidence line**: *tested*, *weak*, *no edge* or
*experimental*. Read it before you read the verdicts. Unfamiliar words are
explained in the [glossary](docs/glossary.md).

## Keeping it fresh

Data goes stale quickly, so the setup can schedule jobs that run on their
own while your computer is on:

| Job | When | What it does |
|---|---|---|
| **daily** | Weekdays 19:30 | Latest prices, the day's records, your watch list, new results |
| **weekly** | Saturday 09:30 | Full-market history, company fundamentals, F&O history, a weekly report |
| **monthly** | 5th of each month | Mutual fund NAVs |
| **quarterly** | 15 Feb, May, Aug, Nov | Filings behind GARP / QARP |
| fno-intraday (optional) | Every 30 min in market hours | Option snapshots for the F&O page |

They use your Mac's launchd, Linux's cron or Windows Task Scheduler. They
only fetch what's missing, so a run after a holiday is quick. Manage them
with:

```bash
.venv/bin/python scripts/install_schedule.py --status
.venv/bin/python scripts/install_schedule.py --install recommended
.venv/bin/python scripts/install_schedule.py --remove all
```

If you'd rather not schedule anything, use the **Data and jobs** page (the
data chip at the top right of every page): it shows how old each dataset is,
with a button to refresh it.

## Updating to a newer version

- **ZIP download:** download the new ZIP, unzip it, copy your `config` and
  `data` folders across from the old copy, and run `setup_project.py` once.
- **git:** `git pull`, then run `setup_project.py` once to pick up anything
  new.

## Getting help

- [Troubleshooting](docs/troubleshooting.md): NSE not answering, stale data,
  jobs not running, errors.
- [Glossary](docs/glossary.md): what CAGR, momentum, a straddle, PCR, core and
  satellite mean.
- **With Claude Code:** open the project folder in Claude Code and type
  `/setup`. It walks you through the setup, explains each choice and helps
  when something fails.
- The [README](README.md) and the [docs](docs/) folder go deeper into how
  each part works and what the backtests found.
