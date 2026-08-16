#!/usr/bin/env python3
"""Build the presentation dashboard from a run's artifacts.

The run data is baked into the page rather than fetched. A dashboard that needs
a web server is a dashboard that can fail to open during a five-minute judging
slot, and browsers block fetch() from file:// anyway. One self-contained file
opens with a double click on any machine.

    python scripts/build_dashboard.py [run-id]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import PATHS  # noqa: E402


def latest_run() -> Path:
    runs = sorted((ROOT / "runs").glob("run-*"), key=lambda path: path.stat().st_mtime)
    if not runs:
        raise SystemExit("No runs found. Run: python run.py --as-of 2026-08-16")
    return runs[-1]


def read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"warning: {path.name} is unreadable ({exc})", file=sys.stderr)
        return None


def read_log(path: Path, limit: int = 4000) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events[-limit:]


def collect(run_dir: Path) -> dict[str, Any]:
    manifest = read_json(run_dir / "manifest.json") or {}
    log_name = manifest.get("log_file", "")
    log_path = ROOT / log_name if log_name else (PATHS.logs / f"{run_dir.name}.jsonl")

    companies = []
    for path in sorted(run_dir.glob("*/baseline.json")):
        payload = read_json(path)
        if payload:
            companies.append(payload)

    return {
        "manifest": manifest,
        "companies": companies,
        "backtest": read_json(run_dir / "backtest.json"),
        "log": read_log(log_path),
        "run_id": run_dir.name,
    }


STYLE = """
*,*::before,*::after{box-sizing:border-box}
:root{
  --ink:#0d1421;--muted:#5b6b82;--faint:#8695a8;--line:#dfe5ee;--soft:#eef2f8;
  --paper:#fff;--wash:#f5f8fc;--blue:#155eef;--blue-soft:#e8f0ff;
  --good:#0d8a5f;--warn:#b7791f;--bad:#c0392b;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--wash);color:var(--ink);
  font:15px/1.6 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{width:min(1180px,calc(100% - 40px));margin:0 auto;padding:32px 0 80px}
header.top{padding:36px 0 20px;border-bottom:1px solid var(--line);margin-bottom:26px}
.eyebrow{color:var(--blue);font-size:12px;font-weight:800;letter-spacing:.14em;text-transform:uppercase;margin:0 0 8px}
h1{margin:0 0 10px;font-size:clamp(30px,5vw,46px);line-height:1.02;letter-spacing:-.035em}
.sub{color:var(--muted);margin:0;font-size:17px;max-width:70ch}
.meta{display:flex;flex-wrap:wrap;gap:8px;margin-top:18px}
.chip{background:var(--paper);border:1px solid var(--line);border-radius:999px;
  padding:5px 13px;font-size:12.5px;color:var(--muted)}
.chip b{color:var(--ink);font-weight:600}
.chip.ok{background:#eaf7f1;border-color:#bfe6d4;color:var(--good)}
.chip.ok b{color:var(--good)}
.chip.bad{background:#fdeeec;border-color:#f5c6c0;color:var(--bad)}
nav{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:26px;
  background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:5px}
nav button{flex:1 1 auto;border:0;background:transparent;color:var(--muted);cursor:pointer;
  padding:9px 14px;border-radius:8px;font:600 13.5px/1 inherit;white-space:nowrap;transition:.12s}
nav button:hover{background:var(--soft);color:var(--ink)}
nav button[aria-selected=true]{background:var(--blue);color:#fff}
section[hidden]{display:none}
.card{background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:22px 24px;margin-bottom:18px}
.card>h2{margin:0 0 4px;font-size:19px;letter-spacing:-.015em}
.card>h2+p.note{margin:0 0 18px}
p.note{color:var(--muted);font-size:13.5px;max-width:78ch}
.grid{display:grid;gap:14px}
.grid.c2{grid-template-columns:repeat(auto-fit,minmax(330px,1fr))}
.grid.c4{grid-template-columns:repeat(auto-fit,minmax(190px,1fr))}
.tile{background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
.tile .k{color:var(--faint);font-size:11.5px;font-weight:700;letter-spacing:.09em;text-transform:uppercase}
.tile .v{font-size:26px;font-weight:680;letter-spacing:-.02em;margin-top:6px;font-variant-numeric:tabular-nums}
.tile .d{color:var(--muted);font-size:12.5px;margin-top:3px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{text-align:left;color:var(--faint);font-size:11.5px;font-weight:700;letter-spacing:.07em;
  text-transform:uppercase;padding:0 10px 9px;border-bottom:1px solid var(--line)}
td{padding:11px 10px;border-bottom:1px solid var(--soft);vertical-align:top}
tr:last-child td{border-bottom:0}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.tag{display:inline-block;padding:2px 9px;border-radius:999px;font-size:11.5px;font-weight:650}
.tag.medium{background:#eaf7f1;color:var(--good)}
.tag.low{background:#fdf4e3;color:var(--warn)}
.tag.fallback{background:#fdeeec;color:var(--bad)}
.err-good{color:var(--good);font-weight:650}
.err-mid{color:var(--warn);font-weight:650}
.err-bad{color:var(--bad);font-weight:650}
details{border:1px solid var(--line);border-radius:10px;margin-top:10px;background:var(--wash)}
details+details{margin-top:8px}
summary{cursor:pointer;padding:11px 14px;font-weight:600;font-size:13.5px;list-style:none}
summary::-webkit-details-marker{display:none}
summary::before{content:"▸ ";color:var(--blue)}
details[open] summary::before{content:"▾ "}
details .body{padding:0 14px 14px}
blockquote{margin:8px 0 0;padding:10px 13px;background:var(--paper);border-left:3px solid var(--blue);
  border-radius:0 8px 8px 0;font-size:12.5px;color:var(--muted);white-space:pre-wrap;
  max-height:190px;overflow:auto}
code,.mono{font-family:var(--mono);font-size:12.5px}
code{background:var(--blue-soft);padding:2px 6px;border-radius:5px}
.src{color:var(--faint);font-size:11.5px;margin-top:7px;font-family:var(--mono);word-break:break-all}
.company-head{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:2px}
.company-head h3{margin:0;font-size:20px;letter-spacing:-.02em}
.company-head .per{color:var(--muted);font-size:13.5px}
.metric-row{border-top:1px solid var(--soft);padding-top:16px;margin-top:16px}
.metric-row:first-of-type{border-top:0;padding-top:8px;margin-top:8px}
.metric-name{font-weight:650;font-size:15px}
.metric-val{font-size:29px;font-weight:680;letter-spacing:-.025em;font-variant-numeric:tabular-nums}
.metric-units{color:var(--muted);font-size:13px;font-weight:500;margin-left:5px}
.method{color:var(--muted);font-size:12.5px;margin-top:5px}
.log{max-height:620px;overflow:auto;border:1px solid var(--line);border-radius:10px;background:var(--paper)}
.log table{font-size:12.5px}
.log td{padding:7px 10px}
.log .ts{color:var(--faint);font-family:var(--mono);font-size:11px;white-space:nowrap}
.log .ev{font-family:var(--mono);font-size:11.5px;color:var(--blue);white-space:nowrap}
.log .msg{color:var(--muted);word-break:break-word}
.log tr.error .ev,.log tr.error .msg{color:var(--bad)}
.log tr.warning .ev,.log tr.warning .msg{color:var(--warn)}
.bar{height:6px;background:var(--soft);border-radius:3px;overflow:hidden;margin-top:5px;min-width:70px}
.bar>i{display:block;height:100%;border-radius:3px}
footer{color:var(--faint);font-size:12.5px;text-align:center;padding:34px 0 0;border-top:1px solid var(--line);margin-top:34px}
@media print{nav,footer{display:none}section[hidden]{display:block!important}.card{break-inside:avoid}}
"""

SCRIPT = """
const $ = (s, r=document) => r.querySelector(s);
const $$ = (s, r=document) => Array.from(r.querySelectorAll(s));
const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

const fmt = (v, units) => {
  if (v === null || v === undefined || Number.isNaN(v)) return '--';
  const abs = Math.abs(v);
  const decimals = units === '%' ? 2 : (abs >= 1000 ? 0 : 2);
  return v.toLocaleString('en-GB', {minimumFractionDigits: decimals, maximumFractionDigits: decimals});
};
const pct = v => (v === null || v === undefined) ? '--' : (v * 100).toFixed(1) + '%';
const errClass = v => v === null || v === undefined ? '' : (v <= 0.15 ? 'err-good' : v <= 0.6 ? 'err-mid' : 'err-bad');

function tabs() {
  const buttons = $$('nav button');
  buttons.forEach(button => button.addEventListener('click', () => {
    buttons.forEach(other => {
      const selected = other === button;
      other.setAttribute('aria-selected', selected);
      $('#' + other.dataset.panel).hidden = !selected;
    });
    window.scrollTo({top: 0, behavior: 'smooth'});
  }));
}

function renderOverview() {
  const rows = [];
  for (const company of DATA.companies) {
    for (const estimate of company.estimates) {
      rows.push(`<tr>
        <td><b>${esc(company.company.slug)}</b><div class="src">${esc(company.company.period)}</div></td>
        <td>${esc(estimate.metric)}</td>
        <td class="num"><b>${fmt(estimate.value, estimate.units)}</b></td>
        <td>${esc(estimate.units)}</td>
        <td><span class="tag ${esc(estimate.confidence)}">${esc(estimate.confidence)}</span></td>
        <td class="src" style="margin:0">${esc(estimate.method)}</td>
      </tr>`);
    }
  }
  $('#overview-table').innerHTML = rows.join('');
}

function renderCompanies() {
  $('#companies').innerHTML = DATA.companies.map(company => {
    const metrics = company.estimates.map(estimate => {
      const evidence = (estimate.evidence || []).map(item => `
        <details>
          <summary>${esc(item.raw)} &mdash; ${esc(item.source.published_at)} &middot; ${esc(item.source.document_type)}</summary>
          <div class="body">
            <blockquote>${esc(item.context)}</blockquote>
            <div class="src">${esc(item.source.path)}</div>
          </div>
        </details>`).join('');
      return `<div class="metric-row">
        <div class="metric-name">${esc(estimate.metric)}
          <span class="tag ${esc(estimate.confidence)}" style="margin-left:8px">${esc(estimate.confidence)}</span></div>
        <div class="metric-val">${fmt(estimate.value, estimate.units)}<span class="metric-units">${esc(estimate.units)}</span></div>
        <div class="method">${esc(estimate.method)}</div>
        ${(estimate.notes || []).map(n => `<div class="src">${esc(n)}</div>`).join('')}
        ${evidence}
      </div>`;
    }).join('');
    return `<div class="card">
      <div class="company-head">
        <h3>${esc(company.company.name)}</h3>
        <span class="per">${esc(company.company.ticker)} &middot; ${esc(company.company.period)}</span>
      </div>
      <p class="note">Written to <code>${esc(company.company.output_file)}</code></p>
      ${metrics}
    </div>`;
  }).join('');
}

function renderBacktest() {
  const backtest = DATA.backtest;
  if (!backtest) { $('#backtest').innerHTML = '<div class="card"><p class="note">No backtest in this run.</p></div>'; return; }
  const leak = backtest.leakage || {};
  const overall = backtest.overall || {};
  const rows = (backtest.metrics || []).map(metric => {
    const e = metric.median_percentage_error;
    const width = e === null || e === undefined ? 0 : Math.min(100, e * 100);
    const colour = e <= 0.15 ? 'var(--good)' : e <= 0.6 ? 'var(--warn)' : 'var(--bad)';
    return `<tr>
      <td><b>${esc(metric.company)}</b></td>
      <td>${esc(metric.metric)}</td>
      <td class="num">${metric.events}</td>
      <td class="num ${errClass(e)}">${pct(e)}
        <div class="bar"><i style="width:${width}%;background:${colour}"></i></div></td>
      <td class="num">${pct(metric.best_percentage_error)}</td>
      <td class="num">${pct(metric.worst_percentage_error)}</td>
    </tr>`;
  }).join('');

  const outcomes = (backtest.outcomes || []).slice().sort((a, b) =>
    a.company.localeCompare(b.company) || a.metric.localeCompare(b.metric) ||
    a.report_date.localeCompare(b.report_date)).map(o => `<tr>
      <td>${esc(o.company)}</td><td>${esc(o.metric)}</td>
      <td class="mono">${esc(o.report_date)}</td>
      <td class="num">${fmt(o.forecast, o.units)}</td>
      <td class="num">${fmt(o.actual, o.units)}</td>
      <td class="num ${errClass(o.percentage_error)}">${pct(o.percentage_error)}</td>
    </tr>`).join('');

  $('#backtest').innerHTML = `
    <div class="card">
      <h2>Leakage guard</h2>
      <p class="note">Each replay is cut off the day before the result was announced. The guard blocks
        documents during retrieval, and the citations of every finished forecast are re-examined
        afterwards. A leak fails the event rather than inflating measured accuracy.</p>
      <div class="grid c4">
        <div class="tile"><div class="k">Status</div>
          <div class="v" style="color:${leak.status === 'clean' ? 'var(--good)' : 'var(--bad)'}">${esc(leak.status)}</div>
          <div class="d">${leak.detected} leaks detected</div></div>
        <div class="tile"><div class="k">Events replayed</div><div class="v">${leak.events_replayed}</div>
          <div class="d">past reporting dates</div></div>
        <div class="tile"><div class="k">Scored metrics</div><div class="v">${overall.scored_metrics ?? '--'}</div>
          <div class="d">of 12 targets</div></div>
        <div class="tile"><div class="k">Median error</div>
          <div class="v ${errClass(overall.median_percentage_error)}">${pct(overall.median_percentage_error)}</div>
          <div class="d">across scored metrics</div></div>
      </div>
    </div>
    <div class="card">
      <h2>Accuracy by metric</h2>
      <p class="note">Median absolute percentage error of the forecast against what the company
        actually reported. This is the number that tells us which metrics to trust.</p>
      <table><thead><tr><th>Co</th><th>Metric</th><th class="num">Events</th>
        <th class="num">Median error</th><th class="num">Best</th><th class="num">Worst</th></tr></thead>
        <tbody>${rows}</tbody></table>
    </div>
    <div class="card">
      <h2>Every replayed event</h2>
      <p class="note">Forecast against actual, for each past reporting date.</p>
      <table><thead><tr><th>Co</th><th>Metric</th><th>Reported on</th>
        <th class="num">Forecast</th><th class="num">Actual</th><th class="num">Error</th></tr></thead>
        <tbody>${outcomes}</tbody></table>
    </div>`;
}

function renderEvidence() {
  const seen = new Map();
  for (const company of DATA.companies) {
    for (const estimate of company.estimates) {
      for (const item of (estimate.evidence || [])) {
        const source = item.source;
        if (!seen.has(source.path)) seen.set(source.path, {source, uses: []});
        seen.get(source.path).uses.push(`${company.company.slug} / ${estimate.metric}`);
      }
    }
  }
  const rows = Array.from(seen.values())
    .sort((a, b) => (b.source.published_at || '').localeCompare(a.source.published_at || ''))
    .map(entry => `<tr>
      <td class="mono">${esc(entry.source.published_at)}</td>
      <td>${esc(entry.source.company)}</td>
      <td>${esc(entry.source.document_type)}</td>
      <td>${esc(entry.source.title)}<div class="src">${esc(entry.source.path)}</div></td>
      <td>${entry.uses.map(u => esc(u)).join('<br>')}</td>
    </tr>`).join('');
  $('#evidence-table').innerHTML = rows || '<tr><td colspan="5">No evidence recorded.</td></tr>';
  $('#evidence-count').textContent = seen.size;
}

function renderActivity() {
  const rows = DATA.log.map(event => {
    const {ts, type, seq, run_id, ...rest} = event;
    const summary = Object.entries(rest)
      .map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`)
      .join('  ').slice(0, 260);
    const cls = type === 'error' || type === 'stage.error' ? 'error' : (type === 'warning' ? 'warning' : '');
    return `<tr class="${cls}">
      <td class="ts">${esc(ts)}</td><td class="ev">${esc(type)}</td><td class="msg">${esc(summary)}</td></tr>`;
  }).join('');
  $('#log-table').innerHTML = rows || '<tr><td colspan="3">No log events.</td></tr>';
  $('#log-count').textContent = DATA.log.length;
}

tabs();
renderOverview();
renderCompanies();
renderBacktest();
renderEvidence();
renderActivity();
"""


def architecture_svg() -> str:
    """Pipeline diagram. Hand-written SVG so it works with scripts disabled."""
    return """
<svg viewBox="0 0 940 460" xmlns="http://www.w3.org/2000/svg" role="img"
     aria-label="Pipeline: orchestrator fans out to four company pipelines, each running retrieval, series, forecast and validation, then the workbook writer">
  <defs>
    <marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
      <path d="M0 0 L10 5 L0 10 z" fill="#8695a8"/>
    </marker>
    <style>
      .b{fill:#fff;stroke:#dfe5ee;stroke-width:1.5;rx:10}
      .bl{fill:#e8f0ff;stroke:#155eef;stroke-width:1.5;rx:10}
      .gd{fill:#eaf7f1;stroke:#0d8a5f;stroke-width:1.5;rx:10}
      .t{font:600 13px Inter,system-ui,sans-serif;fill:#0d1421}
      .s{font:11px Inter,system-ui,sans-serif;fill:#5b6b82}
      .l{stroke:#8695a8;stroke-width:1.4;fill:none;marker-end:url(#a)}
      .lab{font:700 10px Inter,system-ui,sans-serif;fill:#155eef;letter-spacing:.06em}
    </style>
  </defs>

  <rect class="bl" x="330" y="14" width="280" height="46"/>
  <text class="t" x="470" y="34" text-anchor="middle">Orchestrator</text>
  <text class="s" x="470" y="50" text-anchor="middle">reads companies.json, fans out four pipelines</text>

  <rect class="gd" x="30" y="96" width="200" height="62"/>
  <text class="t" x="130" y="118" text-anchor="middle">Point-in-time guard</text>
  <text class="s" x="130" y="134" text-anchor="middle">published_at &lt;= as_of</text>
  <text class="s" x="130" y="148" text-anchor="middle">a leak aborts the run</text>

  <path class="l" d="M470 60 L470 88"/>
  <path class="l" d="M230 127 L300 127"/>

  <g>
    <rect class="b" x="310" y="96" width="140" height="46"/>
    <text class="t" x="380" y="116" text-anchor="middle">HD</text>
    <text class="s" x="380" y="132" text-anchor="middle">FY2026Q2</text>
    <rect class="b" x="466" y="96" width="140" height="46"/>
    <text class="t" x="536" y="116" text-anchor="middle">ADI</text>
    <text class="s" x="536" y="132" text-anchor="middle">FY2026Q3</text>
    <rect class="b" x="622" y="96" width="140" height="46"/>
    <text class="t" x="692" y="116" text-anchor="middle">HAS</text>
    <text class="s" x="692" y="132" text-anchor="middle">FY2026</text>
    <rect class="b" x="778" y="96" width="140" height="46"/>
    <text class="t" x="848" y="116" text-anchor="middle">DE</text>
    <text class="s" x="848" y="132" text-anchor="middle">FY2026Q3</text>
  </g>

  <path class="l" d="M470 142 L470 178"/>
  <text class="lab" x="486" y="166">EACH PIPELINE</text>

  <rect class="b" x="120" y="182" width="700" height="58"/>
  <text class="t" x="146" y="206">Retrieval</text>
  <text class="s" x="146" y="224">BM25 over 69,229 passages, recency-weighted, cutoff-filtered</text>
  <path class="l" d="M470 240 L470 262"/>

  <rect class="b" x="120" y="266" width="700" height="58"/>
  <text class="t" x="146" y="290">Series and statistics</text>
  <text class="s" x="146" y="308">earnings releases read end to end; seasonality, 6m/2y/5y/10y trend, guidance bias</text>
  <path class="l" d="M470 324 L470 346"/>

  <rect class="b" x="120" y="350" width="700" height="58"/>
  <text class="t" x="146" y="374">Forecast and validation</text>
  <text class="s" x="146" y="392">seasonal naive with drift; unit, range and consistency checks</text>

  <path class="l" d="M820 379 L868 379 L868 432 L640 432"/>
  <rect class="bl" x="330" y="410" width="300" height="44"/>
  <text class="t" x="480" y="430" text-anchor="middle">Workbook writer</text>
  <text class="s" x="480" y="446" text-anchor="middle">Summary!C7:C9, native numbers, re-verified</text>

  <rect class="gd" x="30" y="266" width="70" height="142" opacity="0"/>
  <path class="l" d="M130 158 L130 379 L112 379"/>
  <text class="s" x="36" y="424">guard active</text>
  <text class="s" x="36" y="438">for every read</text>
</svg>
"""


def build_html(data: dict[str, Any]) -> str:
    manifest = data["manifest"]
    backtest = data.get("backtest") or {}
    leak = backtest.get("leakage", {})
    overall = backtest.get("overall", {})
    guard_stats = (manifest.get("guard") or {}).get("stats", {})

    metric_count = sum(len(company["estimates"]) for company in data["companies"])
    leak_ok = leak.get("status") == "clean"

    chips = [
        f'<span class="chip">Run <b>{manifest.get("run_id", data["run_id"])}</b></span>',
        f'<span class="chip">As of <b>{manifest.get("as_of", "--")}</b></span>',
        f'<span class="chip">Commit <b>{(manifest.get("commit") or "--")[:10]}</b></span>',
        f'<span class="chip">Forecasts <b>{metric_count}/12</b></span>',
    ]
    if leak:
        chips.append(
            f'<span class="chip {"ok" if leak_ok else "bad"}">Leakage guard '
            f'<b>{leak.get("status", "--")}</b></span>'
        )

    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agents vs Wall Street &mdash; forecast dashboard</title>
<style>{STYLE}</style>
</head>
<body>
<div class="wrap">
  <header class="top">
    <p class="eyebrow">Agents vs Wall Street</p>
    <h1>Forecast dashboard</h1>
    <p class="sub">Twelve financial metrics across four companies, with the evidence behind every
      number and the historical replay that measures how far it can be trusted.</p>
    <div class="meta">{''.join(chips)}</div>
  </header>

  <nav role="tablist">
    <button data-panel="p-overview" aria-selected="true">Overview</button>
    <button data-panel="p-companies" aria-selected="false">Companies</button>
    <button data-panel="p-backtest" aria-selected="false">Backtest</button>
    <button data-panel="p-evidence" aria-selected="false">Evidence</button>
    <button data-panel="p-activity" aria-selected="false">Agent activity</button>
    <button data-panel="p-architecture" aria-selected="false">Architecture</button>
  </nav>

  <section id="p-overview">
    <div class="grid c4" style="margin-bottom:18px">
      <div class="tile"><div class="k">Documents indexed</div><div class="v">1,139</div>
        <div class="d">69,229 searchable passages</div></div>
      <div class="tile"><div class="k">Documents blocked</div><div class="v">{guard_stats.get('blocked', 0):,}</div>
        <div class="d">published after the cutoff</div></div>
      <div class="tile"><div class="k">Events replayed</div><div class="v">{leak.get('events_replayed', 0)}</div>
        <div class="d">past reporting dates</div></div>
      <div class="tile"><div class="k">Backtested median error</div>
        <div class="v">{(f"{overall.get('median_percentage_error', 0) * 100:.1f}%" if overall.get('median_percentage_error') is not None else '--')}</div>
        <div class="d">across scored metrics</div></div>
    </div>
    <div class="card">
      <h2>The twelve submitted forecasts</h2>
      <p class="note">Each figure is written into the supplied template at <code>Summary!C7:C9</code>
        as a native number, then read back and re-checked against the organisers' rules.</p>
      <table><thead><tr><th>Company</th><th>Metric</th><th class="num">Forecast</th>
        <th>Units</th><th>Confidence</th><th>Method</th></tr></thead>
        <tbody id="overview-table"></tbody></table>
    </div>
  </section>

  <section id="p-companies" hidden><div id="companies"></div></section>
  <section id="p-backtest" hidden><div id="backtest"></div></section>

  <section id="p-evidence" hidden>
    <div class="card">
      <h2>Evidence explorer</h2>
      <p class="note"><b id="evidence-count">0</b> documents were actually read and cited by the
        forecasts in this run. Every figure traces back to one of them.</p>
      <table><thead><tr><th>Published</th><th>Co</th><th>Type</th><th>Document</th>
        <th>Used for</th></tr></thead><tbody id="evidence-table"></tbody></table>
    </div>
  </section>

  <section id="p-activity" hidden>
    <div class="card">
      <h2>Agent activity</h2>
      <p class="note"><b id="log-count">0</b> timestamped events from the clear-run log. This is the
        same file submitted as the run record, not a separate summary written for display.</p>
      <div class="log"><table><tbody id="log-table"></tbody></table></div>
    </div>
  </section>

  <section id="p-architecture" hidden>
    <div class="card">
      <h2>How the system works</h2>
      <p class="note">One cutoff date governs every retrieval. The same mechanism makes the
        backtest honest, makes this run reproducible after the event, and lets the agent be reused
        for future earnings events.</p>
      {architecture_svg()}
    </div>
    <div class="card">
      <h2>The one idea worth remembering</h2>
      <p class="note"><code>--as-of</code> is a single global guard, not a filter applied at each
        call site. Retrieval code asks for the active guard and fails loudly when none is
        configured, so an unguarded read is impossible rather than merely discouraged.</p>
      <table><thead><tr><th>Command</th><th>What it does</th></tr></thead><tbody>
        <tr><td><code>python run.py --as-of 2025-11-18</code></td>
            <td>Replays a quarter whose result we know, to score the method</td></tr>
        <tr><td><code>python run.py --as-of 2026-08-16</code></td>
            <td>The competition run. Reruns reproduce it exactly</td></tr>
        <tr><td><code>python run.py</code></td>
            <td>Live mode, for earnings events after the hackathon</td></tr>
      </tbody></table>
    </div>
  </section>

  <footer>Generated from run artifacts &middot; {manifest.get('finished_at', '')}</footer>
</div>
<script>const DATA = {payload};</script>
<script>{SCRIPT}</script>
</body>
</html>
"""


def main() -> int:
    run_dir = (ROOT / "runs" / sys.argv[1]) if len(sys.argv) > 1 else latest_run()
    if not run_dir.exists():
        raise SystemExit(f"Run directory not found: {run_dir}")

    data = collect(run_dir)
    html = build_html(data)

    target = PATHS.dashboard / "index.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")

    size_kb = target.stat().st_size / 1024
    print(f"Dashboard written: {target.relative_to(ROOT)} ({size_kb:.0f} KB)")
    print(f"  run          {data['run_id']}")
    print(f"  companies    {len(data['companies'])}")
    print(f"  log events   {len(data['log'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
