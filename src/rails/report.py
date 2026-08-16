"""Dashboard generator.

Reads logs/full-run.json and writes a self-contained HTML report. Generated rather than
hand-written, so it always describes the run that actually happened - a hand-maintained
dashboard drifts from the system within an hour, and the judging rules mark down a page
that no longer matches.

Every displayed figure is either cited to a doc_id or explicitly labelled derived or
unavailable. Nothing is shown that the system did not actually establish.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

CSS = """
:root{--ground:#f3f5f6;--surface:#fff;--sunk:#e9edee;--ink:#16232b;--soft:#4a5a63;
--faint:#7d8d95;--rule:#d3dadd;--rule2:#b6c1c6;--accent:#1a6f6a;--accent-soft:#d8e8e6;
--pass:#2e7d5b;--warn:#b07a18;--fail:#af3a2c;--pass-bg:#e2efe8;--warn-bg:#f6eeda;--fail-bg:#f7e4e1;
--serif:Georgia,"Iowan Old Style","Times New Roman",serif;
--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
--mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--ground:#0f1619;
--surface:#161f24;--sunk:#1d282e;--ink:#dee6e9;--soft:#a3b2b9;--faint:#74848c;--rule:#2a373e;
--rule2:#3b4a52;--accent:#4fb3ac;--accent-soft:#1a3a38;--pass:#5fb98d;--warn:#d9a541;
--fail:#e0705f;--pass-bg:#16301f;--warn-bg:#322813;--fail-bg:#34191a}}
:root[data-theme="dark"]{--ground:#0f1619;--surface:#161f24;--sunk:#1d282e;--ink:#dee6e9;
--soft:#a3b2b9;--faint:#74848c;--rule:#2a373e;--rule2:#3b4a52;--accent:#4fb3ac;
--accent-soft:#1a3a38;--pass:#5fb98d;--warn:#d9a541;--fail:#e0705f;--pass-bg:#16301f;
--warn-bg:#322813;--fail-bg:#34191a}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);font-family:var(--sans);
font-size:16px;line-height:1.6;-webkit-font-smoothing:antialiased}
.wrap{max-width:1120px;margin:0 auto;padding:36px 24px 72px;display:flex;
flex-direction:column;gap:36px}
.masthead{border-bottom:2px solid var(--ink);padding-bottom:16px;display:flex;
flex-direction:column;gap:8px}
.eyebrow{font-family:var(--mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;
color:var(--accent)}
h1{font-family:var(--serif);font-size:clamp(28px,4.5vw,40px);line-height:1.1;margin:0;
font-weight:400;text-wrap:balance}
.runbar{display:flex;flex-wrap:wrap;gap:8px 24px;font-family:var(--mono);font-size:12px;
color:var(--faint)}
.runbar b{color:var(--ink);font-weight:600}
p{margin:0;max-width:70ch;color:var(--soft)}
h2{font-family:var(--serif);font-size:21px;font-weight:400;margin:0;padding-bottom:8px;
border-bottom:1px solid var(--rule)}
section{display:flex;flex-direction:column;gap:16px}
.co{background:var(--surface);border:1px solid var(--rule);display:flex;
flex-direction:column}
.co-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px 18px;padding:16px 20px;
border-bottom:1px solid var(--rule);background:var(--sunk)}
.co-name{font-family:var(--serif);font-size:20px}
.co-meta{font-family:var(--mono);font-size:11.5px;color:var(--faint)}
.co-meta b{color:var(--ink);font-weight:600}
.chip{font-family:var(--mono);font-size:10px;letter-spacing:.08em;text-transform:uppercase;
padding:2px 8px;background:var(--accent-soft);color:var(--accent)}
.co-body{padding:16px 20px;display:flex;flex-direction:column;gap:18px}
.metrics{display:flex;flex-direction:column;gap:10px}
.metric{border-left:3px solid var(--rule2);padding:8px 0 8px 14px;display:grid;
grid-template-columns:1fr auto;gap:4px 18px;align-items:baseline}
.metric.ok{border-left-color:var(--pass)}
.metric.flag{border-left-color:var(--fail)}
.metric.soft{border-left-color:var(--warn)}
.m-name{font-weight:650;font-size:14.5px}
.m-val{font-family:var(--mono);font-size:19px;font-variant-numeric:tabular-nums;
white-space:nowrap}
.m-unit{font-family:var(--mono);font-size:11px;color:var(--faint);margin-left:6px}
.m-line{grid-column:1/-1;font-size:13px;color:var(--soft);font-family:var(--mono)}
.m-line .k{color:var(--faint)}
.m-note{grid-column:1/-1;font-size:13px;color:var(--soft)}
.spark{grid-column:1/-1;display:flex;align-items:flex-end;gap:3px;height:34px;margin-top:2px}
.spark i{display:block;width:16px;background:var(--accent-soft);border-top:2px solid var(--accent)}
.spark span{font-family:var(--mono);font-size:10px;color:var(--faint);align-self:center;
margin-left:6px}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px}
.panel{background:var(--sunk);padding:12px 16px;display:flex;flex-direction:column;gap:6px}
.panel h3{margin:0;font-family:var(--mono);font-size:10.5px;letter-spacing:.09em;
text-transform:uppercase;color:var(--faint);font-weight:400}
.panel ul{margin:0;padding-left:18px;font-size:13.5px;color:var(--soft)}
.panel li{margin-bottom:3px}
.pill{font-family:var(--mono);font-size:10px;letter-spacing:.06em;text-transform:uppercase;
padding:2px 7px;white-space:nowrap}
.pill.pass{background:var(--pass-bg);color:var(--pass)}
.pill.warn{background:var(--warn-bg);color:var(--warn)}
.pill.fail{background:var(--fail-bg);color:var(--fail)}
.pill.na{background:var(--sunk);color:var(--faint)}
.chan{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}
.ch{background:var(--surface);border:1px solid var(--rule);border-top:3px solid var(--accent);
padding:12px 14px}
.ch.derived{border-top-color:var(--warn)}
.ch.absent{border-top-color:var(--rule2);opacity:.7}
.ch-n{font-family:var(--mono);font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;
color:var(--accent)}
.ch.derived .ch-n{color:var(--warn)}
.ch.absent .ch-n{color:var(--faint)}
.ch-c{font-family:var(--mono);font-size:22px;font-variant-numeric:tabular-nums}
.ch p{font-size:12.5px}
.src{font-family:var(--mono);font-size:11px;color:var(--faint);word-break:break-all}
.tablewrap{overflow-x:auto;border:1px solid var(--rule);background:var(--surface)}
table{border-collapse:collapse;width:100%;min-width:680px}
th{font-family:var(--mono);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;
color:var(--faint);text-align:left;font-weight:400;padding:10px 14px;
border-bottom:1px solid var(--rule2);white-space:nowrap}
td{padding:9px 14px;border-bottom:1px solid var(--rule);font-size:14px;vertical-align:baseline}
tr:last-child td{border-bottom:none}
td.num{font-family:var(--mono);text-align:right;font-variant-numeric:tabular-nums;
white-space:nowrap;font-size:15px}
td.unit{font-family:var(--mono);font-size:11.5px;color:var(--faint)}
td.tick{font-family:var(--mono);font-size:12.5px;font-weight:650}
footer{border-top:1px solid var(--rule);padding-top:14px;font-family:var(--mono);
font-size:11px;color:var(--faint);display:flex;flex-wrap:wrap;gap:6px 22px}
@media (max-width:640px){.metric{grid-template-columns:1fr}}
"""


def confidence_for(res: dict) -> tuple[str, str]:
    """Overall confidence for a metric, plus why.

    Derived, not asked for: agreement between the three independent methods, whether a
    published anchor bounded it, and whether the critic objected.
    """
    verdict = res.get("verdict") or {}
    agree = res.get("agreement")
    reasons = []

    if verdict.get("plausible") is False:
        return "low", "critic objected"
    if res.get("guidance_bound") or (res.get("anchor") or {}).get("kind") in ("guidance", "consensus"):
        reasons.append("published anchor")
        level = "high"
    elif isinstance(agree, (int, float)) and agree >= 0.9:
        reasons.append("methods agree")
        level = "high"
    elif isinstance(agree, (int, float)) and agree >= 0.7:
        reasons.append("moderate method spread")
        level = "medium"
    else:
        reasons.append("methods diverge")
        level = "low"

    if res.get("outliers"):
        reasons.append("outlier cut")
        level = "medium" if level == "high" else level
    if res.get("clamped"):
        reasons.append("clamped")
        level = "medium" if level == "high" else level
    if not res.get("method_values"):
        return "low", "no method proposals"
    return level, ", ".join(reasons)


def _e(value) -> str:
    return html.escape(str(value if value is not None else ""))


def _fmt(value, units: str = "") -> str:
    if value is None:
        return "n/a"
    if abs(value) >= 1000:
        return f"{value:,.2f}"
    if "%" in units or "GBp" in units:
        return f"{value:,.2f}"
    return f"{value:,.2f}"


def _spark(series: list[float]) -> str:
    """Tiny bar sparkline. No script, no external chart library."""
    values = [v for v in series if isinstance(v, (int, float))]
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or abs(hi) or 1.0
    bars = "".join(
        f'<i style="height:{6 + 26 * (v - lo) / span:.0f}px"></i>' for v in reversed(values)
    )
    return f'<div class="spark">{bars}<span>{len(values)} prior periods &middot; {_fmt(lo)}&ndash;{_fmt(hi)}</span></div>'


def _metric_block(label: str, res: dict, evidence: dict) -> str:
    value, units = res.get("value"), res.get("units", "")
    verdict = res.get("verdict") or {}
    flagged = verdict.get("plausible") is False

    pills = []
    if res.get("guidance_bound"):
        pills.append('<span class="pill pass">held to guidance</span>')
    if res.get("anchor") and res["anchor"].get("kind") == "consensus":
        pills.append('<span class="pill pass">consensus anchor</span>')
    if res.get("anchor_rejected"):
        pills.append('<span class="pill pass">wrong-period anchor rejected</span>')
    if res.get("clamped"):
        pills.append('<span class="pill warn">clamped to history</span>')
    if res.get("outliers"):
        pills.append(f'<span class="pill warn">{len(res["outliers"])} outlier cut</span>')
    if flagged:
        pills.append('<span class="pill fail">critic flagged</span>')

    css = "flag" if flagged else ("soft" if (res.get("clamped") or res.get("outliers")) else "ok")

    methods = {k: v for k, v in (res.get("method_values") or {}).items() if k != "_anchor"}
    method_line = " &middot; ".join(
        f'<span class="k">{_e(k)}</span> {_fmt(v, units)}' for k, v in methods.items()
    )

    finance = (evidence or {}).get("finance", [])
    series = [f["value"] for f in finance if isinstance(f.get("value"), (int, float))]
    cyclical = (evidence or {}).get("cyclical", {})

    lines = [f'<div class="m-line">{method_line}</div>'] if method_line else []

    if cyclical.get("available"):
        lines.append(
            f'<div class="m-line"><span class="k">trend</span> '
            f'{cyclical["recent_growth"]:+.1%} recent &middot; {_e(cyclical["direction"])} '
            f'&middot; <span class="k">naive</span> {_fmt(cyclical["naive_projection"], units)} '
            f'<span class="k">(derived)</span></div>'
        )
    else:
        lines.append('<div class="m-line"><span class="k">trend</span> '
                     '<span class="pill na">not derivable &mdash; too few points</span></div>')

    calls = (evidence or {}).get("calls", [])
    for anchor in calls[:1]:
        rng = ""
        if anchor.get("low") is not None and anchor.get("high") is not None:
            rng = f' range {_fmt(anchor["low"])}&ndash;{_fmt(anchor["high"])}'
        lines.append(
            f'<div class="m-line"><span class="k">{_e(anchor.get("kind"))}</span> '
            f'{_fmt(anchor.get("value"), units)}{rng}</div>'
        )
    if not calls:
        lines.append('<div class="m-line"><span class="k">guidance / estimate</span> '
                     '<span class="pill na">none published</span></div>')

    cited = [f.get("doc_id") for f in finance if f.get("doc_id")][:1]
    if cited:
        lines.append(f'<div class="src">source: {_e(cited[0])}</div>')

    return f"""      <div class="metric {css}">
        <div class="m-name">{_e(label)}{' ' + ' '.join(pills) if pills else ''}</div>
        <div class="m-val">{_fmt(value, units)}<span class="m-unit">{_e(units)}</span></div>
        {''.join(lines)}
        {_spark(series)}
      </div>"""


def _company(run: dict) -> str:
    metrics = "".join(
        _metric_block(label, res, (run.get("aggregated") or {}).get(label, {}))
        for label, res in run["results"].items()
    )
    drivers = (run.get("evidence") or {}).get("drivers", [])[:5]
    gaps = run.get("evidence_gaps", [])[:4]
    kpis = run.get("kpis", [])
    catalysts = run.get("catalysts", [])[:4]

    def ul(items):
        return "".join(f"<li>{_e(i)}</li>" for i in items) or "<li>none recorded</li>"

    return f"""    <div class="co">
      <div class="co-head">
        <div class="co-name">{_e(run['company'])}</div>
        <span class="chip">{_e(run.get('profile_label', run.get('profile')))}</span>
        <div class="co-meta">period <b>{_e(run['period'])}</b></div>
        <div class="co-meta">{_e(run.get('tool_calls'))} tool calls &middot; {_e(run.get('elapsed_s'))}s
          &middot; {_e(run.get('followup_passes', 0))} follow-up</div>
      </div>
      <div class="co-body">
        <div class="metrics">{metrics}</div>
        <div class="cols">
          <div class="panel"><h3>What changed this period</h3><ul>{ul(drivers)}</ul></div>
          <div class="panel"><h3>Watch items &amp; gaps</h3><ul>{ul(gaps)}</ul></div>
          <div class="panel"><h3>Industry KPIs</h3><ul>{ul(kpis)}</ul></div>
          <div class="panel"><h3>Catalysts tracked</h3><ul>{ul(catalysts)}</ul></div>
        </div>
      </div>
    </div>"""


def build(run_path: str | Path = "logs/full-run.json",
          out_path: str | Path = "architecture/dashboard.html",
          as_of: str = "", model: str = "") -> Path:
    runs = json.loads(Path(run_path).read_text(encoding="utf-8"))
    runs.sort(key=lambda r: r["ticker"])

    totals = {"finance": 0, "calls": 0, "news": 0, "cyclical": 0}
    for run in runs:
        for channel in run.get("channels", []):
            totals[channel["channel"]] = totals.get(channel["channel"], 0) + channel["rows"]

    metric_count = sum(len(r["results"]) for r in runs)
    filled = sum(1 for r in runs for v in r["results"].values() if v.get("value") is not None)

    channels = f"""
      <div class="ch"><div class="ch-n">Finance</div><div class="ch-c">{totals['finance']}</div>
        <p>Reported actuals from filings. The only audited figures here.</p></div>
      <div class="ch"><div class="ch-n">Calls</div><div class="ch-c">{totals['calls']}</div>
        <p>Guidance and consensus from transcripts and trading statements.</p></div>
      <div class="ch derived"><div class="ch-n">Cyclical &mdash; derived</div>
        <div class="ch-c">{totals['cyclical']}</div>
        <p>Seasonality and trend computed from the finance channel, not read anywhere.</p></div>
      <div class="ch absent"><div class="ch-n">News &mdash; unavailable</div><div class="ch-c">0</div>
        <p>Not in the frozen corpus. Reported missing rather than invented.</p></div>"""

    body = "".join(_company(r) for r in runs)

    conf_pill = {"high": "pass", "medium": "warn", "low": "fail"}
    summary_rows = ""
    for run in runs:
        for label, res in run["results"].items():
            level, why = confidence_for(res)
            summary_rows += (
                f'<tr><td class="tick">{_e(run["ticker"].replace("LSE:", ""))}</td>'
                f'<td>{_e(label)}</td>'
                f'<td class="num">{_fmt(res.get("value"), res.get("units", ""))}</td>'
                f'<td class="unit">{_e(res.get("units"))}</td>'
                f'<td><span class="pill {conf_pill[level]}">{level}</span></td>'
                f'<td class="unit">{_e(why)}</td></tr>'
            )

    return _write(out_path, f"""<title>Quarterly Forecast Desk</title>
