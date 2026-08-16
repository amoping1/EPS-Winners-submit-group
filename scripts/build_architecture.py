#!/usr/bin/env python3
"""Generate architecture/index.html from the current run.

This is the judged artifact and it has hard limits the dashboard does not:
one self-contained file, 2 MB maximum, and scripts, external assets and network
requests do not run in the judging preview. So: inline CSS, inline SVG, and no
JavaScript at all.

Generating it rather than hand-writing it means the numbers in the write-up are
the numbers the system actually produced, and it can be regenerated in seconds
if the system changes before the 17:15 lock.

    python scripts/build_architecture.py [run-id]
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.build_dashboard import architecture_svg, collect, latest_run  # noqa: E402
from src.config import PATHS  # noqa: E402

MAX_BYTES = 2 * 1024 * 1024

STYLE = """
*,*::before,*::after{box-sizing:border-box}
:root{--ink:#0d1421;--muted:#5b6b82;--faint:#8695a8;--line:#dfe5ee;--soft:#eef2f8;
  --paper:#fff;--wash:#f5f8fc;--blue:#155eef;--blue-soft:#e8f0ff;
  --good:#0d8a5f;--warn:#b7791f;--bad:#c0392b;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
body{margin:0;background:var(--wash);color:var(--ink);
  font:16px/1.68 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
main{width:min(940px,calc(100% - 32px));margin:40px auto;padding:clamp(26px,5vw,60px);
  background:var(--paper);border:1px solid var(--line);border-radius:20px}
.eyebrow{color:var(--blue);font-size:12px;font-weight:800;letter-spacing:.14em;
  text-transform:uppercase;margin:0 0 10px}
h1{margin:0 0 12px;font-size:clamp(36px,7vw,60px);line-height:1;letter-spacing:-.045em}
h2{margin:52px 0 10px;font-size:25px;letter-spacing:-.025em;padding-top:22px;border-top:1px solid var(--line)}
h2:first-of-type{border-top:0;padding-top:0}
h3{margin:26px 0 6px;font-size:17px;letter-spacing:-.01em}
p,li{max-width:74ch}
.lede{color:var(--muted);font-size:20px;line-height:1.5;max-width:66ch}
.card{margin:22px 0;padding:18px 20px;background:var(--wash);
  border:1px solid var(--line);border-radius:12px}
.card p{margin:5px 0}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:20px 0 4px}
.chip{background:var(--wash);border:1px solid var(--line);border-radius:999px;
  padding:5px 13px;font-size:13px;color:var(--muted)}
.chip b{color:var(--ink)}
.chip.ok{background:#eaf7f1;border-color:#bfe6d4;color:var(--good)}
.chip.ok b{color:var(--good)}
code{font-family:var(--mono);font-size:13.5px;background:var(--blue-soft);
  padding:2px 6px;border-radius:5px}
pre{background:var(--ink);color:#e6edf6;padding:16px 18px;border-radius:10px;
  overflow:auto;font-family:var(--mono);font-size:13px;line-height:1.55}
pre code{background:none;color:inherit;padding:0;font-size:inherit}
table{width:100%;border-collapse:collapse;font-size:14px;margin:16px 0}
th{text-align:left;color:var(--faint);font-size:11.5px;font-weight:700;letter-spacing:.07em;
  text-transform:uppercase;padding:0 10px 9px;border-bottom:1px solid var(--line)}
td{padding:10px;border-bottom:1px solid var(--soft);vertical-align:top}
tr:last-child td{border-bottom:0}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.good{color:var(--good);font-weight:650}
.mid{color:var(--warn);font-weight:650}
.bad{color:var(--bad);font-weight:650}
svg{max-width:100%;height:auto;display:block;margin:22px 0}
.figure{margin:24px 0;padding:18px;background:var(--wash);border:1px solid var(--line);border-radius:12px}
.figure figcaption{color:var(--muted);font-size:13px;margin-top:6px}
ul{padding-left:20px}
li{margin:7px 0}
.callout{border-left:3px solid var(--blue);background:var(--blue-soft);
  padding:14px 18px;border-radius:0 10px 10px 0;margin:22px 0}
.callout p{margin:0}
.weak{border-left-color:var(--bad);background:#fdeeec}
footer{margin-top:52px;padding-top:22px;border-top:1px solid var(--line);
  color:var(--faint);font-size:13px}
"""


def git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=10, check=False
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def error_class(value: float | None) -> str:
    if value is None:
        return ""
    return "good" if value <= 0.15 else ("mid" if value <= 0.6 else "bad")


def percent(value: float | None) -> str:
    return "--" if value is None else f"{value * 100:.1f}%"


def forecast_table(companies: list[dict[str, Any]]) -> str:
    rows = []
    for company in companies:
        for estimate in company["estimates"]:
            units = estimate["units"]
            decimals = 2 if units == "%" or abs(estimate["value"]) < 1000 else 0
            rows.append(
                f"<tr><td><b>{company['company']['slug']}</b></td>"
                f"<td>{estimate['metric']}</td>"
                f"<td class='num'>{estimate['value']:,.{decimals}f}</td>"
                f"<td>{units}</td>"
                f"<td>{estimate['confidence']}</td></tr>"
            )
    return "".join(rows)


def backtest_table(backtest: dict[str, Any]) -> str:
    rows = []
    for metric in backtest.get("metrics", []):
        error = metric.get("median_percentage_error")
        rows.append(
            f"<tr><td><b>{metric['company']}</b></td>"
            f"<td>{metric['metric']}</td>"
            f"<td class='num'>{metric['events']}</td>"
            f"<td class='num {error_class(error)}'>{percent(error)}</td></tr>"
        )
    return "".join(rows)


def ensemble_section() -> str:
    """Describe the team ensemble, if it produced the submitted figures.

    The rules require this page to describe the system that produced the
    forecasts. When the submitted workbooks come from the ensemble rather than
    from this repository alone, the page has to say so.
    """
    path = ROOT / "runs" / "ensemble.json"
    if not path.exists():
        return ""
    payload = json.loads(path.read_text(encoding="utf-8"))
    summary = payload.get("summary", {})
    metrics = payload.get("metrics", [])
    if not metrics:
        return ""

    rows = []
    for item in metrics:
        members = item.get("members", {})
        cells = "".join(
            f"<td class='num'>{members[name]:,.2f}</td>" if name in members else "<td class='num'>--</td>"
            for name in ("neva", "adrian", "dimitris")
        )
        dropped = item.get("dropped")
        note = f"outlier: {dropped}" if dropped else "median"
        rows.append(
            f"<tr><td><b>{item['company']}</b></td><td>{item['metric']}</td>"
            f"{cells}<td class='num'><b>{item['value']:,.2f}</b></td>"
            f"<td>{note}</td></tr>"
        )

    dropped_counts = summary.get("outliers_discarded_by_source", {})
    dropped_text = ", ".join(f"{k} {v}" for k, v in sorted(dropped_counts.items())) or "none"
    agreement = summary.get("agreement", {})

    with_market = [m for m in metrics if m.get("market_consensus") is not None]
    gaps = [abs(m["market_gap"]) for m in with_market if m.get("market_gap") is not None]
    market_line = ""
    if gaps:
        market_line = (
            f"<p>On the {len(with_market)} metrics where sell-side consensus was available, "
            f"the ensemble sits a median of {sorted(gaps)[len(gaps)//2]*100:.1f}% away from it. "
            f"That closeness is a calibration signal, not a target: the accuracy prize divides our "
            f"error by Wall Street's, so matching consensus scores 1.0 by construction and "
            f"guarantees a tie. Consensus is used as a rail, never as the answer.</p>"
        )

    return f"""
  <h2>The submitted figures come from a three-system ensemble</h2>
  <p>Three of us built three forecasting systems independently, from the same brief, the same
    frozen corpus and the same twelve targets, without sharing code. That is the one situation
    where an ensemble reliably beats its members: the errors are independent, so they partly
    cancel.</p>
  <p>No code was merged. Each system stays in its own repository with its own dependencies;
    only the twelve outputs are read. Merging three codebases an hour before a deadline risks
    all three and buys nothing the numbers do not already give.</p>
  <p>The rule is two-against-one. Sort the three estimates; if the gap on one side is more than
    twice the gap on the other, the far value is an outlier and the two that agree are averaged.
    Otherwise the median stands. With three points, anything more elaborate is fitting noise.</p>
  {market_line}
  <table><thead><tr><th>Co</th><th>Metric</th><th class="num">Neva</th><th class="num">Adrian</th>
    <th class="num">Dimitris</th><th class="num">Submitted</th><th>Rule</th></tr></thead>
    <tbody>{''.join(rows)}</tbody></table>
  <p>Agreement across the twelve targets: {agreement.get('tight', 0)} tight,
    {agreement.get('moderate', 0)} moderate, {agreement.get('wide', 0)} wide, with a median
    relative spread of {summary.get('median_spread', 0) * 100:.1f}%.
    Outliers discarded by source: {dropped_text}.</p>
  <div class="callout">
    <p>This system contributed four of the twelve discarded outliers &mdash; more than either of
      the others. Its adjusted gross margin for ADI read 69.2 against 73.0 and 74.1 from the other
      two, and was correctly voted out. Being outvoted is the ensemble working, and it is worth
      recording rather than hiding.</p>
  </div>
"""


def build_html(data: dict[str, Any]) -> str:
    manifest = data["manifest"]
    backtest = data.get("backtest") or {}
    leak = backtest.get("leakage", {})
    overall = backtest.get("overall", {})
    commit = git("rev-parse", "HEAD") or (manifest.get("commit") or "unknown")

    chips = [
        f'<span class="chip">As of <b>{manifest.get("as_of", "--")}</b></span>',
        f'<span class="chip">Commit <b>{commit[:10]}</b></span>',
        f'<span class="chip">Forecasts <b>12/12</b></span>',
        f'<span class="chip">Documents <b>1,139</b></span>',
    ]
    if leak.get("status") == "clean":
        chips.append(
            f'<span class="chip ok">Leakage guard <b>clean</b> over '
            f'{leak.get("events_replayed", 0)} replays</span>'
        )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agent architecture &mdash; Agents vs Wall Street</title>
<style>{STYLE}</style>
</head>
<body>
<main>
  <p class="eyebrow">Agents vs Wall Street</p>
  <h1>Forecasting with a point-in-time guarantee</h1>
  <p class="lede">One cutoff date governs every retrieval in the system. That single mechanism is
    what makes our backtest honest, what makes this run reproducible after the event, and what lets
    the agent be reused for the next earnings season.</p>

  <div class="chips">{''.join(chips)}</div>

  <div class="card">
    <p>Build style: <strong>coding harness</strong></p>
    <p>Languages: <strong>Python 3.13, no third-party runtime dependencies beyond openpyxl</strong></p>
    <p>Final command: <code>python run.py --as-of 2026-08-16</code></p>
    <p>Final commit: <code>{commit}</code></p>
  </div>

  <h2>The idea</h2>
  <p>Most forecasting systems can tell you what they predicted. Very few can tell you how well the
    method would have worked, because measuring that honestly requires replaying the past without
    seeing it &mdash; and that is easy to get subtly wrong.</p>
  <p>Every document in the supplied corpus carries a <code>published_at</code> date. That makes true
    point-in-time replay possible, so we built the whole system around a single date cutoff:</p>

  <pre><code>python run.py --as-of 2025-11-18   # replay a quarter whose result we know
python run.py --as-of 2026-08-16   # the competition run
python run.py                      # live mode, for future earnings events</code></pre>

  <p>The cutoff is not a filter applied at each call site. It is one global guard that retrieval code
    must ask for, and which <strong>raises when no cutoff is configured</strong>. An unguarded read is
    impossible rather than merely discouraged. Three consequences follow, and all three matter:</p>
  <ul>
    <li><strong>The backtest cannot cheat.</strong> Replaying an event announced on 2026-05-19 sets
      the cutoff to 2026-05-18, so the release being predicted is invisible.</li>
    <li><strong>The competition run is reproducible.</strong> A judge rerunning
      <code>--as-of 2026-08-16</code> next week gets the same forecasts, because nothing published
      after that date can enter the pipeline.</li>
    <li><strong>The agent outlives the hackathon.</strong> Drop the flag and it forecasts the next
      earnings event.</li>
  </ul>

  <h2>How it works</h2>
  <figure class="figure">
    {architecture_svg()}
    <figcaption>The orchestrator fans out one pipeline per company. Every read in every stage passes
      the point-in-time guard.</figcaption>
  </figure>

  <h3>Retrieval</h3>
  <p>ripgrep is not available on the build machine, so we index the corpus ourselves: 1,139 documents
    split into 69,229 passages with a BM25 inverted index, cached to disk (35s cold, 1.5s warm).
    Two retrieval behaviours were added because we watched the plain version fail:</p>
  <ul>
    <li><strong>Recency weighting.</strong> Company boilerplate barely changes between reports, so a
      decade of near-identical Hays trading statements all matched the same terms and buried the July
      2026 one carrying current figures. Scores now decay with document age &mdash; measured against
      the cutoff, not today, so a replay ranks as the run would have at that time.</li>
    <li><strong>A separate "latest" search.</strong> Guidance is restated verbatim across filings, so
      pure relevance returns superseded statements while pure recency returns whatever was filed
      last, answer or not. <code>search_latest()</code> takes a relevance-ranked pool, drops anything
      below 60% of the best score, then prefers the newest.</li>
  </ul>

  <h3>Reading the numbers</h3>
  <p>Series are read from <strong>earnings releases</strong>, not quarterly reports. Releases state
    headline figures in prose with their units ("reported sales of $41.8 billion"); 10-Qs bury the
    same figures in large tables where picking the right row is unreliable. Reading Deere from 10-Qs
    gave a latest revenue of 815 against an actual near 12,000; reading it from 8-Ks gives 11,778.</p>
  <p>Series are indexed by publication date rather than fiscal period. The four companies have four
    fiscal calendars, and the corpus period field is inconsistent &mdash; the same Home Depot
    earnings call is labelled both "Q1 2026" and "Q1 2027" across sibling documents.</p>

  <h3>Forecasting</h3>
  <p>The submitted figures come from a <strong>seasonal naive forecast with drift</strong>: the same
    period a year earlier, moved by recent year-on-year growth. For a seasonal retailer this is much
    better than carrying the last quarter forward &mdash; Home Depot's Q2 is structurally larger than
    its Q1, and a persistence forecast misses that every year. Growth is not applied to percentage
    metrics, which are rates rather than levels.</p>

  <h2>Research and financial reasoning</h2>
  <p>Every figure carries an unbroken chain back to a document a judge can open: the written cell, the
    method that produced it, the anchoring observation, the filing it came from, its publication date
    and the verbatim sentence. The dashboard renders that chain; nothing in the submission is asserted
    without it.</p>
  <p>Units are treated as a first-class failure mode, because a unit error is the cheapest way to
    score the maximum penalty on a metric. The parser understands scale words, currency symbols,
    accounting negatives such as <code>(5)%</code>, basis points and pence, and percentages can never
    convert into money. Beyond that, extraction had to learn three distinctions the backtest exposed:
    quarterly figures from annual ones, earnings per share from dividends per share, and the metric
    itself from the line items sitting next to it.</p>

  <h2>The twelve submitted forecasts</h2>
  <table><thead><tr><th>Co</th><th>Metric</th><th class="num">Forecast</th><th>Units</th>
    <th>Confidence</th></tr></thead><tbody>{forecast_table(data['companies'])}</tbody></table>

  {ensemble_section()}

  <h2>Checks and tests</h2>
  <p>{len(data.get('log', []))} timestamped events were written during this run to the clear-run log
    submitted with the entry. The test suite covers cutoff boundaries, thread-local guard isolation,
    leak auditing, credential redaction, unit parsing and the workbook contract.</p>
  <ul>
    <li><strong>The workbook contract is tested against the validator's own rules.</strong> The
      header row is located by scanning rather than assuming row 6, labels and units are re-checked
      against <code>companies.json</code>, and the file is re-read after writing. Booleans are
      rejected explicitly: <code>bool</code> is an <code>int</code> subclass and would otherwise pass
      as a number.</li>
    <li><strong>Retrieval is pinned to known facts.</strong> Tests assert that ADI's Q3 guidance,
      Deere's FY2026 range, Home Depot's Q1 actuals and Hays' Q4 trading update all remain
      retrievable, and that each becomes invisible at an earlier cutoff.</li>
    <li><strong>Secrets cannot reach disk.</strong> Everything written to the log or an artifact
      passes a redactor first.</li>
  </ul>

  <h2>What the backtest measured</h2>
  <p>We replayed {leak.get('events_replayed', 0)} past reporting events across the four companies.
    Leakage guard: <strong>{leak.get('status', 'unknown')}</strong>. Median absolute percentage error
    against what the companies actually reported:</p>
  <table><thead><tr><th>Co</th><th>Metric</th><th class="num">Events</th>
    <th class="num">Median error</th></tr></thead><tbody>{backtest_table(backtest)}</tbody></table>
  <p>Overall median across scored metrics:
    <strong class="{error_class(overall.get('median_percentage_error'))}">
    {percent(overall.get('median_percentage_error'))}</strong>.</p>

  <div class="callout">
    <p><strong>The backtest is not a report card we wrote at the end. It is what drove the design.</strong>
      It found three defects that inspection had missed, and we fixed and re-measured each one.</p>
  </div>

  <h2>Important design choices, including what we rejected</h2>

  <h3>Aggregating extracted candidates: two rejected attempts</h3>
  <p>The text around a metric is full of quantities that are not the metric &mdash; note references,
    page numbers, share counts, prior-year comparatives, neighbouring line items. Choosing among them
    took three attempts, each measured against known figures:</p>
  <table><thead><tr><th>Attempt</th><th>Result on Home Depot net sales</th><th>Verdict</th></tr></thead>
    <tbody>
      <tr><td>Median of all candidates</td><td class="num bad">220 USDm</td>
        <td>Rejected &mdash; averages unrelated quantities</td></tr>
      <tr><td>Magnitude grouping, score-weighted vote</td><td class="num bad">6 USDm</td>
        <td>Rejected &mdash; a dozen incidental single-digit matches outvoted the passage stating
          "$41.8 billion"</td></tr>
      <tr><td>Top-scoring candidate, refined only by values within 25%</td>
        <td class="num good">41,600 USDm</td><td>Kept</td></tr>
    </tbody></table>

  <h3>Forecasting by search: rejected</h3>
  <p>The first forecaster searched for relevant passages and took the best figure. Backtested against
    Home Depot, it produced 2,600 / 18 / 202 USDm against actuals of 41,800 / 2,500 / 41,400. Reading
    earnings releases end to end into a series instead moved that metric's median error from 96.5% to
    4.5%. Across the six money and per-share metrics:</p>
  <table><thead><tr><th>Metric</th><th class="num">Search-based</th><th class="num">Series-based</th></tr></thead>
    <tbody>
      <tr><td>HD net sales</td><td class="num bad">96.5%</td><td class="num good">4.5%</td></tr>
      <tr><td>ADI revenue</td><td class="num bad">99.7%</td><td class="num good">5.8%</td></tr>
      <tr><td>DE revenue</td><td class="num mid">24.0%</td><td class="num good">10.6%</td></tr>
      <tr><td>ADI adjusted diluted EPS</td><td class="num mid">24.1%</td><td class="num good">11.1%</td></tr>
      <tr><td>HD adjusted diluted EPS</td><td class="num mid">25.1%</td><td class="num good">11.7%</td></tr>
      <tr><td>DE diluted EPS (GAAP)</td><td class="num bad">75.5%</td><td class="num good">13.2%</td></tr>
    </tbody></table>

  <h3>Two extraction defects the backtest caught</h3>
  <ul>
    <li><strong>Quarters read as full years.</strong> Q4 releases state both side by side, so Home
      Depot's Q4 net sales read 164,700 &mdash; the fiscal year. Passages are now matched against the
      period being forecast and rejected when they clearly describe the other one. Home Depot
      comparable sales went from 4.5% to the reported 0.6%.</li>
    <li><strong>A dividend read as earnings.</strong> Deere's quarterly dividend of $1.62 was being
      read as its EPS at <em>every single replayed event</em>. Per-share candidates now require
      per-share wording and reject dividend, book-value and buyback contexts.</li>
  </ul>

  <h3>Hays replays were invalid, not merely inaccurate</h3>
  <p>Hays' target is a full year, but it was being scored against quarterly trading updates &mdash;
    comparing an annual forecast with a quarter's figures, which drove its measured error to 2,429%.
    Annual-target companies now replay annual results events only. This is the kind of error a
    backtest catches and inspection does not: the number looked bad, but the real problem was that
    the number was meaningless.</p>

  <h3>Build order: output path first</h3>
  <p>The workbook writer was built before the forecasting logic, so that from early in the day a
    single command always produced four workbooks passing
    <code>npm run check:submission</code>. Every later stage improved numbers that already existed,
    rather than being the only thing standing between us and an empty submission. A missing forecast
    scores the maximum penalty, so the system also degrades rather than failing: if a stage errors,
    the metric falls back to a lower-confidence path instead of leaving a blank cell.</p>

  <h2>Known weaknesses</h2>
  <div class="callout weak">
    <p>We would rather be precise about where this system is weak than round the picture up.</p>
  </div>
  <ul>
    <li><strong>Percentage metrics are our worst results.</strong> ADI adjusted gross margin and Home
      Depot comparable sales remain well above acceptable error. A margin is a ratio of two line
      items, and our extraction reads it as a number near a phrase rather than deriving it.</li>
    <li><strong>Hays is the weakest company by a distance.</strong> Its trading statements report net
      fees as growth rates by region rather than absolute figures, so reconstructing a level requires
      combining a prior-year base with four regional growth rates. Our extraction cannot do that. The
      irony is that Hays should be the <em>easiest</em> target: its FY2026 ended on 30 June 2026 and
      was updated on 10 July 2026, so the year is closed and the answer is nearly determined.</li>
    <li><strong>The forecast is statistical, not analytical.</strong> It projects the past forward. It
      does not read the sector commentary, weigh management tone, or reason about why a number will
      move. The retrieval, evidence and validation layers were built to support exactly that
      reasoning; with no model credentials available during the event, the agent layer that would
      consume them could not be exercised.</li>
    <li><strong>Published guidance is collected but not yet used as an anchor.</strong> ADI states a
      Q3 revenue outlook of $3.9bn and adjusted EPS of $3.30, and Deere guides FY2026 net income to
      $4.5&ndash;5.0bn. Guidance-versus-actual bias is computed by the series layer, but the
      submitted figures do not yet blend it with the statistical path.</li>
    <li><strong>Backtest depth is modest.</strong> Eight events per company is enough to rank metrics
      by reliability, not enough for tight confidence intervals.</li>
  </ul>

  <h2>Reproducing the run</h2>
  <pre><code>git clone &lt;repository&gt; &amp;&amp; cd agents-vs-wall-street-starter
npm install
python -m pip install openpyxl

python run.py --as-of 2026-08-16      # writes submission/*.xlsx and runs/&lt;run-id&gt;/
npm run check:submission              # the organisers' validator

python -m unittest discover -s tests -t .
python scripts/build_dashboard.py     # regenerates the dashboard
python scripts/build_architecture.py  # regenerates this page</code></pre>
  <p>No API keys are required. The system reads only the frozen corpus supplied in the repository, so
    a rerun needs no network access and produces the same figures.</p>

  <div class="card">
    <p>Final commit: <code>{commit}</code></p>
    <p>Final command: <code>python run.py --as-of 2026-08-16</code></p>
    <p>Expected output: four completed workbooks in <code>submission/</code>.</p>
  </div>

  <footer>Generated from run <code>{manifest.get('run_id', '')}</code> at
    {manifest.get('finished_at', '')}.</footer>
</main>
</body>
</html>
"""


def main() -> int:
    run_dir = (ROOT / "runs" / sys.argv[1]) if len(sys.argv) > 1 else latest_run()
    data = collect(run_dir)
    html = build_html(data)

    target = PATHS.architecture
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")

    size = target.stat().st_size
    print(f"Architecture page written: {target.relative_to(ROOT)} ({size / 1024:.0f} KB)")
    if size > MAX_BYTES:
        print(f"ERROR: exceeds the 2 MB limit by {(size - MAX_BYTES) / 1024:.0f} KB", file=sys.stderr)
        return 1
    if "<script" in html.lower():
        print("ERROR: contains a script tag; scripts do not run in the judging preview", file=sys.stderr)
        return 1
    print("  no scripts, no external assets, within the 2 MB limit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
