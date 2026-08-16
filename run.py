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

from src.agent import improve_company, weak_metrics
from src.backtest import run_backtest
from src.baseline import estimate_company
from src.config import PATHS, Company, Settings, find_company, load_companies, load_dotenv
from src.context import RunContext, create_run_context
from src.corpus import get_index
from src.errors import ForecastSystemError
from src.llm import LLM
from src.validate import validate_company
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
        "--no-agent",
        action="store_true",
        help="Skip the reasoning agent and submit the deterministic forecasts.",
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
            use_agent=not args.no_agent,
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


def forecast_company(
    context: RunContext,
    company: Company,
    *,
    llm: LLM | None = None,
    targets: set[tuple[str, str]] | None = None,
) -> dict:
    """Produce and write one company's three forecasts."""
    logger = context.logger
    with logger.stage("company", company=company.slug) as state:
        index = get_index()

        estimates = estimate_company(index, company)

        # The model is asked only about metrics the backtest says the
        # deterministic path handles badly. Replacing a measured method with an
        # unmeasured one is a bad trade, however capable the model.
        proposals: list = []
        if llm is not None and targets:
            with logger.stage("agent", company=company.slug) as agent_state:
                proposals = improve_company(
                    llm, index, company, estimates, targets, logger=logger
                )
                agent_state["proposed"] = len(proposals)
                agent_state["accepted"] = sum(1 for p in proposals if p.accepted)
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

        # Validate before writing. The organisers' check confirms a workbook is
        # well-formed; it cannot tell that 41.8 was written where 41,800 belonged.
        validation = validate_company(index, company, values)
        for check in validation.failures + validation.warnings:
            logger.event(
                "validation.flag",
                company=company.slug,
                metric=check.metric,
                check=check.name,
                status=check.status,
                detail=check.detail,
            )

        path, written = write_company_workbook(company, values)
        verified = verify_workbook(company, path)

        state["metrics"] = len(written)
        state["workbook"] = str(path.relative_to(PATHS.root))
        state["validation"] = validation.as_dict()["status"]
        state["validation_flags"] = len(validation.failures) + len(validation.warnings)

        context.write_artifact(
            f"{company.slug}/baseline.json",
            {
                "company": company.describe(),
                "as_of": context.as_of.isoformat(),
                "estimates": [estimate.as_dict() for estimate in estimates.values()],
                "written_cells": [cell.as_dict() for cell in written],
                "validation": validation.as_dict(),
                "agent": [proposal.as_dict() for proposal in proposals],
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
    use_agent: bool = True,
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

        # The backtest runs first because it decides where model budget is spent.
        # It costs a few seconds and needs no credentials.
        report: dict = {}
        targets: set[tuple[str, str]] = set()
        if backtest:
            try:
                with logger.stage("backtest") as state:
                    events = backtest_events or context.settings.backtest_events
                    report = run_backtest(
                        index, context.companies, events_per_company=events, logger=logger
                    )
                    context.write_artifact("backtest.json", report)
                    targets = weak_metrics(report)
                    state["events"] = report["leakage"]["events_replayed"]
                    state["leakage"] = report["leakage"]["status"]
                    state["median_percentage_error"] = report["overall"][
                        "median_percentage_error"
                    ]
                    state["weak_metrics"] = len(targets)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"backtest failed: {exc}", error_type=type(exc).__name__)

        llm: LLM | None = None
        if use_agent:
            candidate = LLM(context.settings, logger=logger)
            if candidate.enabled and targets:
                llm = candidate
                logger.event(
                    "agent.enabled",
                    providers=candidate.providers(),
                    model_fast=context.settings.model_fast,
                    model_reasoning=context.settings.model_reasoning,
                    targets=sorted(f"{c}/{m}" for c, m in targets),
                )
            else:
                logger.info(
                    "reasoning agent not used",
                    credentials=candidate.enabled,
                    weak_metrics=len(targets),
                )

        for company in context.companies:
            try:
                results.append(
                    forecast_company(context, company, llm=llm, targets=targets)
                )
            except Exception as exc:  # noqa: BLE001 - one company must not sink the rest
                logger.error(
                    f"{company.slug} failed: {exc}",
                    company=company.slug,
                    error_type=type(exc).__name__,
                )

        if llm is not None:
            context.write_artifact("llm-usage.json", llm.usage.as_dict())
            logger.event("agent.usage", **llm.usage.as_dict())

    return results


if __name__ == "__main__":
    sys.exit(main())


