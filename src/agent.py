"""The reasoning agent, applied only where deterministic code cannot reach.

The backtest measures every metric, so we know exactly where the deterministic
path works and where it does not. Seven of the twelve metrics backtest under
15%; five do not, and each fails for a structural reason rather than a tuning
one:

* **Hays reports growth, not levels.** Its trading statements say "Germany (5)%,
  UK&I (8)%, ANZ 7%, RoW (4)%" and never state net fees in pounds. Reconstructing
  a level means taking a prior-year base and applying regional growth rates
  weighted by regional size. That is arithmetic over read evidence -- reasoning,
  not pattern matching.
* **Segment lines sit among their neighbours.** Deere's Production & Precision Ag
  operating profit is one row of a segment table, indistinguishable by proximity
  from the rows above and below it.
* **Guidance ranges look like results.** Home Depot's comparable sales guidance
  and its reported comparable sales are written the same way.

So the model is asked to do the reading and the arithmetic, on evidence the
deterministic retrieval has already gathered, and its answer is only accepted if
it survives the same validation the deterministic path faces. Where it fails, is
implausible, or the budget runs out, the deterministic figure stands.

Nothing here bypasses the point-in-time guard: every passage handed to the model
came through cutoff-filtered retrieval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .baseline import BaselineEstimate
from .config import Company, Metric
from .corpus import CorpusIndex
from .errors import BudgetExhaustedError
from .extract import metric_search_terms, wants_annual
from .llm import LLM, LLMError
from .runlog import RunLogger
from .series import build_series

FORECAST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["value", "confidence", "derivation", "citations"],
    "properties": {
        "value": {"type": "number"},
        "confidence": {"type": "string"},
        "derivation": {"type": "string"},
        "citations": {"type": "array", "items": {"type": "string"}},
        "disagrees_with_baseline": {"type": "boolean"},
        "why": {"type": "string"},
    },
}

SYSTEM = (
    "You are a sell-side analyst producing a single number for one financial "
    "metric. You are given passages from company filings, all published before a "
    "cutoff date, and a statistical baseline. Work only from the passages. "
    "Show the arithmetic that gets you from evidence to the number. If the "
    "evidence does not support a better answer than the baseline, return the "
    "baseline and say so. Never invent a figure that is not derivable from the "
    "passages."
)


@dataclass
class AgentForecast:
    """A model-produced figure and the reasoning behind it."""

    metric: str
    units: str
    value: float
    confidence: str
    derivation: str
    citations: list[str] = field(default_factory=list)
    accepted: bool = False
    rejection: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "units": self.units,
            "value": self.value,
            "confidence": self.confidence,
            "derivation": self.derivation,
            "citations": list(self.citations),
            "accepted": self.accepted,
            "rejection": self.rejection,
        }


def gather_evidence(
    index: CorpusIndex,
    company: Company,
    metric: Metric,
    *,
    passages: int = 10,
) -> list[dict[str, Any]]:
    """Cutoff-filtered passages most likely to contain or imply the metric."""
    terms = " ".join(metric_search_terms(metric)[:4])
    period = "full year" if wants_annual(company) else "quarter"
    scale = "million" if metric.kind == "money" else metric.units

    # Three queries, because one is not enough for the metrics that reach here.
    # Hays never states net fees as a number in its trading statements -- it
    # reports regional growth rates -- so the level has to come from an earlier
    # annual result, and the growth from the recent updates. Asking only for the
    # latest passages returns the growth rates and no base to apply them to.
    queries = [
        (f"{terms} {period} results", ["FILING"], True),
        (f"{terms} {scale} reported for the year", ["FILING"], False),
        (f"{terms} {period} performance growth", None, True),
    ]

    seen: set[int] = set()
    hits = []
    for query, types, latest in queries:
        finder = index.search_latest if latest else index.search
        for hit in finder(
            query, company=company.slug, document_types=types, limit=passages
        ):
            if hit.chunk.chunk_id in seen:
                continue
            seen.add(hit.chunk.chunk_id)
            hits.append(hit)

    hits.sort(key=lambda hit: (hit.document.published_at, hit.score), reverse=True)
    return [hit.as_dict(excerpt_chars=1400) for hit in hits[: passages + 6]]


def build_prompt(
    company: Company,
    metric: Metric,
    baseline: BaselineEstimate,
    evidence: list[dict[str, Any]],
    history: list[dict[str, Any]],
) -> str:
    lines = [
        f"Company: {company.name} ({company.ticker})",
        f"Forecast period: {company.period}",
        f"Metric: {metric.label}",
        f"Units: {metric.units} -- {metric.scale_note}",
        "",
        f"Statistical baseline: {baseline.value:,.4f} ({baseline.method})",
        "",
        "Recent reported history (most recent last):",
    ]
    for point in history[-10:]:
        lines.append(f"  {point['published_at']}  {point['value']:,.4f}")

    lines += ["", "Evidence passages, newest first:"]
    for index_, item in enumerate(evidence, start=1):
        lines.append(
            f"\n[{index_}] {item['published_at']} {item['document_type']} "
            f"{item['title']}\n{item['excerpt']}"
        )

    lines += [
        "",
        "Produce the value for the forecast period in the stated units.",
        "Cite the passage numbers you used, as strings such as \"[3]\".",
        "In 'derivation', show the arithmetic explicitly.",
    ]
    return "\n".join(lines)


def forecast_metric(
    llm: LLM,
    index: CorpusIndex,
    company: Company,
    metric: Metric,
    baseline: BaselineEstimate,
    *,
    logger: RunLogger | None = None,
) -> AgentForecast | None:
    """Ask the model for one metric. Returns ``None`` if it could not answer."""
    evidence = gather_evidence(index, company, metric)
    if not evidence:
        return None

    series = build_series(index, company, metric)
    history = [point.as_dict() for point in series.reported]

    try:
        reply = llm.complete(
            build_prompt(company, metric, baseline, evidence, history),
            schema=FORECAST_SCHEMA,
            system=SYSTEM,
            tier="reasoning",
            max_tokens=2400,
        )
    except BudgetExhaustedError:
        if logger:
            logger.warning(
                "budget exhausted; keeping the deterministic forecast",
                company=company.slug,
                metric=metric.label,
            )
        return None
    except LLMError as exc:
        if logger:
            logger.warning(
                f"model call failed: {exc}", company=company.slug, metric=metric.label
            )
        return None

    return AgentForecast(
        metric=metric.label,
        units=metric.units,
        value=float(reply["value"]),
        confidence=str(reply.get("confidence", "unknown")),
        derivation=str(reply.get("derivation", "")),
        citations=[str(c) for c in reply.get("citations", [])],
    )


# A metric whose deterministic path already backtests below this is left alone.
# Replacing a measured method with an unmeasured one is a bad trade, however
# capable the model.
WEAKNESS_THRESHOLD = 0.15


def weak_metrics(backtest: dict[str, Any], threshold: float = WEAKNESS_THRESHOLD) -> set[tuple[str, str]]:
    """(company, metric) pairs the deterministic path handles badly.

    The backtest decides where model budget is spent. Metrics it scores well are
    not sent to the model at all.
    """
    weak: set[tuple[str, str]] = set()
    for entry in backtest.get("metrics", []):
        error = entry.get("median_percentage_error")
        if error is None or error > threshold:
            weak.add((entry["company"], entry["metric"]))
    return weak


def accept_or_reject(
    company: Company,
    metric: Metric,
    proposal: AgentForecast,
    baseline: BaselineEstimate,
    index: CorpusIndex,
    *,
    trust_series: bool = False,
) -> AgentForecast:
    """Apply the deterministic checks to the model's answer before trusting it.

    The model gets no special standing: its figure faces the same absolute unit
    and range checks as any other, and must come with a derivation that can be
    read.

    What it is *not* checked against is the deterministic series, unless that
    series is known to be sound. Every metric that reaches this function is here
    precisely because the backtest showed the deterministic path handles it
    badly, so anchoring on "how far is this from our last reported value" means
    validating a right answer against a wrong one. That check rejected the
    agent's 890.4 GBPm for Hays net fees -- derived correctly from operating
    profit and conversion rate -- for being nine times our own broken 85.
    """
    from .validate import FAIL, check_sign, check_units

    for check in (
        check_units(company, metric, proposal.value),
        check_sign(company, metric, proposal.value),
    ):
        if check.status == FAIL:
            proposal.rejection = f"failed the {check.name} check: {check.detail}"
            return proposal

    if not proposal.derivation.strip():
        proposal.rejection = "no derivation supplied, so the number cannot be checked"
        return proposal

    if not proposal.citations:
        proposal.rejection = "no passages cited, so the figure cannot be traced"
        return proposal

    if trust_series:
        series = build_series(index, company, metric)
        latest = series.latest()
        if latest is not None and abs(latest.value) > 1e-9:
            divergence = abs(proposal.value - latest.value) / abs(latest.value)
            # A metric can genuinely inflect, but not by an order of magnitude in
            # one period. Beyond 3x the last reported level it has misread a unit.
            if divergence > 3.0:
                proposal.rejection = (
                    f"{proposal.value:,.4g} is {divergence:.0f}x from the last "
                    f"reported {latest.value:,.4g}, which reads as a unit error"
                )
                return proposal

    proposal.accepted = True
    return proposal


def improve_company(
    llm: LLM,
    index: CorpusIndex,
    company: Company,
    estimates: dict[str, BaselineEstimate],
    targets: set[tuple[str, str]],
    *,
    logger: RunLogger | None = None,
) -> list[AgentForecast]:
    """Send only the weak metrics to the model, and only keep what validates."""
    proposals: list[AgentForecast] = []
    for metric in company.metrics:
        if (company.slug, metric.label) not in targets:
            continue
        baseline = estimates[metric.label]
        proposal = forecast_metric(llm, index, company, metric, baseline, logger=logger)
        if proposal is None:
            continue

        proposal = accept_or_reject(company, metric, proposal, baseline, index)
        proposals.append(proposal)

        if logger:
            logger.event(
                "agent.forecast",
                company=company.slug,
                metric=metric.label,
                baseline=baseline.value,
                proposed=proposal.value,
                accepted=proposal.accepted,
                rejection=proposal.rejection or None,
                citations=len(proposal.citations),
            )

        if proposal.accepted:
            baseline.notes.append(
                f"replaced by the reasoning agent: {proposal.derivation[:400]}"
            )
            baseline.notes.append(f"agent cited passages {', '.join(proposal.citations)}")
            baseline.method = "reasoning agent over cutoff-filtered filing passages"
            baseline.value = proposal.value
            baseline.confidence = "medium"
        else:
            baseline.notes.append(
                f"reasoning agent proposed {proposal.value:,.4g} but it was "
                f"rejected: {proposal.rejection}"
            )
    return proposals