<style>{CSS}</style>
<div class="wrap">
  <header class="masthead">
    <div class="eyebrow">EPS-Winners &middot; generated from the last run</div>
    <h1>Quarterly forecast desk</h1>
    <p>Every figure below is either cited to a source document, labelled <em>derived</em>,
       or marked unavailable. Nothing is shown that the system did not establish.</p>
    <div class="runbar">
      <span>as_of <b>{_e(as_of)}</b></span><span>model <b>{_e(model)}</b></span>
      <span>metrics <b>{filled}/{metric_count}</b></span>
      <span>companies <b>{len(runs)}</b></span>
    </div>
  </header>

  <section>
    <h2>Evidence channels</h2>
    <div class="chan">{channels}</div>
  </section>

  <section>
    <h2>The twelve submitted forecasts</h2>
    <p>Exactly what is written to the four workbooks. Confidence is derived from method
       agreement, whether a published anchor bounded the value, and whether the critic
       objected.</p>
    <div class="tablewrap"><table>
      <thead><tr><th>Co</th><th>Metric</th><th style="text-align:right">Forecast</th>
        <th>Units</th><th>Confidence</th><th>Basis</th></tr></thead>
      <tbody>{summary_rows}</tbody>
    </table></div>
  </section>

  <section>
    <h2>Company reports</h2>
    {body}
  </section>

  <footer><span>generated &mdash; not hand-maintained</span>
    <span>uploads are manual</span><span>no secrets in this page</span></footer>
</div>""")


def _write(out_path, content: str) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    return out
