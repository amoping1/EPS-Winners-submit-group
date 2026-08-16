"""Repository paths, runtime settings and the challenge specification.

The twelve forecast targets are read from ``challenge/companies.json`` at runtime
and never hardcoded: the organisers' validator compares metric labels and units by
exact string match, so a copy in our source would be a silent way to fail the
submission.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .errors import ConfigurationError

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Paths:
    """Every location the system reads from or writes to."""

    root: Path = REPO_ROOT
    challenge: Path = REPO_ROOT / "challenge"
    companies_file: Path = REPO_ROOT / "challenge" / "companies.json"
    offline_data: Path = REPO_ROOT / "challenge" / "offline-data"
    templates: Path = REPO_ROOT / "challenge" / "templates"
    submission: Path = REPO_ROOT / "submission"
    runs: Path = REPO_ROOT / "runs"
    logs: Path = REPO_ROOT / "logs"
    cache: Path = REPO_ROOT / ".cache"
    news_cache: Path = REPO_ROOT / "data" / "news-cache"
    dashboard: Path = REPO_ROOT / "dashboard"
    architecture: Path = REPO_ROOT / "architecture" / "index.html"

    def ensure_writable(self) -> None:
        for directory in (self.submission, self.runs, self.logs, self.cache, self.news_cache):
            directory.mkdir(parents=True, exist_ok=True)


PATHS = Paths()


# --------------------------------------------------------------------------
# Environment
# --------------------------------------------------------------------------


def load_dotenv(path: Path | None = None, *, override: bool = False) -> dict[str, str]:
    """Load ``KEY=VALUE`` pairs from a .env file into ``os.environ``.

    Deliberately dependency-free. Values already present in the environment win
    unless ``override`` is set, so a shell export can beat the file.
    """
    env_path = Path(path) if path else REPO_ROOT / ".env"
    loaded: dict[str, str] = {}
    if not env_path.exists():
        return loaded
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        loaded[key] = value
        if override or key not in os.environ:
            os.environ[key] = value
    return loaded


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Runtime configuration, all overridable by environment variable."""

    llm_primary: str = "openai"
    llm_fallback: str | None = None
    model_fast: str = ""
    model_reasoning: str = ""
    max_usd_budget: float = 40.0
    request_timeout_s: float = 120.0
    max_retries: int = 4
    max_workers: int = 4
    backtest_events: int = 8

    @classmethod
    def from_env(cls) -> "Settings":
        fallback = os.environ.get("LLM_FALLBACK", "").strip() or None
        return cls(
            llm_primary=os.environ.get("LLM_PRIMARY", "openai").strip() or "openai",
            llm_fallback=fallback,
            model_fast=os.environ.get("MODEL_FAST", "").strip(),
            model_reasoning=os.environ.get("MODEL_REASONING", "").strip(),
            max_usd_budget=_env_float("MAX_USD_BUDGET", 40.0),
            request_timeout_s=_env_float("LLM_TIMEOUT_S", 120.0),
            max_retries=_env_int("LLM_MAX_RETRIES", 4),
            max_workers=_env_int("MAX_WORKERS", 4),
            backtest_events=_env_int("BACKTEST_EVENTS", 8),
        )

    def has_llm_credentials(self) -> bool:
        return any(
            os.environ.get(name, "").strip()
            for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")
        )

    def describe(self) -> dict[str, Any]:
        """Configuration summary safe to log: never includes credential values."""
        return {
            "llm_primary": self.llm_primary,
            "llm_fallback": self.llm_fallback,
            "model_fast": self.model_fast or None,
            "model_reasoning": self.model_reasoning or None,
            "max_usd_budget": self.max_usd_budget,
            "max_workers": self.max_workers,
            "backtest_events": self.backtest_events,
            "credentials_present": self.has_llm_credentials(),
        }


# --------------------------------------------------------------------------
# Challenge specification
# --------------------------------------------------------------------------

PERCENT_UNITS = {"%"}
PER_SHARE_UNITS = {"USD / share", "GBp"}
MILLIONS_UNITS = {"USDm", "GBPm"}


