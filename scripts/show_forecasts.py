#!/usr/bin/env python3
"""Print the forecasts from a run directory. Development aid, not part of the pipeline."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def latest_run() -> Path:
    runs = sorted((ROOT / "runs").glob("run-*"), key=lambda p: p.stat().st_mtime)
    if not runs:
        raise SystemExit("No runs found.")
    return runs[-1]


def main() -> int:
    run_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else latest_run()
    print(f"Run: {run_dir.name}\n")
    for path in sorted(run_dir.glob("*/baseline.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        company = payload["company"]
        print("=" * 78)
        print(f"{company['slug']}  {company['name']}  {company['period']}")
        for estimate in payload["estimates"]:
            print(
                f"  {estimate['value']:>14,.2f}  {estimate['units']:<12}"
                f"  {estimate['confidence']:<9}  {estimate['metric']}"
            )
            for note in estimate["notes"]:
                print(f"       - {note}")
            for item in estimate["evidence"][:2]:
                source = item["source"]
                print(
                    f"       > {item['raw']:<14} {source['published_at']} "
                    f"{Path(source['path']).name[:44]}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
