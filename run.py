#!/usr/bin/env python3
"""Agents vs Wall Street - single entry point.

    python run.py --as-of 2025-11-18   backtest replay of a quarter we can score
    python run.py --as-of 2026-08-16   the competition run
    python run.py                      live mode, for reuse after the event

The ``--as-of`` date is the cutoff for every retrieval in the system. See
:mod:`src.asof`.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime

from src.config import PATHS, Settings, find_company, load_companies, load_dotenv
from src.context import create_run_context
from src.errors import ForecastSystemError


def parse_as_of(value: str) -> date:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"--as-of must be an ISO date such as 2026-08-16, got {value!r}"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="Forecast twelve financial metrics across four companies.",
    )
    parser.add_argument(
        "--as-of",
        type=parse_as_of,
        default=None,
        metavar="YYYY-MM-DD",
        help="Point-in-time cutoff. Nothing published after this date is visible. "
        "Defaults to today (live mode).",
    )
    parser.add_argument(
        "--companies",
        default=None,
        metavar="HD,ADI",
        help="Comma-separated subset to run. Defaults to all four.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not echo log events to the console.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv()

    as_of = args.as_of or date.today()
    all_companies = load_companies()
    if args.companies:
        selected = tuple(
            find_company(token, all_companies)
            for token in args.companies.split(",")
            if token.strip()
        )
    else:
        selected = all_companies

    context = create_run_context(
        as_of,
        companies=selected,
        settings=Settings.from_env(),
        echo=not args.quiet,
    )

    try:
        with context.logger.stage("pipeline"):
            for company in context.companies:
                context.logger.event(
                    "company.registered",
                    company=company.slug,
                    period=company.period,
                    output_file=company.output_file,
                    metrics=[m.label for m in company.metrics],
                    corpus_dir=str(company.corpus_dir.relative_to(PATHS.root)),
                )
            # Retrieval, modelling, forecasting and workbook writing are added
            # in build steps 2-7. The guard and the log are live from here on.
            context.logger.warning(
                "Pipeline stages are not implemented yet; no workbooks were written."
            )
    except ForecastSystemError as exc:
        context.logger.error(str(exc), error_type=type(exc).__name__)
        context.write_manifest(status="failed", error=str(exc))
        context.close()
        return 1

    manifest_path = context.write_manifest(status="skeleton")
    context.logger.event(
        "run.end",
        status="skeleton",
        manifest=str(manifest_path.relative_to(PATHS.root)),
        guard=context.guard.describe()["stats"],
    )
    context.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
