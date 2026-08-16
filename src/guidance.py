"""Guidance-anchored forecasting.

When a company has already told the market what it expects, that statement is
usually a better forecast than anything reconstructed from its history. ADI
guided Q3 FY2026 revenue to $3.9bn and adjusted EPS to $3.30; Deere guided FY2026
net income to $4.5-5.0bn. A statistical projection that ignores those is throwing
away the single most informative sentence in the corpus.

Two cautions shape this module:

* **Guidance is a claim about a specific period.** A figure lifted from a
  guidance paragraph that refers to the full year is not a quarterly forecast.
* **Guidance is systematically biased.** Companies that habitually beat their own
  midpoint make the raw midpoint the wrong anchor, so the historical
  guidance-versus-actual bias measured by :mod:`src.series` is applied where
  there is enough history to measure it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .config import Company, Metric
from .corpus import CorpusIndex, SearchHit
from .extract import (
    ValueCandidate,
    candidates_from_hits,
    metric_search_terms,
    wants_annual,
)

# Sentences that state an expectation rather than a result. Deliberately narrow:
# "guidance" and "outlook" on their own also appear in headlines and in
# boilerplate around reported figures.
GUIDANCE_PHRASES = (
    "we are forecasting",
    "we expect",
    "we currently expect",
    "we anticipate",
    "is forecasted to be",
    "are forecasted to be",
    "we are planning for",
    "we are guiding",
    "guidance for",
    "outlook for",
    "expects to",
)

# Past-tense result language. Press releases open with an unpunctuated headline,
# so a naive sentence split glues "Reaffirms Fiscal 2026 Guidance" onto "today
# reported sales of $41.8 billion" and the reported figure is read as guidance.
REPORTED_PHRASES = (
    "reported",
    "compared with",
    "compared to",
    "in the same period",
    "results for",
    "were $",
    "was $",
    "increased",
    "decreased",
)

# A range such as "$4.5 billion to $5.0 billion" or "3.9 billion, +/- 100 million".
RANGE_JOINERS = (" to ", " - ", " through ", "+/-", "±")

# Longest plausible guidance sentence. Beyond this it is a table or a paragraph
# of several statements, and attributing one figure to the metric is guesswork.
MAX_GUIDANCE_SENTENCE = 420

# Whether the measured guidance-versus-actual bias may adjust a submitted figure.
# See apply_bias() for why this is off.
TRUST_MEASURED_BIAS = False


@dataclass
class GuidanceAnchor:
    """A forward-looking figure the company itself published."""

    metric: str
    units: str
    value: float
    raw: str
    context: str
    published_at: str
    source_path: str
    bias_applied: float | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "units": self.units,
            "value": self.value,
            "raw": self.raw,
            "context": self.context[:320],
            "published_at": self.published_at,
            "source": self.source_path,
            "bias_applied": self.bias_applied,
            "notes": list(self.notes),
        }


def _guidance_windows(text: str) -> list[str]:
    """Sentences that state an expectation, with result statements excluded.

    A sentence must both announce an expectation and be free of past-tense
    result language. Requiring only the former reads reported figures as
    guidance; requiring both leaves genuine forward-looking statements.
    """
    windows: list[str] = []
    for sentence in re.split(r"(?<=[.;])\s+|\n{2,}", text):
        lowered = sentence.lower()
        if not any(phrase in lowered for phrase in GUIDANCE_PHRASES):
            continue
        if any(phrase in lowered for phrase in REPORTED_PHRASES):
            continue
        # Guidance is written as prose. Financial tables carry no sentence
        # punctuation, so a whole table can arrive here as one "sentence" and
        # its reported figures get read as an expectation.
        if len(sentence) > MAX_GUIDANCE_SENTENCE or sentence.count("|") > 2:
            continue
        if sum(character.isdigit() for character in sentence) / max(len(sentence), 1) > 0.2:
            continue
        windows.append(sentence)
    return windows


def find_guidance(
    index: CorpusIndex,
    company: Company,
    metric: Metric,
    *,
    limit: int = 8,
) -> GuidanceAnchor | None:
    """Find the company's most recent published expectation for this metric.

    Uses ``search_latest`` because guidance is restated across filings and only
    the newest statement is still in force.
    """
    terms = metric_search_terms(metric)[:3]
    period_word = "full year" if wants_annual(company) else "quarter"
    query = f"{' '.join(terms)} outlook guidance we expect {period_word}"

    hits = index.search_latest(
        query, company=company.slug, document_types=["FILING"], limit=limit
    )
    if not hits:
        return None

    for hit in hits:
        windows = _guidance_windows(hit.chunk.text)
        if not windows:
            continue

        # Re-run extraction over only the guidance sentences, so a reported
        # figure elsewhere in the same passage cannot be mistaken for guidance.
        synthetic = SearchHit(
            document=hit.document,
            chunk=type(hit.chunk)(
                chunk_id=hit.chunk.chunk_id,
                doc_id=hit.chunk.doc_id,
                ordinal=hit.chunk.ordinal,
                heading=hit.chunk.heading,
                text="\n".join(windows),
            ),
            score=hit.score,
            numbers=(),
        )
        candidates: list[ValueCandidate] = candidates_from_hits(
            company, metric, [synthetic], max_candidates=6
        )
        if not candidates:
            continue

        best = candidates[0]
        notes = [f"guidance sentence from {hit.document.document_type.lower()}"]
        if any(joiner in best.context for joiner in RANGE_JOINERS):
            notes.append("stated as a range; midpoint handling is approximate")

        return GuidanceAnchor(
            metric=metric.label,
            units=metric.units,
            value=best.value,
            raw=best.raw,
            context=best.context,
            published_at=(
                hit.document.published_at.isoformat() if hit.document.published_at else ""
            ),
            source_path=hit.document.rel_path,
            notes=notes,
        )
    return None


def apply_bias(anchor: GuidanceAnchor, bias: dict[str, Any] | None) -> GuidanceAnchor:
    """Correct a guidance figure by how this company's guidance usually lands.

    Only applied when there is enough history to measure it and the measured
    bias is large enough to matter. A correction smaller than a percent is noise,
    and applying it would imply a precision the data does not support.
    """
    # Disabled deliberately. The bias is measured by pairing guidance points with
    # the result that followed, and those guidance points come from the same
    # detector that is only right about a quarter of the time -- so the
    # correction inherits and amplifies that error. It pushed ADI's correctly
    # read $3.9bn guidance to $4.41bn, which is worse than not correcting at all.
    # The statistic is still computed and shown in the dashboard; it is not
    # allowed to move a submitted number until the pairing can be validated.
    if not TRUST_MEASURED_BIAS:
        anchor.notes.append(
            "measured guidance bias not applied: the pairing that produces it is "
            "not yet reliable enough to move a forecast"
        )
        return anchor

    if not bias or bias.get("observations", 0) < 4:
        anchor.notes.append("no measured guidance bias; midpoint used unadjusted")
        return anchor

    median_surprise = bias.get("median_surprise")
    if median_surprise is None or abs(median_surprise) < 0.01:
        anchor.notes.append("measured guidance bias below 1%; midpoint used unadjusted")
        return anchor

    # Guard against a bias estimate distorted by a period the company
    # restructured or restated; beyond a quarter of the value it is not credible.
    correction = max(-0.25, min(0.25, median_surprise))
    anchor.bias_applied = correction
    anchor.value = anchor.value * (1.0 + correction)
    anchor.notes.append(
        f"corrected by measured guidance bias of {correction:+.1%} "
        f"over {bias['observations']} past statements"
    )
    return anchor
