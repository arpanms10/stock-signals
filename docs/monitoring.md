# Monitoring & reviews

**Portfolio › Reviews** lists events that might change why you hold a
company, and what moved between two reviews. A review also writes a report
to `data/reviews/review-<date>.md`.

```text
PYTHONPATH=. .venv/bin/python scripts/refresh_holding_data.py   # shareholding and results first
PYTHONPATH=. .venv/bin/python scripts/run_review.py             # fetch, detect, snapshot, diff, report
PYTHONPATH=. .venv/bin/python scripts/run_review.py --offline   # no NSE fetch
PYTHONPATH=. .venv/bin/python scripts/run_review.py --events    # open events only
```

An event is a prompt to read the filing and re-check the thesis. It is never
a buy or sell signal, and nothing here ranks holdings.

## Events

| Source | What becomes an event | Severity |
|---|---|---|
| NSE corporate announcements | Statutory auditor resigned or removed; qualified or adverse opinion; fraud, default, insolvency, show-cause or a SEBI order; credit rating downgrade or negative watch; promoter pledge or encumbrance; MD, CEO, CFO or chairman leaving | Thesis check |
| | Exchange asking for clarification on news; auditor appointed or rotated; fund raise, preferential issue or buyback; acquisition, merger, demerger or sale | Watch |
| | Everything else (newspaper notices, analyst meets, trading window, results publications) | Info, hidden by default |
| Shareholding filings | Promoter stake down 1 pt or more in a quarter; pledge at 10% of promoter holding or more; pledge up 2 pts or more | Thesis check |
| | Promoter stake down 3 pts or more over the filings on file (~2 years) | Watch |
| Results filings | Profit down 20% or more on the same quarter last year; revenue down 10% or more; a loss after a profit; for lenders, gross NPA up 0.5 pts or more | Thesis check |
| | Any other new quarter, with the year-on-year changes | Watch |

The thresholds and patterns are in `config/monitoring.yaml`. The first
matching rule wins, so a specific rule (statutory auditor resigned) sits above
a general one (auditor appointed).

Every event links to its source: the announcement's PDF, the results XBRL
filing, or the company's NSE page for shareholding. Results compare with the
same quarter a year earlier, so seasonal businesses don't trigger on every
quarter.

### Reviewing an event

On the page, open **Mark an event reviewed**, write what you read and
decided, and mark it. The note is kept in `research.db` and appears in later
reports. A reviewed event stays reviewed when the review runs again. Every
status change also goes into the audit log.

## What changed between reviews

Each review saves a snapshot for every company holding:

- score, quality tier, suggested role and your bucket;
- governance flags;
- weight in the book;
- promoter and pledge percentages;
- valuation margin of safety and band position;
- the plan's buy or trim, under your buckets and under the suggested roles, with no new money assumed.

The page compares any two snapshots. The report compares with the previous
one. A change is listed when it crosses a threshold in `review:`:

| Field | Listed when |
|---|---|
| Score | Moves 5 pts or more |
| Weight | Moves 1 pt or more |
| Plan action | Changes, or moves ₹5,000 or more |
| Tier, role, bucket, flags | Any change |
| Promoter or pledge % | Moves 0.5 pts or more |

A review also saves the day's scorecards, so `/holdings/<S>/changes` in the
research pack fills in.

## The report

`data/reviews/review-<date>.md`, never committed, has:

- open thesis checks with links;
- what changed since the previous review;
- watch items;
- what you reviewed, with your notes;
- any data problems, such as a holding whose announcements couldn't be fetched.

## The research assistant

The research pack carries each holding's thesis checks and watch events
(`/holdings/<S>/events`), the open thesis checks (`/review/open_thesis_checks`)
and the last review's diff (`/review/changes`). So `/research Which holdings
need a thesis check?` and `/research Which recommendations have changed since
the previous review?` answer from them.

## Storage

All in `data/research.db`:

| Table | Holds |
|---|---|
| `announcement` | Announcements, kept |
| `announcement_fetch` | How far each holding has been fetched; the first run reads 120 days back, later runs re-read 7 days before the last fetch |
| `monitor_event` | Events with status and your note |
| `review_snapshot` | One snapshot per day |

## Limits

- **Keyword rules.** Announcements are classified by keyword rules on NSE's
  category and one-line summary, not by reading the PDF. A filing with a
  vague summary can land in Info. Unusual wording can miss a rule, so open
  Info when a holding is under watch.
- **Freshness.** Shareholding and results are only as fresh as the last
  `refresh_holding_data.py`.
- **Quarterly change needs a refresh.** The quarter-on-quarter promoter
  change is computed at refresh, so it appears after the next refresh.
- **Coverage.** REITs, InvITs and ETFs are not monitored. Their filings
  don't fit these rules.
- **Not scheduled.** Nothing runs reviews automatically. Run one after each
  results season, or add it to the weekly job.
