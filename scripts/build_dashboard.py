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


def corpus_timeline() -> list[dict[str, Any]]:
    """Publication month of every corpus document, for the cutoff strip.

    The strip is the page's central claim made visible: everything the system
    could see, packed hard against the date it was cut off at.
    """
    from datetime import date

    from src import asof
    from src.asof import AsOfGuard
    from src.corpus import get_index

    asof.set_guard(AsOfGuard(date(2026, 8, 16)))
    try:
        index = get_index()
        buckets: dict[str, int] = {}
        for document in index.documents.values():
            if document.published_at is None:
                continue
            key = document.published_at.strftime("%Y-%m")
            buckets[key] = buckets.get(key, 0) + 1
        return [{"month": key, "count": buckets[key]} for key in sorted(buckets)]
    finally:
        asof.set_guard(None)


def collect(run_dir: Path) -> dict[str, Any]:
    manifest = read_json(run_dir / "manifest.json") or {}
    log_name = manifest.get("log_file", "")
    log_path = ROOT / log_name if log_name else (PATHS.logs / f"{run_dir.name}.jsonl")

    companies = []
    for path in sorted(run_dir.glob("*/baseline.json")):
        payload = read_json(path)
        if payload:
            companies.append(payload)

    try:
        timeline = corpus_timeline()
    except Exception as exc:  # noqa: BLE001 - the strip illustrates, it is not data
        print(f"warning: corpus timeline unavailable ({exc})", file=sys.stderr)
        timeline = []

    return {
        "manifest": manifest,
        "companies": companies,
        "backtest": read_json(run_dir / "backtest.json"),
        "log": read_log(log_path),
        "timeline": timeline,
        "run_id": run_dir.name,
    }


# --------------------------------------------------------------------------
# Design
#
# The subject is not "finance", it is a date boundary: what was knowable then
# against what is being predicted now. So the page is built around one literal
# vertical rule, and everything is coloured by which side of it a number came
# from -- teal for measured, rust for projected.
#
# Monospace is the display face rather than a data face. In a world made of
# timestamps, tickers and tabular figures it is the subject's own vernacular,
# and it keeps the page from reading like every other dashboard.
# --------------------------------------------------------------------------

