"""Numeric extraction from filing text.

Turning "reported sales of $41.8 billion" into ``41800`` in USDm is where unit
mistakes are born, and a unit mistake is the cheapest way to score the maximum
penalty on a metric. So parsing is explicit about scale at every step and every
extracted value keeps the verbatim text it came from, so a judge -- or the
validation agent -- can check it against the source.

Search terms are derived from the metric labels in ``challenge/companies.json``
rather than written per company, so adding a fifth company needs no new code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .config import Company, Metric
from .corpus import Document, SearchHit

# --------------------------------------------------------------------------
# Number parsing
# --------------------------------------------------------------------------

_NUMBER_TOKEN = re.compile(
    r"""
    (?P<open>\()?                       # accounting negatives: (5)
    \s*(?P<minus>[-−])?            # ASCII hyphen or unicode minus
    \s*(?P<currency>[$£€])?\s*
    (?P<minus2>[-−])?              # currency can precede the sign: $-3.2
    (?P<digits>\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)
    \s*(?P<close>\))?
    \s*(?P<scale>billion|bn|million|mn|m\b|thousand|k\b)?
    \s*(?P<percent>%|percent|percentage\ points?|bps|basis\ points?)?
    \s*(?P<pence>pence|p\b)?
    """,
    re.IGNORECASE | re.VERBOSE,
)

_SCALE_TO_MILLIONS = {
    "billion": 1000.0,
    "bn": 1000.0,
    "million": 1.0,
    "mn": 1.0,
    "m": 1.0,
    "thousand": 0.001,
    "k": 0.001,
}


@dataclass(frozen=True)
class ParsedNumber:
    """A number lifted from text, with everything needed to judge its units."""

    value: float
    raw: str
    currency: str | None
    scale: str | None
    is_percent: bool
    is_pence: bool
    negative: bool

    def as_millions(self) -> float | None:
        """Value expressed in millions, or ``None`` if it is not a money figure."""
        if self.is_percent:
            return None
        multiplier = _SCALE_TO_MILLIONS.get((self.scale or "").lower())
        if multiplier is None:
            # A bare figure inside a financial table is already in the table's
            # units, which for these filings is millions.
            multiplier = 1.0
        return self.value * multiplier

    def as_percentage_points(self) -> float | None:
        if not self.is_percent:
            return None
        if self.scale and self.scale.lower() in ("bps", "basis points", "basis point"):
            return self.value / 100.0
        return self.value


def parse_number(text: str) -> ParsedNumber | None:
    """Parse the first numeric token in ``text``."""
    match = _NUMBER_TOKEN.search(text)
    if not match:
        return None
    return _from_match(match)


def _from_match(match: re.Match[str]) -> ParsedNumber | None:
    digits = match.group("digits")
    if not digits:
        return None
    try:
        value = float(digits.replace(",", ""))
    except ValueError:
        return None

    percent_token = (match.group("percent") or "").strip().lower()
    negative = bool(
        (match.group("open") and match.group("close"))
        or match.group("minus")
        or match.group("minus2")
    )
    if negative:
        value = -value

    scale = (match.group("scale") or "").strip().lower() or None
    if percent_token in ("bps", "basis point", "basis points"):
        scale = "bps"

    return ParsedNumber(
        value=value,
        raw=match.group(0).strip(),
        currency=match.group("currency"),
        scale=scale,
        is_percent=bool(percent_token),
        is_pence=bool(match.group("pence")),
        negative=negative,
    )


def iter_numbers(text: str) -> Iterable[ParsedNumber]:
    for match in _NUMBER_TOKEN.finditer(text):
        parsed = _from_match(match)
        if parsed is not None:
            yield parsed


# --------------------------------------------------------------------------
# Metric vocabulary
# --------------------------------------------------------------------------

# Expansions applied to metric labels so the searcher covers the wording filings
# actually use. Keyed by substring of the label, not by company.
_LABEL_SYNONYMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("eps", ("earnings per share", "per diluted share", "per share")),
    ("diluted eps", ("diluted earnings per share", "per diluted share")),
    ("basic eps", ("basic earnings per share",)),
    ("net sales", ("sales", "net sales")),
    ("revenue", ("revenue", "revenues", "net revenue")),
    ("net fees", ("net fees", "group net fees")),
    ("gross margin", ("gross margin", "gross profit margin")),
    ("operating profit", ("operating profit", "operating income")),
    ("comparable sales", ("comparable sales", "comp sales", "comparable store sales")),
    ("worldwide net sales and revenues", ("net sales and revenues", "worldwide net sales")),
    ("production & precision ag", ("production and precision ag", "production & precision ag")),
)

_ADJUSTMENT_WORDS = ("adjusted", "pre-exceptional", "gaap", "non-gaap", "underlying")


def metric_search_terms(metric: Metric) -> list[str]:
    """Phrases likely to appear near this metric in a filing."""
    label = metric.label.lower()
    terms: list[str] = [metric.label]

    for needle, expansions in _LABEL_SYNONYMS:
        if needle in label:
            terms.extend(expansions)

    # Keep the qualifier attached: "adjusted diluted EPS" and "diluted EPS" are
    # different numbers, and mixing them is a silent scoring error.
    qualifiers = [word for word in _ADJUSTMENT_WORDS if word in label]
    if qualifiers:
        base_terms = list(terms)
        for qualifier in qualifiers:
            for term in base_terms:
                if qualifier not in term.lower():
                    terms.append(f"{qualifier} {term}")

    seen: set[str] = set()
    unique: list[str] = []
    for term in terms:
        key = term.lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(term)
    return unique


def metric_query(company: Company, metric: Metric, *, extra: str = "") -> str:
    """A corpus query for this metric, including the company's reporting period."""
    parts = metric_search_terms(metric)[:4]
    period = re.sub(r"FY(\d{4})(Q(\d))?", r"fiscal \1 \2", company.period).replace("Q", "quarter ")
    return " ".join([*parts, period, extra]).strip()


