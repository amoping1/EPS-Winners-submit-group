# Final run — ready

Verified at 16:58 London. Two full runs produced **identical figures on all
twelve metrics**, so the architecture HTML locked at 17:15 stays accurate
whatever time the accepted run happens.

## The command

One line. Give it its own terminal and put nothing after it — piping the run
into anything that truncates will kill it mid-way and leave an empty run
directory.

```bash
python run.py --as-of 2026-08-16 --quiet && python scripts/collect_team_forecasts.py && python scripts/build_ensemble.py --write && npm run check:submission
```

Takes about 85 seconds against a 45-minute window. Expect to see five `PASS`
lines at the end: one for `entry.json` and one per workbook.

## The twelve figures it will produce

| Company | Cell | Metric | Value | Units |
|---|---|---|---|---|
| HD | C7 | Net sales | 47,324.52 | USDm |
| HD | C8 | Adjusted diluted EPS | 4.66 | USD / share |
| HD | C9 | Comparable sales, total company | 1.10 | % |
| ADI | C7 | Revenue | 3,921.10 | USDm |
| ADI | C8 | Adjusted diluted EPS | 3.3457 | USD / share |
| ADI | C9 | Adjusted gross margin | 73.55 | % |
| HAS | C7 | Net fees | 941.75 | GBPm |
| HAS | C8 | Pre-exceptional basic EPS | 1.00 | GBp |
| HAS | C9 | Pre-exceptional operating profit | 43.9855 | GBPm |
| DE | C7 | Worldwide net sales and revenues | 10,911.73 | USDm |
| DE | C8 | Diluted EPS (GAAP) | 4.7178 | USD / share |
| DE | C9 | Production & Precision Ag operating profit | 378.34 | USDm |

Check these against the upload screens before submitting. Percentages are in
percentage points and Hays EPS is in pence.

## The five things to submit

| # | What | Where it is | Status |
|---|---|---|---|
| 1 | `entry.json` | repo root, gitignored | ready, `check:entry` passes |
| 2 | Repository + final commit | github.com/amoping1/EPS-Winners-submit-group | `master` and `neva` branches |
| 3 | `architecture/index.html` | 29 KB, no scripts, under 2 MB | ready |
| 4 | Clear-run log | `logs/run-<id>.jsonl` | written by the run |
| 5 | Four workbooks | `submission/*.xlsx` | ready |

Items 1 and 3 go to the private form on openstocks.com/hackathon from 17:30.
Item 5 uploads one file per company to its Forecast Model. Uploads are manual —
the agent must not submit programmatically.

## After the run, before uploading

1. `npm run check:submission` — five PASS lines.
2. `git rev-parse HEAD` — if it differs from `submission.finalCommit` in
   `entry.json`, update that field and re-run `npm run check:entry`.
3. Confirm the four filenames are exactly:
   `HD-FY2026Q2.xlsx`, `ADI-FY2026Q3.xlsx`, `HAS-FY2026.xlsx`, `DE-FY2026Q3.xlsx`.

## If something breaks

The workbooks already on disk are valid and were produced by the declared
system. A failed re-run costs nothing: the existing files stay in place, and the
window allows retries. Do not delete `submission/` before a re-run.

If the run does fail, `python run.py --as-of 2026-08-16 --quiet --no-agent`
skips the model entirely and produces figures from the deterministic path alone.

## Deadlines

- **17:15** architecture HTML locks, 45-minute run window opens
- **17:30** OpenStocks uploads and the private form open
- **18:00** hard deadline, all four uploaded and the entry recorded