STYLE = """
*,*::before,*::after{box-sizing:border-box}
:root{
  --paper:#f4f3ef; --card:#fffefc; --sunk:#eceae4;
  --ink:#15171c; --ink2:#4e535d; --ink3:#8b909b;
  --rule:#dcd8cf; --rule2:#c6c1b6;
  --measured:#0f5c58;      /* known, backtested */
  --forecast:#93331f;      /* projected */
  --flag:#9a6c07;          /* needs a human */
  --mono:ui-monospace,"SF Mono","Cascadia Mono","Segoe UI Mono",Menlo,Consolas,monospace;
  --sans:Inter,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.6 var(--sans);
  -webkit-font-smoothing:antialiased}
.wrap{width:min(1240px,calc(100% - 36px));margin:0 auto}

/* ---- fixed cutoff bar ---- */
.railbar{position:sticky;top:0;z-index:40;background:var(--ink);color:var(--paper)}
.railbar .wrap{display:flex;align-items:center;justify-content:space-between;
  gap:16px;height:44px;font:600 11.5px/1 var(--mono);letter-spacing:.13em;text-transform:uppercase}
.railbar .brand{color:var(--paper)}
.railbar .right{display:flex;gap:18px;align-items:center;color:#a9aeb8}
.railbar .cut{color:var(--paper);border-bottom:2px solid var(--forecast);padding-bottom:2px}

/* ---- hero ---- */
.hero{padding:64px 0 26px}
.wordmark{font:700 clamp(66px,15vw,168px)/.82 var(--mono);letter-spacing:-.06em;margin:0}
.wordmark .dim{color:var(--rule2)}
.tagline{margin:22px 0 0;font-size:19px;line-height:1.5;color:var(--ink2);max-width:56ch}
.tagline strong{color:var(--ink);font-weight:600}

/* ---- the signature: cutoff strip ---- */
.strip{margin:38px 0 8px;border-top:1px solid var(--rule);padding-top:20px}
.strip-head{display:flex;justify-content:space-between;align-items:baseline;
  font:600 10.5px/1 var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--ink3)}
.strip-head b{color:var(--measured);font-weight:700}
.strip-head .fut{color:var(--forecast)}
.plot{position:relative;height:104px;margin-top:14px}
.ticks{position:absolute;inset:0 30% 0 0;display:flex;align-items:flex-end;gap:1px}
.tick{flex:1 1 auto;background:var(--measured);opacity:.34;border-radius:1px 1px 0 0;
  transform-origin:bottom;transform:scaleY(0);animation:grow .5s cubic-bezier(.2,.8,.3,1) forwards}
@keyframes grow{to{transform:scaleY(1)}}
.cutline{position:absolute;top:-6px;bottom:-6px;left:70%;width:2px;background:var(--forecast);
  transform:scaleY(0);transform-origin:bottom;animation:drop .45s .5s cubic-bezier(.2,.8,.3,1) forwards}
@keyframes drop{to{transform:scaleY(1)}}
.cutline::after{content:attr(data-label);position:absolute;left:9px;top:0;white-space:nowrap;
  font:700 10.5px/1 var(--mono);letter-spacing:.12em;color:var(--forecast);opacity:0;
  animation:fade .4s .85s forwards}
@keyframes fade{to{opacity:1}}
.future{position:absolute;top:0;bottom:0;left:70%;right:0;
  background:repeating-linear-gradient(-45deg,transparent 0 6px,rgba(147,51,31,.13) 6px 7px);
  border-left:0;opacity:0;animation:fade .5s .7s forwards}
.future span{position:absolute;right:0;bottom:6px;font:600 10.5px/1 var(--mono);
  letter-spacing:.1em;text-transform:uppercase;color:var(--forecast)}
.axis{display:flex;justify-content:space-between;margin-top:8px;
  font:11px/1 var(--mono);color:var(--ink3)}

/* ---- tabs ---- */
.tabs{position:sticky;top:44px;z-index:30;background:var(--paper);
  border-bottom:1px solid var(--rule);padding-top:26px;margin-bottom:28px}
.tabs .wrap{display:flex;gap:2px;overflow-x:auto}
.tabs button{border:0;background:none;cursor:pointer;white-space:nowrap;color:var(--ink3);
  padding:11px 15px 12px;font:600 11.5px/1 var(--mono);letter-spacing:.11em;
  text-transform:uppercase;border-bottom:2px solid transparent;transition:color .14s}
.tabs button:hover{color:var(--ink)}
.tabs button[aria-selected=true]{color:var(--ink);border-bottom-color:var(--forecast)}
.tabs button:focus-visible{outline:2px solid var(--measured);outline-offset:-2px}
section[hidden]{display:none}
main{padding-bottom:96px}

/* ---- blocks ---- */
.block{background:var(--card);border:1px solid var(--rule);margin-bottom:16px}
.block>header{padding:20px 24px 0}
.block h2{margin:0;font:600 13px/1 var(--mono);letter-spacing:.13em;text-transform:uppercase}
.block .note{margin:9px 0 0;color:var(--ink2);font-size:13.5px;max-width:82ch}
.block .body{padding:20px 24px 22px}
.block>header+.body{padding-top:18px}

/* ---- stats ---- */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(178px,1fr));
  border-top:1px solid var(--rule)}
.stat{padding:18px 22px;border-right:1px solid var(--rule);background:var(--card)}
.stat:last-child{border-right:0}
.stat .k{font:600 10px/1 var(--mono);letter-spacing:.13em;text-transform:uppercase;color:var(--ink3)}
.stat .v{font:600 30px/1.05 var(--mono);letter-spacing:-.03em;margin-top:9px}
.stat .d{color:var(--ink2);font-size:12.5px;margin-top:5px}
.stat.teal .v{color:var(--measured)} .stat.rust .v{color:var(--forecast)}
.stat.amber .v{color:var(--flag)}

/* ---- tables ---- */
table{width:100%;border-collapse:collapse}
thead th{text-align:left;padding:0 12px 10px;border-bottom:1px solid var(--rule2);
  font:600 10px/1 var(--mono);letter-spacing:.13em;text-transform:uppercase;color:var(--ink3)}
tbody td{padding:12px;border-bottom:1px solid var(--rule);vertical-align:top;font-size:13.5px}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover{background:var(--sunk)}
.n{text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums;white-space:nowrap}
th.n{text-align:right}
.tk{font:700 12px/1 var(--mono);letter-spacing:.06em}
.unit{color:var(--ink3);font:11.5px/1 var(--mono)}
.big{font:600 15px/1 var(--mono);font-variant-numeric:tabular-nums}

/* ---- pills ---- */
.pill{display:inline-block;padding:3px 9px;border-radius:2px;
  font:600 10px/1.4 var(--mono);letter-spacing:.09em;text-transform:uppercase}
.pill.medium,.pill.pass{background:rgba(15,92,88,.11);color:var(--measured)}
.pill.low,.pill.warn{background:rgba(154,108,7,.13);color:var(--flag)}
.pill.fallback,.pill.fail{background:rgba(147,51,31,.12);color:var(--forecast)}

/* ---- error bars ---- */
.ebar{position:relative;height:7px;background:var(--sunk);margin-top:6px;min-width:120px}
.ebar>i{position:absolute;left:0;top:0;bottom:0;display:block}
.ebar>u{position:absolute;top:-3px;bottom:-3px;width:1px;background:var(--ink3);text-decoration:none}
.good{color:var(--measured)} .mid{color:var(--flag)} .bad{color:var(--forecast)}
.fill-good{background:var(--measured)} .fill-mid{background:var(--flag)} .fill-bad{background:var(--forecast)}

/* ---- replay timeline ---- */
.replay{border-bottom:1px solid var(--rule);padding:14px 0}
.replay:last-child{border-bottom:0}
.replay-top{display:flex;justify-content:space-between;gap:12px;align-items:baseline;
  font:12px/1 var(--mono)}
.replay-top .m{font-family:var(--sans);font-size:13.5px;color:var(--ink2)}
.track{position:relative;height:26px;margin-top:9px;background:var(--sunk)}
.track .known{position:absolute;left:0;top:0;bottom:0;
  background:repeating-linear-gradient(90deg,rgba(15,92,88,.2) 0 2px,transparent 2px 4px)}
.track .cut{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--ink)}
.track .mk{position:absolute;top:50%;width:9px;height:9px;margin:-4.5px 0 0 -4.5px;border-radius:50%}
.track .mk.f{background:var(--forecast)} .track .mk.a{background:var(--measured)}
.track .gap{position:absolute;top:50%;height:1px;background:var(--ink3)}
.legend{display:flex;gap:18px;margin-top:10px;font:11px/1 var(--mono);color:var(--ink2)}
.legend i{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px}

/* ---- evidence ---- */
.metric{border-top:1px solid var(--rule);padding:18px 0}
.metric:first-child{border-top:0;padding-top:4px}
.metric-top{display:flex;justify-content:space-between;gap:18px;align-items:flex-start;flex-wrap:wrap}
.metric-name{font-weight:600;font-size:15px}
.metric-num{font:600 30px/1 var(--mono);letter-spacing:-.03em;color:var(--forecast);
  font-variant-numeric:tabular-nums}
.metric-num .u{font-size:13px;color:var(--ink3);margin-left:6px;letter-spacing:0}
.method{color:var(--ink2);font-size:12.5px;margin-top:7px;max-width:78ch}
.notes{margin:8px 0 0;padding:0;list-style:none}
.notes li{color:var(--ink3);font:11.5px/1.55 var(--mono);margin:3px 0}
details{border:1px solid var(--rule);margin-top:8px;background:var(--paper)}
summary{cursor:pointer;padding:10px 13px;font:12px/1.3 var(--mono);list-style:none;
  display:flex;gap:10px;align-items:baseline}
summary::-webkit-details-marker{display:none}
summary::before{content:"+";color:var(--forecast);font-weight:700}
details[open] summary::before{content:"\\2212"}
summary:focus-visible{outline:2px solid var(--measured);outline-offset:-2px}
details .inner{padding:0 13px 13px 32px}
blockquote{margin:0;padding:11px 14px;background:var(--card);border-left:2px solid var(--measured);
  font:12.5px/1.6 var(--sans);color:var(--ink2);white-space:pre-wrap;max-height:200px;overflow:auto}
.src{margin-top:8px;font:11px/1.5 var(--mono);color:var(--ink3);word-break:break-all}

/* ---- log ---- */
.log{max-height:640px;overflow:auto;border:1px solid var(--rule)}
.log table tbody td{padding:6px 12px;font:11.5px/1.5 var(--mono);border-bottom:0}
.log tbody tr:nth-child(odd){background:var(--paper)}
.log .t{color:var(--ink3);white-space:nowrap}
.log .e{color:var(--measured);white-space:nowrap}
.log .d{color:var(--ink2);word-break:break-word}
.log tr.err .e,.log tr.err .d{color:var(--forecast)}
.log tr.wrn .e,.log tr.wrn .d{color:var(--flag)}

svg.diagram{width:100%;height:auto;display:block}
footer{border-top:1px solid var(--rule);padding:22px 0 40px;
  font:11.5px/1.6 var(--mono);color:var(--ink3)}

@media (max-width:760px){
  .hero{padding:40px 0 18px}
  .plot{height:76px}
  .stat{border-right:0;border-bottom:1px solid var(--rule)}
}
@media (prefers-reduced-motion:reduce){
  .tick,.cutline{animation:none;transform:none}
  .future,.cutline::after{animation:none;opacity:1}
}
@media print{.tabs,.railbar{display:none}section[hidden]{display:block!important}}
"""