# --------------------------------------------------------------------------
# Candidate extraction
# --------------------------------------------------------------------------


@dataclass
class ValueCandidate:
    """A number that may be the value of a metric, with its evidence."""

    value: float
    units: str
    raw: str
    context: str
    document: Document
    score: float = 0.0
    notes: list[str] = field(default_factory=list)

    def as_dict(self, context_chars: int = 320) -> dict[str, Any]:
        context = self.context
        if len(context) > context_chars:
            context = context[:context_chars].rstrip() + "..."
        return {
            "value": self.value,
            "units": self.units,
            "raw": self.raw,
            "context": context,
            "score": round(self.score, 3),
            "notes": list(self.notes),
            "source": self.document.citation(),
        }


def _windows(text: str, anchor: str, radius: int = 220) -> Iterable[str]:
    """Text around each occurrence of ``anchor``, case-insensitively."""
    lowered = text.lower()
    needle = anchor.lower()
    start = 0
    while True:
        position = lowered.find(needle, start)
        if position == -1:
            return
        yield text[max(0, position - 40) : position + len(needle) + radius]
        start = position + len(needle)


def _plausible(metric: Metric, value: float) -> bool:
    """Reject values that cannot be this metric, whatever the text said."""
    if metric.kind == "percent":
        return -100.0 <= value <= 100.0
    if metric.kind == "per_share":
        # Covers dollars per share and pence per share alike.
        return 0.0 < abs(value) <= 500.0
    if metric.kind == "money":
        return 1.0 <= abs(value) <= 500_000.0
    return True


def candidates_from_hits(
    company: Company,
    metric: Metric,
    hits: Sequence[SearchHit],
    *,
    max_candidates: int = 40,
) -> list[ValueCandidate]:
    """Pull plausible values for ``metric`` out of ranked search hits."""
    terms = metric_search_terms(metric)
    found: list[ValueCandidate] = []

    for hit in hits:
        for anchor in terms:
            for window in _windows(hit.chunk.text, anchor):
                for parsed in iter_numbers(window):
                    if metric.kind == "percent":
                        value = parsed.as_percentage_points()
                    elif metric.kind == "money":
                        value = None if parsed.is_percent else parsed.as_millions()
                    else:
                        value = None if parsed.is_percent else parsed.value
                    if value is None or not _plausible(metric, value):
                        continue

                    # A money figure in a filing is either written with its unit
                    # ("$41.8 billion") or sits in a table already denominated in
                    # millions, so it is large. A bare small integer next to the
                    # metric name is a note reference, a page number or a row
                    # count -- and there are enough of them to outvote the real
                    # figure if they are allowed through.
                    if (
                        metric.kind == "money"
                        and not parsed.currency
                        and not parsed.scale
                        and abs(value) < 100
                    ):
                        continue

                    notes: list[str] = []
                    score = hit.score
                    if metric.kind == "money" and parsed.scale in ("billion", "bn", "million", "mn"):
                        # An explicit scale word removes all unit ambiguity.
                        score *= 1.6
                        notes.append("explicit-scale")
                    # Prefer figures stated in the same sentence as the metric.
                    distance = window.lower().find(anchor.lower())
                    position = window.find(parsed.raw)
                    if distance >= 0 and position >= 0:
                        gap = abs(position - distance)
                        score *= 1.0 / (1.0 + gap / 120.0)
                        notes.append(f"gap={gap}")
                    if parsed.currency:
                        score *= 1.1
                        notes.append(f"currency={parsed.currency}")
                    if parsed.scale:
                        notes.append(f"scale={parsed.scale}")

                    found.append(
                        ValueCandidate(
                            value=value,
                            units=metric.units,
                            raw=parsed.raw,
                            context=window.strip(),
                            document=hit.document,
                            score=score,
                            notes=notes,
                        )
                    )

    found.sort(key=lambda candidate: candidate.score, reverse=True)

    # The same figure is often matched through several anchor phrases and in
    # several passages of one filing. Left in, those duplicates would act as
    # independent corroboration when they are really one statement.
    deduped: list[ValueCandidate] = []
    seen: set[tuple[str, float, str]] = set()
    for candidate in found:
        key = (candidate.document.doc_id, round(candidate.value, 6), candidate.raw)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)

    return deduped[:max_candidates]
