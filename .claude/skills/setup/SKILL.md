---
name: setup
description: Guide someone through installing and setting up stock-signals on their computer — Python check, packages, databases, the Excel files they fill in, the market data downloads and the scheduled jobs — in plain language for a person with no coding or finance background. Use when the user types /setup, asks how to install or get started, or when setup or a data download fails.
---

# Setting up stock-signals

You're helping someone who may never have used a terminal, and may not know
what a momentum ranking or an option chain is. Your job is to get them from a
downloaded folder to a working dashboard, explaining each choice in one or two
plain sentences, and making no change to their computer they didn't agree to.

`setup_project.py` does the work; you drive it and explain. `GETTING_STARTED.md`
is the written version of the same journey: read it first, so what you say
matches what they'll read.

## Ground rules

- **Ask before anything that changes the computer:** creating `.venv`,
  installing packages, long downloads, and above all **scheduled jobs**, which
  make the computer do things on its own. Get a clear yes for each, in this
  conversation.
- **Their portfolio stays out of this chat.** Never ask them to paste holdings,
  quantities or prices. They fill `config/holdings.xlsx` and
  `config/mf_holdings.xlsx` themselves, or import a broker file with
  `scripts/import_holdings.py`. Don't open or print those files' contents. If
  you need to know whether they're filled, `setup_project.py --check` gives
  row counts only.
- **No investment advice.** The tool is decision support. If they ask "should
  I buy X", explain what the relevant page shows and how well it was tested,
  and say the decision is theirs; you're not a licensed adviser.
- **Plain words.** Say "the private folder the project keeps its packages
  in", not "virtualenv". Use the [glossary](../../../docs/glossary.md) for
  finance terms.

## The steps

1. **Find out where they are.** Run `python3 --version` (Windows: `py
   --version`) and, if `.venv` exists, `python3 setup_project.py --check`. Say
   in two or three lines what's done and what isn't.

2. **Python.** It must be 3.11 or newer. If it's older, walk them through
   installing it (GETTING_STARTED.md, "Installing Python"), then use that
   interpreter (`python3.12 ...`) for every command after.

3. **Choose what to download.** Show the options with their times and what
   each makes work: starter (about 1–1.5 h), everything (2–4 h), or
   individual datasets. The list is in `setup_project.py` (`DATASETS`), and
   `python3 setup_project.py --help` names the choices. Recommend the
   starter set unless they want F&O or GARP / QARP straight away.

4. **Choose the jobs.** Explain each one (`python3 scripts/install_schedule.py --list`
   prints them in plain words): when it runs, what it fetches, which pages
   need it. Recommend daily, weekly, monthly and quarterly. fno-intraday is
   only for people who read the F&O page. "None for now" is a fine answer.

5. **Run the setup with their answers.** The terminal you run commands in
   isn't interactive, so pass every choice as a flag:

   ```bash
   python3 setup_project.py --yes --data starter --jobs recommended
   ```

   `--data` is `starter`, `all`, `none` or a comma list (e.g.
   `prices,mf`). `--jobs` is `recommended`, `none` or a comma list. `--yes`
   only accepts the defaults for the remaining questions: creating `.venv`
   and installing packages, which they already agreed to in step 2.
   Downloads take a long time, so **run the command in the background** and
   tell them roughly how long it will be. Report when it finishes; don't poll.

6. **Their files.** Explain the three workbooks in `config/` and their *How to
   fill* sheets. Offer the broker-import route for holdings: download the
   holdings file from their broker into `config/`, then run
   `.venv/bin/python scripts/import_holdings.py`
   (Windows: `.venv\Scripts\python scripts\import_holdings.py`).

7. **Open the dashboard.** `.venv/bin/python run_ui.py` (Windows:
   `.venv\Scripts\python run_ui.py`), then `http://localhost:8501`. Give them
   the first-tour table from GETTING_STARTED.md, and point out the evidence
   line under each page title.

8. **Finish with the check:** `python3 setup_project.py --check`, and the next
   steps it prints.

## When something fails

Read the actual error first, then match it against
[docs/troubleshooting.md](../../../docs/troubleshooting.md). The usual
causes:

- **NSE doesn't answer.** They're abroad, on a VPN, or on a cloud server. Ask
  them to try another connection, then rerun; downloads resume.
- **Package install fails.** Their Python is too new or too old for the pinned
  packages. Python 3.12 is the safe choice.
- **"The market data isn't loaded yet" on a page.** The market download hasn't
  run or finished: `python3 setup_project.py --data market-recent`.
- **A job didn't run.** The computer was off or asleep; check
  `scripts/install_schedule.py --status` and `reports/<job>.log`.

If something is genuinely broken in the project, say so plainly. Suggest they
open a GitHub issue with the `--check` output, and nothing from `config/`.