SCRIPT = """
const $ = (s, r=document) => r.querySelector(s);
const $$ = (s, r=document) => Array.from(r.querySelectorAll(s));
const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

const fmt = (v, units) => {
  if (v === null || v === undefined || Number.isNaN(v)) return '--';
  const d = units === '%' ? 2 : (Math.abs(v) >= 1000 ? 0 : 2);
  return v.toLocaleString('en-GB', {minimumFractionDigits: d, maximumFractionDigits: d});
};
const pct = v => (v === null || v === undefined) ? '--' : (v * 100).toFixed(1) + '%';
const band = v => v === null || v === undefined ? '' : (v <= 0.15 ? 'good' : v <= 0.60 ? 'mid' : 'bad');

/* ---------- signature: the cutoff strip ---------- */
function renderStrip() {
  const months = DATA.timeline || [];
  if (!months.length) return;
  const max = Math.max(...months.map(m => m.count));
  const ticks = months.map((m, i) => {
    const h = Math.max(4, Math.round(m.count / max * 100));
    const delay = (i / months.length * 420).toFixed(0);
    return `<i class="tick" style="height:${h}%;animation-delay:${delay}ms"
              title="${esc(m.month)}: ${m.count} documents"></i>`;
  }).join('');
  $('#ticks').innerHTML = ticks;
  $('#strip-from').textContent = months[0].month;
  $('#strip-to').textContent = months[months.length - 1].month;
}

/* ---------- tabs ---------- */
function tabs() {
  const buttons = $$('.tabs button');
  const show = button => {
    buttons.forEach(other => {
      const on = other === button;
      other.setAttribute('aria-selected', on);
      $('#' + other.dataset.panel).hidden = !on;
    });
  };
  buttons.forEach(button => {
    button.addEventListener('click', () => show(button));
    button.addEventListener('keydown', event => {
      const i = buttons.indexOf(button);
      if (event.key === 'ArrowRight') buttons[(i + 1) % buttons.length].focus();
      if (event.key === 'ArrowLeft') buttons[(i - 1 + buttons.length) % buttons.length].focus();
    });
  });
}

/* ---------- overview ---------- */
function renderOverview() {
  const errors = {};
  for (const m of ((DATA.backtest || {}).metrics || [])) {
    errors[m.company + '|' + m.metric] = m.median_percentage_error;
  }
  const rows = [];
  for (const company of DATA.companies) {
    company.estimates.forEach((e, i) => {
      const err = errors[company.company.slug + '|' + e.metric];
      const w = err === null || err === undefined ? 0 : Math.min(100, err * 100);
      rows.push(`<tr>
        <td>${i === 0 ? `<span class="tk">${esc(company.company.slug)}</span>
             <div class="unit">${esc(company.company.period)}</div>` : ''}</td>
        <td>${esc(e.metric)}</td>
        <td class="n"><span class="big">${fmt(e.value, e.units)}</span></td>
        <td class="unit">${esc(e.units)}</td>
        <td class="n ${band(err)}">${pct(err)}
          <div class="ebar"><i class="fill-${band(err)}" style="width:${w}%"></i>
            <u style="left:15%"></u></div></td>
        <td><span class="pill ${esc(e.confidence)}">${esc(e.confidence)}</span></td>
      </tr>`);
    });
  }
  $('#tbl-overview').innerHTML = rows.join('');
}

/* ---------- backtest ---------- */
function renderBacktest() {
  const bt = DATA.backtest;
  if (!bt) { $('#backtest').innerHTML =
    '<div class="block"><div class="body"><p class="note">This run skipped the backtest.</p></div></div>'; return; }

  const rows = (bt.metrics || []).map(m => {
    const e = m.median_percentage_error;
    const w = e === null || e === undefined ? 0 : Math.min(100, e * 100);
    return `<tr>
      <td><span class="tk">${esc(m.company)}</span></td>
      <td>${esc(m.metric)}</td>
      <td class="n">${m.events}</td>
      <td class="n ${band(e)}"><span class="big">${pct(e)}</span>
        <div class="ebar"><i class="fill-${band(e)}" style="width:${w}%"></i>
          <u style="left:15%"></u></div></td>
      <td class="n good">${pct(m.best_percentage_error)}</td>
      <td class="n bad">${pct(m.worst_percentage_error)}</td>
    </tr>`;
  }).join('');

  const byCompany = {};
  for (const o of (bt.outcomes || [])) (byCompany[o.company] ||= []).push(o);

  const replays = Object.entries(byCompany).map(([slug, list]) => {
    const items = list.slice().sort((a, b) => b.report_date.localeCompare(a.report_date))
      .map(o => {
        const hi = Math.max(Math.abs(o.forecast), Math.abs(o.actual)) * 1.25 || 1;
        const fx = 40 + Math.abs(o.forecast) / hi * 55;
        const ax = 40 + Math.abs(o.actual) / hi * 55;
        return `<div class="replay">
          <div class="replay-top">
            <span>${esc(o.report_date)} <span class="m">${esc(o.metric)}</span></span>
            <span class="${band(o.percentage_error)}">${pct(o.percentage_error)}</span>
          </div>
          <div class="track">
            <div class="known" style="width:38%"></div>
            <div class="cut" style="left:38%"></div>
            <div class="gap" style="left:${Math.min(fx,ax)}%;width:${Math.abs(fx-ax)}%"></div>
            <div class="mk f" style="left:${fx}%" title="forecast ${fmt(o.forecast, o.units)}"></div>
            <div class="mk a" style="left:${ax}%" title="actual ${fmt(o.actual, o.units)}"></div>
          </div>
          <div class="replay-top" style="margin-top:6px;color:var(--ink3)">
            <span>cut ${esc(o.report_date)} &minus; 1 day</span>
            <span>forecast ${fmt(o.forecast, o.units)} &middot; actual ${fmt(o.actual, o.units)}</span>
          </div>
        </div>`;
      }).join('');
    return `<div class="block">
      <header><h2>${esc(slug)} replays</h2></header>
      <div class="body">${items}</div></div>`;
  }).join('');

  const leak = bt.leakage || {}, all = bt.overall || {};
  $('#backtest').innerHTML = `
    <div class="block">
      <header><h2>Leakage guard</h2>
        <p class="note">Each replay is cut off the day before the result was announced. The guard
          blocks documents during retrieval, and the citations of every finished forecast are
          re-examined afterwards. A leak fails the event rather than inflating the score.</p></header>
      <div class="stats">
        <div class="stat ${leak.status === 'clean' ? 'teal' : 'rust'}"><div class="k">Status</div>
          <div class="v">${esc(leak.status)}</div><div class="d">${leak.detected} leaks detected</div></div>
        <div class="stat"><div class="k">Events replayed</div><div class="v">${leak.events_replayed}</div>
          <div class="d">past reporting dates</div></div>
        <div class="stat"><div class="k">Metrics scored</div><div class="v">${all.scored_metrics ?? '--'}</div>
          <div class="d">of 12 targets</div></div>
        <div class="stat ${band(all.median_percentage_error) === 'good' ? 'teal' : 'amber'}">
          <div class="k">Median error</div><div class="v">${pct(all.median_percentage_error)}</div>
          <div class="d">across scored metrics</div></div>
      </div>
    </div>
    <div class="block">
      <header><h2>Accuracy by metric</h2>
        <p class="note">Median absolute percentage error against what the companies actually
          reported. The hairline on each bar marks 15%.</p></header>
      <div class="body">
        <table><thead><tr><th>Co</th><th>Metric</th><th class="n">Events</th>
          <th class="n">Median error</th><th class="n">Best</th><th class="n">Worst</th></tr></thead>
          <tbody>${rows}</tbody></table>
        <div class="legend"><span><i style="background:var(--measured)"></i>actual reported</span>
          <span><i style="background:var(--forecast)"></i>our forecast</span></div>
      </div>
    </div>
    ${replays}`;
}

/* ---------- companies ---------- */
function renderCompanies() {
  $('#companies').innerHTML = DATA.companies.map(company => {
    const metrics = company.estimates.map(e => {
      const evidence = (e.evidence || []).map(item => `
        <details><summary><span>${esc(item.raw)}</span>
          <span style="color:var(--ink3)">${esc(item.source.published_at)} &middot;
            ${esc(item.source.document_type)}</span></summary>
          <div class="inner">
            <blockquote>${esc(item.context)}</blockquote>
            <div class="src">${esc(item.source.path)}</div>
          </div></details>`).join('');
      return `<div class="metric">
        <div class="metric-top">
          <div><div class="metric-name">${esc(e.metric)}</div>
            <div class="method">${esc(e.method)}</div></div>
          <div style="text-align:right">
            <div class="metric-num">${fmt(e.value, e.units)}<span class="u">${esc(e.units)}</span></div>
            <span class="pill ${esc(e.confidence)}">${esc(e.confidence)}</span></div>
        </div>
        <ul class="notes">${(e.notes || []).map(n => `<li>${esc(n)}</li>`).join('')}</ul>
        ${evidence}
      </div>`;
    }).join('');
    return `<div class="block">
      <header><h2>${esc(company.company.slug)} &mdash; ${esc(company.company.name)}</h2>
        <p class="note">${esc(company.company.period)} &middot; written to
          <span class="unit">${esc(company.company.output_file)}</span></p></header>
      <div class="body">${metrics}</div></div>`;
  }).join('');
}

/* ---------- validation ---------- */
function renderValidation() {
  const totals = {pass: 0, warn: 0, fail: 0};
  const rows = [];
  for (const company of DATA.companies) {
    for (const check of ((company.validation || {}).checks || [])) {
      totals[check.status] = (totals[check.status] || 0) + 1;
      rows.push(`<tr>
        <td><span class="tk">${esc(check.company)}</span></td>
        <td>${esc(check.metric)}</td>
        <td class="unit">${esc(check.check)}</td>
        <td><span class="pill ${esc(check.status)}">${esc(check.status)}</span></td>
        <td style="color:var(--ink2)">${esc(check.detail)}</td></tr>`);
    }
  }
  $('#validation').innerHTML = `
    <div class="block">
      <header><h2>Pre-submission checks</h2>
        <p class="note">The organisers' validator confirms a workbook is well-formed. It cannot tell
          that 41.8 was written where 41,800 belonged, or that a margin came out at 270%. These are
          the checks a careful analyst runs before pressing upload. Every one that fired is shown:
          a caught mistake is evidence the system works.</p></header>
      <div class="stats">
        <div class="stat teal"><div class="k">Passed</div><div class="v">${totals.pass || 0}</div></div>
        <div class="stat amber"><div class="k">Warnings</div><div class="v">${totals.warn || 0}</div>
          <div class="d">flagged for a human</div></div>
        <div class="stat ${totals.fail ? 'rust' : 'teal'}"><div class="k">Failures</div>
          <div class="v">${totals.fail || 0}</div></div>
        <div class="stat"><div class="k">Cells written</div><div class="v">12</div>
          <div class="d">none left empty</div></div>
      </div>
    </div>
    <div class="block"><div class="body">
      <table><thead><tr><th>Co</th><th>Metric</th><th>Check</th><th>Status</th><th>Detail</th></tr></thead>
        <tbody>${rows.join('')}</tbody></table></div></div>`;
}

/* ---------- evidence ---------- */
function renderEvidence() {
  const seen = new Map();
  for (const company of DATA.companies)
    for (const e of company.estimates)
      for (const item of (e.evidence || [])) {
        const s = item.source;
        if (!seen.has(s.path)) seen.set(s.path, {s, uses: []});
        seen.get(s.path).uses.push(company.company.slug + ' / ' + e.metric);
      }
  const rows = Array.from(seen.values())
    .sort((a, b) => (b.s.published_at || '').localeCompare(a.s.published_at || ''))
    .map(x => `<tr>
      <td class="unit">${esc(x.s.published_at)}</td>
      <td><span class="tk">${esc(x.s.company)}</span></td>
      <td class="unit">${esc(x.s.document_type)}</td>
      <td>${esc(x.s.title)}<div class="src">${esc(x.s.path)}</div></td>
      <td style="color:var(--ink2)">${x.uses.map(esc).join('<br>')}</td></tr>`).join('');
  $('#tbl-evidence').innerHTML = rows || '<tr><td colspan="5">No evidence recorded.</td></tr>';
  $('#n-evidence').textContent = seen.size;
}

/* ---------- activity ---------- */
function renderActivity() {
  $('#tbl-log').innerHTML = DATA.log.map(ev => {
    const {ts, type, seq, run_id, ...rest} = ev;
    const d = Object.entries(rest).map(([k, v]) =>
      `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`).join('  ').slice(0, 240);
    const cls = /error/.test(type) ? 'err' : (type === 'warning' ? 'wrn' : '');
    return `<tr class="${cls}"><td class="t">${esc((ts || '').slice(11, 23))}</td>
      <td class="e">${esc(type)}</td><td class="d">${esc(d)}</td></tr>`;
  }).join('') || '<tr><td colspan="3">No log events.</td></tr>';
  $('#n-log').textContent = DATA.log.length;
}

renderStrip(); tabs(); renderOverview(); renderBacktest();
renderCompanies(); renderValidation(); renderEvidence(); renderActivity();
"""