@dataclass(frozen=True)
class Metric:
    """One forecast target, exactly as the organisers' validator expects it."""

    label: str
    units: str

    @property
    def kind(self) -> str:
        """``percent``, ``per_share`` or ``money``."""
        if self.units in PERCENT_UNITS:
            return "percent"
        if self.units in PER_SHARE_UNITS:
            return "per_share"
        if self.units in MILLIONS_UNITS:
            return "money"
        return "unknown"

    @property
    def currency(self) -> str | None:
        if self.units in ("USDm", "USD / share"):
            return "USD"
        if self.units == "GBPm":
            return "GBP"
        if self.units == "GBp":
            return "GBp"
        return None

    @property
    def scale_note(self) -> str:
        """Human-readable reminder of the expected magnitude, used in validation."""
        if self.kind == "percent":
            return "percentage points, so 4.5 means 4.5%"
        if self.units == "GBp":
            return "pence per share, so 6.2 means 6.2p"
        if self.kind == "per_share":
            return "currency per share, so 3.25 means $3.25"
        if self.kind == "money":
            return f"millions of {self.currency}, so 41800 means 41.8 billion"
        return "unknown units"

    @property
    def key(self) -> str:
        """Stable identifier for filenames and JSON keys."""
        return re.sub(r"[^a-z0-9]+", "_", self.label.lower()).strip("_")


@dataclass(frozen=True)
class Company:
    """One challenge company and its three metrics."""

    name: str
    ticker: str
    period: str
    output_file: str
    metrics: tuple[Metric, ...]

    @property
    def slug(self) -> str:
        """Short ticker used throughout the system: HD, ADI, HAS, DE."""
        return self.ticker.rsplit(":", 1)[-1].upper()

    @property
    def corpus_dir(self) -> Path:
        return resolve_corpus_dir(self.name)

    def metric_by_label(self, label: str) -> Metric:
        for metric in self.metrics:
            if metric.label == label:
                return metric
        raise ConfigurationError(f"{self.slug} has no metric labelled {label!r}")

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ticker": self.ticker,
            "slug": self.slug,
            "period": self.period,
            "output_file": self.output_file,
            "metrics": [
                {"label": m.label, "units": m.units, "kind": m.kind} for m in self.metrics
            ],
        }


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


@lru_cache(maxsize=None)
def resolve_corpus_dir(company_name: str) -> Path:
    """Find a company's document folder from its name, without hardcoding folders.

    ``Home Depot`` maps directly to ``home-depot``; ``Hays plc`` and
    ``Deere & Company`` need trailing words dropped before they match.
    """
    root = PATHS.offline_data
    if not root.exists():
        raise ConfigurationError(f"Offline data folder is missing: {root}")

    candidates = [directory for directory in root.iterdir() if directory.is_dir()]
    by_slug = {directory.name: directory for directory in candidates}

    tokens = _slugify(company_name).split("-")
    while tokens:
        candidate = "-".join(tokens)
        if candidate in by_slug:
            return by_slug[candidate]
        tokens.pop()

    first = _slugify(company_name).split("-")[0]
    for slug, directory in by_slug.items():
        if slug.startswith(first):
            return directory

    raise ConfigurationError(
        f"No document folder found for {company_name!r} in {root}. "
        f"Available: {sorted(by_slug)}"
    )


@lru_cache(maxsize=None)
def load_companies(path: Path | None = None) -> tuple[Company, ...]:
    """Read the twelve forecast targets from the organisers' specification."""
    spec_path = Path(path) if path else PATHS.companies_file
    try:
        payload: dict[str, Any] = json.loads(spec_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"Challenge specification is missing: {spec_path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"Challenge specification is not valid JSON: {exc}") from exc

    entries = payload.get("companies")
    if not isinstance(entries, list) or not entries:
        raise ConfigurationError("Challenge specification contains no companies")

    companies: list[Company] = []
    for entry in entries:
        metrics = tuple(
            Metric(label=str(m["label"]), units=str(m["units"]))
            for m in entry.get("metrics", [])
        )
        if not metrics:
            raise ConfigurationError(f"{entry.get('ticker')!r} has no metrics")
        companies.append(
            Company(
                name=str(entry["company"]),
                ticker=str(entry["ticker"]),
                period=str(entry["period"]),
                output_file=str(entry["outputFile"]),
                metrics=metrics,
            )
        )
    return tuple(companies)


def find_company(selector: str, companies: tuple[Company, ...] | None = None) -> Company:
    """Look up a company by ticker, short ticker or name, case-insensitively."""
    pool = companies if companies is not None else load_companies()
    wanted = selector.strip().casefold()
    for company in pool:
        identifiers = {
            company.ticker.casefold(),
            company.slug.casefold(),
            company.name.casefold(),
        }
        if wanted in identifiers:
            return company
    available = ", ".join(company.slug for company in pool)
    raise ConfigurationError(f"Unknown company {selector!r}. Available: {available}")
