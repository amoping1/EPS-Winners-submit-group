"""Orchestrator: evidence -> forecasts -> critique -> reconcile -> validated numbers.

Companies run concurrently. The final-run window is 45 minutes and a retry has to fit
inside it, so wall-clock matters more than tidiness here.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from .agents.forecast import run_critic, run_forecaster
from .agents.profiles import PROFILES, classify
from .agents.research import run_research
from .rails.reconcile import align_all, history_for, pick_anchor, reconcile_metric
from .tools.documents import DocumentTools

METHODS = ("guidance", "statistical", "qualitative")


def classify_company(corpus_root: str, company: dict, as_of):
    """Pick an IndustryProfile from filing text. No hardcoded ticker->industry map."""
    tools = DocumentTools(corpus_root, company["corpusDir"], as_of=as_of)
    text = ""
    for entry in tools.list_index(doc_type="Filing", limit=3):
        text += tools.read_document(entry.doc_id, window=12_000)["text"]
    profile, confidence, hits = classify(text)
    return profile, confidence, hits


def run_company(client, corpus_root: str, company: dict, as_of, max_steps: int = 20) -> dict:
    """Full chain for one company. Returns forecasts plus the full audit trail."""
    started = time.time()
    labels = [m["label"] for m in company["metrics"]]

    profile, confidence, hits = classify_company(corpus_root, company, as_of)
    pack = run_research(client, corpus_root, company, profile, as_of,
                        max_steps=max_steps, verbose=False)

    proposals: dict[str, dict] = {}
    for method in METHODS:
        aligned = align_all(run_forecaster(client, company, pack, method), labels)
        for label, forecast in aligned.items():
            proposals.setdefault(label, {})[method] = forecast

    verdicts = align_all(run_critic(client, company, pack, proposals), labels)

    results = {}
    for metric in company["metrics"]:
        label = metric["label"]
        results[label] = reconcile_metric(
            proposals.get(label, {}),
            pick_anchor(pack.anchors, label),
            history_for(pack.history, label),
            target_period=company["period"],
        )
        results[label]["verdict"] = verdicts.get(label)
        results[label]["units"] = metric["units"]
        results[label]["basis"] = metric.get("basis")

    return {
        "ticker": company["ticker"],
        "company": company["company"],
        "period": company["period"],
        "profile": profile.key,
        "profile_confidence": confidence,
        "profile_signals": hits,
        "elapsed_s": round(time.time() - started, 1),
        "tool_calls": len(pack.trace),
        "history_rows": len(pack.history),
        "anchors": pack.anchors,
        "gaps": pack.gaps,
        "consensus": pack.consensus,
        "results": results,
        "evidence": pack.to_dict(),
    }


def run_all(client, corpus_root: str, companies: list[dict], as_of, max_steps: int = 20,
            workers: int = 4) -> list[dict]:
    """All four companies concurrently."""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(run_company, client, corpus_root, c, as_of, max_steps)
            for c in companies
        ]
        return [f.result() for f in futures]