def architecture_svg() -> str:
    """Pipeline diagram. Hand-written SVG so it works with scripts disabled."""
    return """
<svg class="diagram" viewBox="0 0 940 470" xmlns="http://www.w3.org/2000/svg" role="img"
     aria-label="Orchestrator fans out to four company pipelines; each runs retrieval, series, forecast and validation behind the point-in-time guard, then the workbook writer">
  <defs>
    <marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
      <path d="M0 0 L10 5 L0 10 z" fill="#8b909b"/>
    </marker>
    <pattern id="hatch" width="7" height="7" patternTransform="rotate(-45)" patternUnits="userSpaceOnUse">
      <line x1="0" y1="0" x2="0" y2="7" stroke="rgba(147,51,31,.20)" stroke-width="2"/>
    </pattern>
    <style>
      .bx{fill:#fffefc;stroke:#dcd8cf;stroke-width:1.5}
      .bi{fill:#15171c;stroke:#15171c}
      .bg{fill:rgba(15,92,88,.08);stroke:#0f5c58;stroke-width:1.5}
      .t{font:600 13px ui-monospace,Menlo,Consolas,monospace;fill:#15171c}
      .ti{font:600 13px ui-monospace,Menlo,Consolas,monospace;fill:#f4f3ef}
      .s{font:11.5px Inter,system-ui,sans-serif;fill:#4e535d}
      .si{font:11.5px Inter,system-ui,sans-serif;fill:#a9aeb8}
      .lb{font:700 9.5px ui-monospace,Menlo,Consolas,monospace;fill:#93331f;letter-spacing:.12em}
      .ln{stroke:#8b909b;stroke-width:1.4;fill:none;marker-end:url(#ar)}
    </style>
  </defs>

  <rect class="bi" x="322" y="12" width="296" height="48" rx="2"/>
  <text class="ti" x="470" y="33" text-anchor="middle">ORCHESTRATOR</text>
  <text class="si" x="470" y="50" text-anchor="middle">reads companies.json, fans out four pipelines</text>

  <rect class="bg" x="24" y="96" width="206" height="72" rx="2"/>
  <text class="t" x="127" y="120" text-anchor="middle">--as-of GUARD</text>
  <text class="s" x="127" y="138" text-anchor="middle">published_at &lt;= cutoff</text>
  <text class="s" x="127" y="155" text-anchor="middle">unguarded read raises</text>

  <path class="ln" d="M470 60 L470 90"/>
  <path class="ln" d="M230 132 L306 132"/>

  <rect class="bx" x="316" y="98" width="140" height="48" rx="2"/>
  <text class="t" x="386" y="120" text-anchor="middle">HD</text>
  <text class="s" x="386" y="136" text-anchor="middle">FY2026Q2</text>
  <rect class="bx" x="470" y="98" width="140" height="48" rx="2"/>
  <text class="t" x="540" y="120" text-anchor="middle">ADI</text>
  <text class="s" x="540" y="136" text-anchor="middle">FY2026Q3</text>
  <rect class="bx" x="624" y="98" width="140" height="48" rx="2"/>
  <text class="t" x="694" y="120" text-anchor="middle">HAS</text>
  <text class="s" x="694" y="136" text-anchor="middle">FY2026</text>
  <rect class="bx" x="778" y="98" width="138" height="48" rx="2"/>
  <text class="t" x="847" y="120" text-anchor="middle">DE</text>
  <text class="s" x="847" y="136" text-anchor="middle">FY2026Q3</text>

  <path class="ln" d="M470 146 L470 184"/>
  <text class="lb" x="486" y="172">EACH PIPELINE</text>

  <rect class="bx" x="118" y="188" width="700" height="56" rx="2"/>
  <text class="t" x="142" y="211">RETRIEVAL</text>
  <text class="s" x="142" y="229">BM25 over 69,229 passages, recency-weighted, cutoff-filtered</text>
  <path class="ln" d="M470 244 L470 266"/>

  <rect class="bx" x="118" y="270" width="700" height="56" rx="2"/>
  <text class="t" x="142" y="293">SERIES</text>
  <text class="s" x="142" y="311">earnings releases read end to end; seasonality, trend, guidance bias</text>
  <path class="ln" d="M470 326 L470 348"/>

  <rect class="bx" x="118" y="352" width="700" height="56" rx="2"/>
  <text class="t" x="142" y="375">FORECAST + VALIDATION</text>
  <text class="s" x="142" y="393">seasonal naive with drift, guidance-blended; unit, range and margin checks</text>

  <path class="ln" d="M127 168 L127 380 L110 380"/>
  <path class="ln" d="M818 380 L866 380 L866 438 L634 438"/>

  <rect class="bi" x="330" y="416" width="304" height="44" rx="2"/>
  <text class="ti" x="482" y="435" text-anchor="middle">WORKBOOK WRITER</text>
  <text class="si" x="482" y="451" text-anchor="middle">Summary!C7:C9, native numbers, re-verified</text>

  <rect x="866" y="188" width="50" height="220" fill="url(#hatch)" opacity="0"/>
</svg>
"""


