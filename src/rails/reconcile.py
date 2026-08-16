"""Reconciliation rail.

Deterministic on purpose. The three forecasters disagree; something has to choose, and
that something must not be persuadable. An LLM asked to pick between its own three answers
will rationalise. Arithmetic will not.

Not a flat median. Guidance quality varies enormously across these four companies - ADI
guides the target quarter with a point estimate, Deere guides only the full year, Hays has
published analyst consensus. Weighting all three methods equally throws away the strongest
signals available.
"""

from __future__ import annotations

from statistics import median

# Method weights. Guidance wins where guidance exists; the statistical model is the
# reliable floor; qualitative is a tie-breaker that should rarely dominate.
BASE_WEIGHTS = {"guidance": 1.0, "statistical": 0.8, "qualitative": 0.6}
CONFIDENCE_WEIGHTS = {"high": 1.0, "medium": 0.7, "low": 0.4}

# How far a single forecaster may sit from the group median before it is treated as an
# outlier and down-weighted. Capped scoring punishes blow-ups far more than it rewards
# precision, so we lean hard against outliers.
OUTLIER_RATIO = 0.35


def _relative_spread(value: float, ref: float) -> float:
    if ref == 0:
        return abs(value)
    return abs(value - ref) / abs(ref)


def reconcile_metric(
    proposals: dict[str, dict],
    anchor: dict | None = None,
    history: list[float] | None = None,
) -> dict:
    """Combine method proposals for one metric into a single number.

    Returns the chosen value plus the audit trail: weights applied, outliers found, and
    whether a clamp fired. Everything here is explainable to a judge.
    """
    if not proposals:
        return {"value": None, "note": "no proposals"}

    values = {m: p["value"] for m, p in proposals.items()}
    ref = median(values.values())

    weights, outliers = {}, []
    for method, proposal in proposals.items():
        weight = BASE_WEIGHTS.get(method, 0.5) * CONFIDENCE_WEIGHTS.get(
            proposal.get("confidence", "medium"), 0.7
        )
        spread = _relative_spread(proposal["value"], ref)
        if spread > OUTLIER_RATIO:
            weight *= 0.25
            outliers.append({"method": method, "value": proposal["value"],
                             "spread_from_median": round(spread, 3)})
        weights[method] = weight

    # A high-confidence company guidance anchor outranks any model. Management sees the
    # quarter from the inside; our trend fit does not.
    anchor_pull = None
    if anchor and anchor.get("kind") in ("guidance", "consensus"):
        if isinstance(anchor.get("value"), (int, float)):
            anchor_weight = 1.2 if anchor["kind"] == "guidance" else 0.9
            if anchor.get("confidence") == "low":
                anchor_weight *= 0.5
            weights["_anchor"] = anchor_weight
            values["_anchor"] = float(anchor["value"])
            anchor_pull = {"kind": anchor["kind"], "value": float(anchor["value"]),
                           "weight": round(anchor_weight, 2)}

    total = sum(weights.values()) or 1.0
    combined = sum(values[m] * w for m, w in weights.items()) / total

    # Clamp against observed history. A forecast far outside everything ever reported is
    # more likely a unit error than an insight.
    clamp = None
    if history:
        lo, hi = min(history), max(history)
        span = (hi - lo) or abs(hi) or 1.0
        floor, ceiling = lo - span, hi + span
        if combined < floor or combined > ceiling:
            clamp = {"from": round(combined, 4), "floor": round(floor, 4),
                     "ceiling": round(ceiling, 4)}
            combined = min(max(combined, floor), ceiling)

    return {
        "value": round(combined, 4),
        "method_values": {m: round(v, 4) for m, v in values.items()},
        "weights": {m: round(w, 3) for m, w in weights.items()},
        "median_of_methods": round(ref, 4),
        "outliers": outliers,
        "anchor": anchor_pull,
        "clamped": clamp,
        "agreement": round(1.0 - min(
            _relative_spread(max(values.values()), ref),
            1.0,
        ), 3),
    }


def _normalise(label: str) -> str:
    """Strip decoration so 'Revenue (USDm, basis: reported)' matches 'Revenue'."""
    label = str(label).split("(")[0]
    return "".join(ch for ch in label.lower() if ch.isalnum())


def align_label(returned: str, canonical: list[str]) -> str | None:
    """Map whatever a model returned onto the canonical metric label.

    Prompts drift no matter how firmly they are worded, and a label mismatch silently
    drops a metric - which scores 5.0. This is the deterministic backstop.
    """
    target = _normalise(returned)
    if not target:
        return None
    for label in canonical:
        if _normalise(label) == target:
            return label
    for label in canonical:
        norm = _normalise(label)
        if target.startswith(norm) or norm.startswith(target):
            return label
    return None


def align_all(by_label: dict, canonical: list[str]) -> dict:
    """Re-key a {label: ...} mapping onto canonical labels, dropping unmatchable keys."""
    out: dict = {}
    for key, value in by_label.items():
        matched = align_label(key, canonical)
        if matched is None:
            continue
        if isinstance(value, dict) and isinstance(out.get(matched), dict):
            out[matched].update(value)
        else:
            out[matched] = value
    return out


def pick_anchor(anchors: list[dict], metric_label: str) -> dict | None:
    """Best anchor for a metric: company guidance beats consensus beats last actual."""
    ranked = {"guidance": 3, "consensus": 2, "last_actual": 1, "derived": 0}
    candidates = [a for a in anchors if align_label(a.get("metric", ""), [metric_label])
                  and isinstance(a.get("value"), (int, float))]
    if not candidates:
        return None
    return max(candidates, key=lambda a: ranked.get(a.get("kind", "derived"), 0))


def history_for(history: list[dict], metric_label: str) -> list[float]:
    return [
        float(h["value"]) for h in history
        if align_label(h.get("metric", ""), [metric_label]) and isinstance(h.get("value"), (int, float))
    ]
