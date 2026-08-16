"""The run context: everything a pipeline stage needs, assembled once.

Constructing a :class:`RunContext` is what activates the point-in-time guard, so
no retrieval can happen before the cutoff is decided.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from . import asof
from .asof import AsOfGuard
from .config import PATHS, Company, Paths, Settings, load_companies
from .runlog import Redactor, RunLogger, utc_now_iso


def make_run_id(as_of: date, *, prefix: str = "run") -> str:
    """Readable, sortable, unique enough for a one-day competition."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{as_of.isoformat()}-{stamp}"


def git_commit(short: bool = False) -> str | None:
    """Current commit hash, recorded so a run can be tied to exact source."""
    args = ["git", "rev-parse", "--short" if short else "HEAD"]
    try:
        result = subprocess.run(
            args,
            cwd=PATHS.root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value or None


@dataclass
class RunContext:
    """Shared state for one execution of the pipeline."""

    run_id: str
    as_of: date
    companies: tuple[Company, ...]
    settings: Settings
    guard: AsOfGuard
    logger: RunLogger
    run_dir: Path
    log_path: Path
    paths: Paths = field(default=PATHS)
    started_at: str = field(default_factory=utc_now_iso)
    commit: str | None = field(default_factory=lambda: git_commit())

    # -- artifact helpers --------------------------------------------------

    def company_dir(self, company: Company) -> Path:
        directory = self.run_dir / company.slug
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def write_artifact(self, relative_path: str, payload: Any) -> Path:
        """Write one JSON artifact under this run's directory."""
        target = self.run_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        # Redact the structure, not the serialised text: scrubbing the finished
        # JSON string would apply the log-field length cap to the whole file and
        # leave the dashboard reading truncated, unparseable artifacts.
        safe = self.logger.redactor.scrub(payload, truncate=False)
        text = json.dumps(safe, indent=2, ensure_ascii=False, default=str)
        target.write_text(text, encoding="utf-8")
        return target

    def manifest(self, **extra: Any) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "as_of": self.as_of.isoformat(),
            "started_at": self.started_at,
            "finished_at": utc_now_iso(),
            "commit": self.commit,
            "companies": [company.describe() for company in self.companies],
            "settings": self.settings.describe(),
            "guard": self.guard.describe(),
            "log_file": str(self.log_path.relative_to(self.paths.root)),
            "environment": {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
            },
            **extra,
        }

    def write_manifest(self, **extra: Any) -> Path:
        return self.write_artifact("manifest.json", self.manifest(**extra))

    def close(self) -> None:
        self.logger.close()


def create_run_context(
    as_of: date,
    *,
    companies: tuple[Company, ...] | None = None,
    settings: Settings | None = None,
    run_id: str | None = None,
    guard_label: str = "run",
    echo: bool = True,
) -> RunContext:
    """Build a run context and install its point-in-time guard globally."""
    resolved_settings = settings or Settings.from_env()
    resolved_companies = companies if companies is not None else load_companies()
    resolved_run_id = run_id or make_run_id(as_of)

    PATHS.ensure_writable()
    run_dir = PATHS.runs / resolved_run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = PATHS.logs / f"{resolved_run_id}.jsonl"

    logger = RunLogger(resolved_run_id, log_path, redactor=Redactor(), echo=echo)
    guard = AsOfGuard(as_of, label=guard_label)
    asof.set_guard(guard)

    context = RunContext(
        run_id=resolved_run_id,
        as_of=as_of,
        companies=resolved_companies,
        settings=resolved_settings,
        guard=guard,
        logger=logger,
        run_dir=run_dir,
        log_path=log_path,
    )

    logger.event(
        "run.start",
        as_of=as_of.isoformat(),
        commit=context.commit,
        companies=[company.slug for company in resolved_companies],
        settings=resolved_settings.describe(),
        python=sys.version.split()[0],
    )
    return context