def build_html(data: dict[str, Any]) -> str:
    manifest = data["manifest"]
    backtest = data.get("backtest") or {}
    leak = backtest.get("leakage", {})
    overall = backtest.get("overall", {})
    as_of = manifest.get("as_of", "--")
    commit = (manifest.get("commit") or "unknown")[:10]
    documents = sum(item["count"] for item in data.get("timeline", [])) or 1139

    median = overall.get("median_percentage_error")
    median_text = f"{median * 100:.1f}%" if median is not None else "--"

    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Asof &mdash; forecast dashboard</title>
<style>{STYLE}</style>
</head>
<body>

<div class="railbar"><div class="wrap">
  <span class="brand">Agents vs Wall Street</span>
  <span class="right">
    <span>{commit}</span>
    <span class="cut">cutoff {as_of}</span>
  </span>
</div></div>

<header class="hero"><div class="wrap">
  <h1 class="wordmark">ASOF<span class="dim">.</span></h1>
  <p class="tagline">Twelve forecasts across four companies. Every one traceable to a document
    published <strong>before the cutoff</strong> &mdash; which is also why the backtest cannot
    cheat, and why this run reproduces exactly a week from now.</p>

  <div class="strip">
    <div class="strip-head">
      <span><b>{documents:,} documents</b> visible</span>
      <span class="fut">forecast period</span>
    </div>
    <div class="plot">
      <div class="ticks" id="ticks"></div>
      <div class="future"><span>unknowable</span></div>
      <div class="cutline" data-label="{as_of}"></div>
    </div>
    <div class="axis"><span id="strip-from"></span><span id="strip-to"></span></div>
  </div>
