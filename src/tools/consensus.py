"""Deterministic consensus scanner.

Published analyst consensus is the single most valuable fact available, because the
accuracy score is our error divided by Wall Street's error. If a company publishes the
number we are being measured against, finding it is not optional.

The research agent was given "company compiled consensus" as a query template and did not
reliably use it - Hays' calls channel came back empty while the corpus plainly states
consensus of GBP 43.5m. Anything this valuable belongs in code, not in an agent's
discretion.

UK-listed companies routinely publish compiled consensus; US filers essentially never do.
So this scan is cheap and usually returns nothing - that is the expected outcome, not a
failure.
"""

from __future__ import annotations

import re
from datetime import timedelta

from .documents import DocumentTools

# Phrases that introduce a published consensus figure.
TRIGGERS = (
    "company compiled consensus",
    "company complied consensus",
    "company-compiled consensus",
    "compiled consensus",
    "complied consensus",
    "consensus range",
    "analyst consensus",
    "consensus expectations",
    "consensus for",
    "market expectations",
    "vuma consensus",
)

# "GBP 43.5m", "£43.5 million", "43.5m", with an optional range.
# A money amount must carry a currency symbol or a magnitude suffix. Bare integers in
# these statements are footnote markers, analyst counts and dates - never the figure.
_MONEY = r"(?:[£$€]\s*)?(\d+(?:[.,]\d+)?)\s*(?:m\b|million|bn\b|billion)"
_RANGE = re.compile(
    rf"[£$€]?\s*(\d+(?:[.,]\d+)?)\s*(?:m\b|million)?\s*(?:-|–|to)\s*"
    rf"[£$€]?\s*(\d+(?:[.,]\d+)?)\s*(?:m\b|million|bn\b|billion)",
    re.I,
)
_POINT = re.compile(_MONEY, re.I)

# Management steering language, which tells us where in a range they expect to land.
STEER = {
    "top of": "upper",
    "upper end": "upper",
    "top end": "upper",
    "above the": "upper",
    "middle of": "mid",
    "midpoint": "mid",
    "lower end": "lower",
    "bottom of": "lower",
    "below the": "lower",
}


# The corpus is PDF-extracted and splits numbers with stray spaces: "£4 3.5 m" is 43.5,
# "£37 .0-46 .0m" is 37.0-46.0, "1 0 analysts" is 10. Repair before matching, or the regex
# happily returns 3.5 and the forecast is off by an order of magnitude.
_DIGIT_GAP = re.compile(r"(?<=\d)\s+(?=[\d.])")
_DOT_GAP = re.compile(r"(?<=\d)\s+(?=\.)|(?<=\.)\s+(?=\d)")
_UNIT_GAP = re.compile(r"(?<=\d)\s+(?=(?:m|bn|million|billion)\b)", re.I)
# Same extraction artefact splits hyphenated words: "pre -exceptional operating profit".
_HYPHEN_GAP = re.compile(r"\s*-\s*(?=[A-Za-z])")


def repair_numbers(text: str) -> str:
    """Close spaces that PDF extraction inserted inside numeric tokens."""
    text = _DOT_GAP.sub("", text)
    text = _DIGIT_GAP.sub("", text)
    text = _UNIT_GAP.sub("", text)
    return _HYPHEN_GAP.sub("-", text)


def _to_float(text: str) -> float | None:
    try:
        return float(text.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def scan_consensus(tools: DocumentTools, metric_labels: list[str],
                   limit_docs: int = 6, max_age_days: int = 400) -> list[dict]:
    """Search recent documents for published consensus and return anchors.

    Returns a list shaped like the research agent's `anchors`, so it drops straight into
    the same reconciliation path.
    """
    found: list[dict] = []
    seen_docs: set[str] = set()

    queries = [
        "company compiled consensus analysts",
        "consensus range operating profit",
        "analyst consensus expectations",
    ]
    candidates = []
    for query in queries:
        for doc, _score in tools.search(query, limit=4):
            if doc.doc_id not in seen_docs:
                seen_docs.add(doc.doc_id)
                candidates.append(doc)
    # A consensus published for a prior year is not a forecast for this one.
    cutoff = tools.as_of - timedelta(days=max_age_days)
    candidates = [d for d in candidates if d.published_at and d.published_at >= cutoff]
    candidates.sort(key=lambda d: d.published_at, reverse=True)

    for doc in candidates[:limit_docs]:
        body = doc.body
        lowered = body.lower()
        for trigger in TRIGGERS:
            start = lowered.find(trigger)
            if start < 0:
                continue

            # Look forward from the trigger - the figure follows the phrase.
            window = body[start: start + 340]
            flat = repair_numbers(" ".join(window.split()))
            context = repair_numbers(" ".join(body[max(0, start - 300): start + 340].split()))

            # Which metric is this consensus about?
            metric, best_pos = None, 10**9
            for label in metric_labels:
                key = label.lower().split("(")[0].strip()
                if not key:
                    continue
                # The metric must be named AFTER the trigger phrase. Requiring only
                # nearby mention produced false positives - "net fee productivity" sitting
                # in the same paragraph claimed an operating-profit consensus.
                pos = flat.lower().find(key)
                if pos >= 0 and pos < best_pos:
                    metric, best_pos = label, pos
            if metric is None:
                continue

            low = high = point = None
            range_match = _RANGE.search(flat)
            if range_match:
                low, high = sorted(
                    v for v in (_to_float(range_match.group(1)), _to_float(range_match.group(2)))
                    if v is not None
                ) or (None, None)

            point_match = _POINT.search(flat)
            if point_match:
                point = _to_float(point_match.group(1))

            steer = None
            doc_text = repair_numbers(" ".join(body.split())).lower()
            for phrase, position in STEER.items():
                idx = doc_text.find(phrase)
                # Only count steering language that sits near a range or consensus mention.
                if idx >= 0 and re.search(r"(range|consensus)", doc_text[idx: idx + 160]):
                    steer = position
                    break

            # Management steering beats the midpoint: "we expect to be at the top of the
            # 37.0-46.0m range" means ~46, not 43.5.
            value = point
            if low is not None and high is not None:
                if steer == "upper":
                    value = high - (high - low) * 0.08
                elif steer == "lower":
                    value = low + (high - low) * 0.08
                elif steer == "mid" or value is None:
                    value = (low + high) / 2

            if value is None:
                continue

            found.append({
                "metric": metric,
                "kind": "consensus",
                "value": round(value, 4),
                "low": low,
                "high": high,
                "steer": steer,
                "note": f"{trigger}: {flat[:220]}",
                "doc_id": doc.doc_id,
                "published_at": doc.published_at.isoformat() if doc.published_at else None,
                "confidence": "high" if steer else "medium",
                "source": "deterministic_scan",
            })
            break

    # One anchor per metric, newest first.
    best: dict[str, dict] = {}
    for anchor in found:
        current = best.get(anchor["metric"])
        if not current or (anchor["published_at"] or "") > (current["published_at"] or ""):
            best[anchor["metric"]] = anchor
    return list(best.values())


# A deterministic guidance scanner was attempted here and removed. ADI publishes
# "revenue of $3.9 billion, +/- $100 million" for the exact target quarter, and two
# consecutive runs landed at 4,106 and 3,625 - both outside a band management printed -
# because the guidance rail only engages when the research pass happens to record the
# anchor. The scan itself proved fiddly (filings spell quarters out, guidance blocks span
# several sentences, magnitudes mix billions and millions) and was cut at feature freeze
# rather than shipped half-working. The gap is real and is declared in the write-up.
