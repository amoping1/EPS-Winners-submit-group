#!/usr/bin/env python3
"""Combine the three teams' forecasts and write the ensemble workbooks.

    python scripts/collect_team_forecasts.py
    python scripts/build_ensemble.py [--write]

Without --write it reports only, so the comparison can be read before anything
touches submission/.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import asof  # noqa: E402
from src.asof import AsOfGuard  # noqa: E402
from src.config import PATHS, load_companies  # noqa: E402
from src.corpus import get_index  # noqa: E402
from src.ensemble import build, summarise  # noqa: E402
from src.validate import validate_company  # noqa: E402
from src.workbook import verify_workbook, write_company_workbook  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="Write submission/*.xlsx")
    parser.add_argument("--as-of", default="2026-08-16")
    args = parser.parse_args()

    source = ROOT / "runs" / "team-forecasts.json"
    if not source.exists():
        raise SystemExit("Run scripts/collect_team_forecasts.py first.")

    collected = json.loads(source.read_text(encoding="utf-8"))
    results = build(collected)
    by_key = {(item.company, item.metric): item for item in results}

    print(f"{'CO':<5}{'METRIC':<42}{'NEVA':>11}{'ADRIAN':>11}{'DIMITRIS':>11}"
          f"{'ENSEMBLE':>12}  RULE")
    print("-" * 118)
    for company in load_companies():
        for metric in company.metrics:
            item = by_key.get((company.slug, metric.label))
            if item is None:
                continue
            members = item.members
            cells = [
                f"{members.get(name, float('nan')):>11,.2f}" if name in members else f"{'--':>11}"
                for name in ("neva", "adrian", "dimitris")
            ]
            marker = "*" if item.dropped else " "
            print(
                f"{item.company:<5}{item.metric[:42]:<42}{''.join(cells)}"
                f"{item.value:>12,.2f}{marker} {item.rule[:44]}"
            )
            if item.market_consensus is not None:
                print(f"{'':<5}{'  analyst consensus':<42}{'':>33}"
                      f"{item.market_consensus:>12,.2f}  "
                      f"ensemble sits {item.market_gap:+.1%} from it")

    stats = summarise(results)
    print("\nAgreement:", stats["agreement"])
    print("Outliers discarded, by source:", stats["outliers_discarded_by_source"] or "none")
    print("Median relative spread:", f"{stats['median_spread']:.1%}")

    target = ROOT / "runs" / "ensemble.json"
    target.write_text(
        json.dumps(
            {"summary": stats, "metrics": [item.as_dict() for item in results]},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\nWritten: {target.relative_to(ROOT)}")

    if not args.write:
        print("\nReport only. Re-run with --write to produce the workbooks.")
        return 0

    asof.set_guard(AsOfGuard(date.fromisoformat(args.as_of)))
    index = get_index()
    print("\nWriting workbooks")
    for company in load_companies():
        values = {
            metric.label: by_key[(company.slug, metric.label)].value
            for metric in company.metrics
            if (company.slug, metric.label) in by_key
        }
        if len(values) != len(company.metrics):
            print(f"  {company.slug}: incomplete, skipped")
            continue

        report = validate_company(index, company, values)
        path, _ = write_company_workbook(company, values)
        verify_workbook(company, path)
        flags = len(report.failures) + len(report.warnings)
        print(f"  {company.output_file:<22} written, validation {report.as_dict()['status']}"
              f" ({flags} flag{'s' if flags != 1 else ''})")
        for check in report.failures + report.warnings:
            print(f"      [{check.status}] {check.metric}: {check.detail[:88]}")
    asof.set_guard(None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