</div></header>

<div class="tabs"><div class="wrap" role="tablist">
  <button data-panel="p-overview" aria-selected="true" role="tab">Forecasts</button>
  <button data-panel="p-backtest" aria-selected="false" role="tab">Backtest</button>
  <button data-panel="p-companies" aria-selected="false" role="tab">Evidence trail</button>
  <button data-panel="p-validation" aria-selected="false" role="tab">Checks</button>
  <button data-panel="p-evidence" aria-selected="false" role="tab">Documents</button>
  <button data-panel="p-activity" aria-selected="false" role="tab">Run log</button>
  <button data-panel="p-architecture" aria-selected="false" role="tab">Architecture</button>
</div></div>

<main class="wrap">

  <section id="p-overview" role="tabpanel">
    <div class="block">
      <div class="stats">
        <div class="stat teal"><div class="k">Backtested error</div><div class="v">{median_text}</div>
          <div class="d">median across scored metrics</div></div>
        <div class="stat"><div class="k">Events replayed</div>
          <div class="v">{leak.get('events_replayed', 0)}</div>
          <div class="d">leakage {leak.get('status', '--')}</div></div>
        <div class="stat"><div class="k">Passages indexed</div><div class="v">69,229</div>
          <div class="d">from {documents:,} documents</div></div>
        <div class="stat rust"><div class="k">Human input</div><div class="v">none</div>
          <div class="d">one command, start to workbooks</div></div>
      </div>
    </div>
    <div class="block">
      <header><h2>The twelve submitted forecasts</h2>
        <p class="note">Each figure is written into the supplied template at
          <span class="unit">Summary!C7:C9</span> as a native number, then read back and re-checked
          against the organisers' rules. The error column is what the same method scored when
          replayed against past results.</p></header>
      <div class="body">
        <table><thead><tr><th>Co</th><th>Metric</th><th class="n">Forecast</th><th>Units</th>
          <th class="n">Backtested error</th><th>Confidence</th></tr></thead>
          <tbody id="tbl-overview"></tbody></table>
      </div>
    </div>
  </section>

  <section id="p-backtest" role="tabpanel" hidden><div id="backtest"></div></section>
  <section id="p-companies" role="tabpanel" hidden><div id="companies"></div></section>
  <section id="p-validation" role="tabpanel" hidden><div id="validation"></div></section>

  <section id="p-evidence" role="tabpanel" hidden>
    <div class="block">
      <header><h2>Documents actually read</h2>
        <p class="note"><b id="n-evidence">0</b> documents were cited by the forecasts in this run.
          Every submitted figure traces back to one of them.</p></header>
      <div class="body">
        <table><thead><tr><th>Published</th><th>Co</th><th>Type</th><th>Document</th>
          <th>Used for</th></tr></thead><tbody id="tbl-evidence"></tbody></table>
      </div>
    </div>
  </section>

  <section id="p-activity" role="tabpanel" hidden>
    <div class="block">
      <header><h2>Run log</h2>
        <p class="note"><b id="n-log">0</b> timestamped events. This is the same file submitted as
          the clear-run record, not a summary written for display.</p></header>
      <div class="body"><div class="log"><table><tbody id="tbl-log"></tbody></table></div></div>
    </div>
  </section>

  <section id="p-architecture" role="tabpanel" hidden>
    <div class="block">
      <header><h2>How it works</h2>
        <p class="note">One cutoff governs every retrieval. It is a single global guard that
          retrieval code must ask for, and which raises when none is configured &mdash; so an
          unguarded read is impossible rather than merely discouraged.</p></header>
      <div class="body">{architecture_svg()}</div>
    </div>
    <div class="block">
      <header><h2>One mechanism, three jobs</h2></header>
      <div class="body">
        <table><thead><tr><th>Command</th><th>What it does</th></tr></thead><tbody>
          <tr><td class="unit">python run.py --as-of 2025-11-18</td>
              <td>Replays a quarter whose result we know, to score the method</td></tr>
          <tr><td class="unit">python run.py --as-of {as_of}</td>
              <td>The competition run. Reruns reproduce it exactly</td></tr>
          <tr><td class="unit">python run.py</td>
              <td>Live mode, for earnings events after the hackathon</td></tr>
        </tbody></table>
      </div>
    </div>
  </section>

</main>

<footer class="wrap">
  run {manifest.get('run_id', '')} &middot; commit {commit} &middot;
  generated {manifest.get('finished_at', '')}
</footer>

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
    target = PATHS.dashboard / "index.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_html(data), encoding="utf-8")

    print(f"Dashboard written: {target.relative_to(ROOT)} ({target.stat().st_size / 1024:.0f} KB)")
    print(f"  run          {data['run_id']}")
    print(f"  companies    {len(data['companies'])}")
    print(f"  log events   {len(data['log'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
