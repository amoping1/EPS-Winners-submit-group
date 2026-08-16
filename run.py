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

from src.backtest import run_backtest
from src.baseline import estimate_company
from src.config import PATHS, Company, Settings, find_company, load_companies, load_dotenv
from src.context import RunContext, create_run_context
from src.corpus import get_index
from src.errors import ForecastSystemError
from src.workbook import verify_workbook, write_company_workbook


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
    parser.add_argument(
        "--no-backtest",
        action="store_true",
        help="Skip the historical replay that calibrates and scores the system.",
    )
    parser.add_argument(
        "--backtest-events",
        type=int,
        default=None,
        metavar="N",
        help="Reporting events to replay per company. Defaults to BACKTEST_EVENTS or 8.",
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
        results = run_pipeline(
            context,
            backtest=not args.no_backtest,
            backtest_events=args.backtest_events,
        )
    except ForecastSystemError as exc:
        context.logger.error(str(exc), error_type=type(exc).__name__)
        context.write_manifest(status="failed", error=str(exc))
        context.close()
        return 1

    manifest_path = context.write_manifest(status="baseline", forecasts=results)
    context.logger.event(
        "run.end",
        status="baseline",
        manifest=str(manifest_path.relative_to(PATHS.root)),
        guard=context.guard.describe()["stats"],
        workbooks=len(results),
    )
    context.close()
    return 0


def forecast_company(context: RunContext, company: Company) -> dict:
    """Produce and write one company's three forecasts."""
    logger = context.logger
    with logger.stage("company", company=company.slug) as state:
        index = get_index()

        estimates = estimate_company(index, company)
        for estimate in estimates.values():
            logger.event(
                "forecast.metric",
                company=company.slug,
                metric=estimate.metric,
                units=estimate.units,
                value=estimate.value,
                confidence=estimate.confidence,
                method=estimate.method,
                evidence=len(estimate.candidates),
            )

        values = {label: estimate.value for label, estimate in estimates.items()}
        path, written = write_company_workbook(company, values)
        verified = verify_workbook(company, path)

        state["metrics"] = len(written)
        state["workbook"] = str(path.relative_to(PATHS.root))

        context.write_artifact(
            f"{company.slug}/baseline.json",
            {
                "company": company.describe(),
                "as_of": context.as_of.isoformat(),
                "estimates": [estimate.as_dict() for estimate in estimates.values()],
                "written_cells": [cell.as_dict() for cell in written],
            },
        )
        return {
            "company": company.slug,
            "workbook": str(path.relative_to(PATHS.root)),
            "cells": [cell.as_dict() for cell in verified],
        }


def run_pipeline(
    context: RunContext,
    *,
    backtest: bool = True,
    backtest_events: int | None = None,
) -> list[dict]:
    """Run every company and produce the four workbooks.

    Companies are independent, so a failure in one must not cost the other three
    their output.
    """
    logger = context.logger
    results: list[dict] = []

    with logger.stage("pipeline", companies=len(context.companies)):
        with logger.stage("corpus.index") as state:
            index = get_index()
            state.update(index.stats())

        for company in context.companies:
            try:
                results.append(forecast_company(context, company))
            except Exception as exc:  # noqa: BLE001 - one company must not sink the rest
                logger.error(
                    f"{company.slug} failed: {exc}",
                    company=company.slug,
                    error_type=type(exc).__name__,
                )

        if backtest:
            # Runs after the workbooks exist, so a backtest failure can never
            # cost us a submission.
            try:
                with logger.stage("backtest") as state:
                    events = backtest_events or context.settings.backtest_events
                    report = run_backtest(
                        index,
                        context.companies,
                        events_per_company=events,
                        logger=logger,
                    )
                    context.write_artifact("backtest.json", report)
                    state["events"] = report["leakage"]["events_replayed"]
                    state["leakage"] = report["leakage"]["status"]
                    state["median_percentage_error"] = report["overall"][
                        "median_percentage_error"
                    ]
            except Exception as exc:  # noqa: BLE001
                logger.error(f"backtest failed: {exc}", error_type=type(exc).__name__)

    return results


if __name__ == "__main__":
    sys.exit(main())

