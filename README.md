# EPS-Winners

Team repository for **Agents vs Wall Street** — the forecasting-agent hackathon held in
London on Sunday 16 August 2026, hosted by OpenStocks with Primer, AI Tinkerers and OpenAI.

## The challenge

Build an agent that researches four public companies and forecasts twelve reported
figures — three per company — then submits them as OpenStocks workbooks before the 18:00
deadline.

| Company | Ticker | Period | Metrics |
|---|---|---|---|
| Home Depot | HD | FY2026 Q2 | Net sales · Adjusted diluted EPS · Comparable sales |
| Analog Devices | ADI | FY2026 Q3 | Revenue · Adjusted diluted EPS · Adjusted gross margin |
| Hays plc | LSE:HAS | FY2026 | Net fees · Pre-exceptional basic EPS · Pre-exceptional operating profit |
| Deere & Company | DE | FY2026 Q3 | Worldwide net sales & revenues · Diluted EPS (GAAP) · Production & Precision Ag operating profit |

Two prizes are judged separately: **Architecture & Design**, decided on the day against a
published 100-point rubric, and **Forecast Accuracy**, settled after the companies report.
Accuracy is scored as the team's absolute error divided by Wall Street's absolute error on
the same metric, capped at 5.0 and averaged across all twelve.

## Rules that shaped the work

- Everything competition-specific had to be built after the 11:15 start. Off-the-shelf
  models and public libraries are allowed and must be declared in `entry.json`.
- The supplied frozen corpus of filings, transcripts and slides may be used, along with
  public information found during the event.
- Uploads to OpenStocks are manual — the agent must never submit programmatically.
- No secrets in the repository, the architecture page, or the run logs.
- Repository history and the final commit are mandatory parts of the entry.

## Repositories

| Repository | Contents |
|---|---|
| `EPS-Winners-submit` (branch `Adrian`) | Full agent: tools, agents, rails, backtest harness, generated architecture page |
| `EPS-Winners-submit-group` | This repository — team landing point |

## Status

Initial commit. See the individual member branches and repositories above for the working
code and the architecture write-up.

---

## This branch: Asof

A forecasting system built around a single point-in-time cutoff.

`ash
npm install && python -m pip install openpyxl
python run.py --as-of 2026-08-16     # writes submission/*.xlsx
npm run check:submission             # the organisers validator
`

--as-of is one global guard that every retrieval must ask for, and which raises
when no cutoff is configured. The same mechanism does three jobs: it makes the
backtest honest, it makes the competition run reproduce exactly after the event,
and dropping it turns the system into a live forecaster for future earnings events.

| Command | What it does |
|---|---|
| python run.py --as-of 2025-11-18 | Replays a quarter whose result we know, to score the method |
| python run.py --as-of 2026-08-16 | The competition run |
| python run.py | Live mode |

The backtest runs before forecasting and decides where model budget is spent:
metrics it scores under 15% never reach a model at all. Six of twelve qualified,
each failing deterministically for a structural reason rather than a tuning one.

- dashboard/index.html - presentation dashboard, opens with a double click
- rchitecture/index.html - the judged write-up, self-contained, no scripts
- logs/ - timestamped clear-run logs
- 
uns/ - per-run artifacts, including the backtest report

`ash
python -m unittest discover -s tests -t .   # 132 tests
python scripts/build_dashboard.py
python scripts/build_architecture.py
`
