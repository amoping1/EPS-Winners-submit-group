"""Searchable index over the frozen historical document corpus.

The corpus is 1,139 Markdown documents (filings, call transcripts and slide
decks) for the four challenge companies. Every document carries frontmatter with
a ``published_at`` date, which is what makes point-in-time retrieval possible.

Two properties matter more than raw ranking quality:

* **Nothing is returned without a cutoff.** Every entry point calls
  :func:`src.asof.get_guard`, which raises when no cutoff is configured, and the
  final result set is audited a second time by :func:`src.asof.assert_no_leak`.
* **Every hit carries provenance.** A hit knows its document path, publication
  date, document type, reporting period and the verbatim text it matched, so a
  forecast can always be traced back to a source a judge can open.

ripgrep is not available on the build machine, so the index is built in Python
and cached on disk between runs.
"""

from __future__ import annotations

import json
import math
import pickle
import re
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import asof
from .asof import parse_published_at
from .config import PATHS, Company, load_companies
from .errors import ConfigurationError

# --------------------------------------------------------------------------
# Text handling
# --------------------------------------------------------------------------

WORD_RE = re.compile(r"[a-z0-9][a-z0-9.'\-]*", re.IGNORECASE)

# Figures worth surfacing alongside an excerpt: currency amounts, percentages,
# per-share values and plain magnitudes.
NUMBER_RE = re.compile(
    r"(?<![\w])(?:[$£€]\s*)?\(?-?\d(?:[\d,]*\d)?(?:\.\d+)?\)?"
    r"(?:\s*(?:%|percent|million|billion|bn|m\b|pence|p\b|per\s+share))?",
    re.IGNORECASE,
)

STOP_WORDS = frozenset(
    """
    a an and are as at be been but by for from has have how in into is it its of on or
    that the their there these this to was were what when which who will with
    """.split()
)

HEADING_RE = re.compile(r"^(#{1,4})\s+(.*\S)\s*$")

CHUNK_TARGET_CHARS = 1600
CHUNK_MAX_CHARS = 2600

# Converted filings carry zero-width spaces and table padding that survive as
# tokens and inflate scores without carrying meaning.
NOISE_CHARS = str.maketrans({"​": "", "﻿": "", " ": " "})

# A passage must be at least this dense in letters and digits to be indexed.
# Financial tables are valuable and pass comfortably; grids of "| --- |"
# separators do not.
MIN_ALNUM_RATIO = 0.30
MIN_INFORMATIVE_CHARS = 60

INDEX_VERSION = 4


def normalise_text(text: str) -> str:
    """Strip invisible characters that would otherwise become search tokens."""
    return text.translate(NOISE_CHARS)


def is_informative(text: str) -> bool:
    """Reject passages that are mostly table scaffolding rather than content."""
    if len(text) < MIN_INFORMATIVE_CHARS:
        return False
    alnum = sum(1 for character in text if character.isalnum())
    return alnum / len(text) >= MIN_ALNUM_RATIO


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens, stopwords removed, numbers preserved."""
    return [
        token
        for token in (match.group(0).lower() for match in WORD_RE.finditer(text))
        if token not in STOP_WORDS and len(token) > 1
    ]


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse the JSON-compatible YAML header used by the supplied documents."""
    if not text.startswith("---\n"):
        return {}, text
    marker = text.find("\n---\n", 4)
    if marker == -1:
        return {}, text
    metadata: dict[str, Any] = {}
    for line in text[4:marker].splitlines():
        if ":" not in line:
            continue
        key, _, raw_value = line.partition(":")
        raw_value = raw_value.strip()
        try:
            metadata[key.strip()] = json.loads(raw_value)
        except json.JSONDecodeError:
            metadata[key.strip()] = raw_value
    return metadata, text[marker + 5 :]


def extract_numbers(text: str, limit: int = 12) -> tuple[str, ...]:
    """Pull candidate figures out of an excerpt, for quick human scanning."""
    found: list[str] = []
    seen: set[str] = set()
    for match in NUMBER_RE.finditer(text):
        value = match.group(0).strip()
        if len(value) < 2 or value in seen:
            continue
        seen.add(value)
        found.append(value)
        if len(found) >= limit:
            break
    return tuple(found)


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Document:
    """One corpus document and its provenance."""

    doc_id: str
    rel_path: str
    company: str
    ticker: str
    company_slug: str
    published_at: date | None
    document_type: str
    period: str
    title: str
    source_url: str | None

    @property
    def path(self) -> Path:
        return PATHS.root / self.rel_path

    def citation(self) -> dict[str, Any]:
        """The provenance block every downstream claim must carry."""
        return {
            "doc_id": self.doc_id,
            "path": self.rel_path,
            "company": self.company_slug,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "document_type": self.document_type,
            "period": self.period,
            "title": self.title,
            "source_url": self.source_url,
        }


@dataclass(frozen=True)
class Chunk:
    """A passage of a document, the unit that is searched and cited."""

    chunk_id: int
    doc_id: str
    ordinal: int
    heading: str
    text: str


@dataclass(frozen=True)
class SearchHit:
    """A ranked passage with everything needed to cite and verify it."""

    document: Document
    chunk: Chunk
    score: float
    numbers: tuple[str, ...]

    def as_dict(self, excerpt_chars: int = 900) -> dict[str, Any]:
        excerpt = self.chunk.text
        if len(excerpt) > excerpt_chars:
            excerpt = excerpt[:excerpt_chars].rstrip() + "..."
        return {
            **self.document.citation(),
            "heading": self.chunk.heading,
            "score": round(self.score, 4),
            "excerpt": excerpt,
            "numbers": list(self.numbers),
        }


# --------------------------------------------------------------------------
# Index
# --------------------------------------------------------------------------


def _iter_chunks(body: str) -> Iterable[tuple[str, str]]:
    """Split a document body into passages, remembering the heading in force."""
    heading = ""
    buffer: list[str] = []
    size = 0

    def flush() -> Iterable[tuple[str, str]]:
        nonlocal buffer, size
        if buffer:
            text = "\n".join(buffer).strip()
            if text:
                yield heading, text
        buffer = []
        size = 0

    for line in body.splitlines():
        match = HEADING_RE.match(line)
        if match:
            yield from flush()
            heading = match.group(2)
            continue
        stripped = line.rstrip()
        if not stripped and size >= CHUNK_TARGET_CHARS:
            yield from flush()
            continue
        buffer.append(stripped)
        size += len(stripped) + 1
        if size >= CHUNK_MAX_CHARS:
            yield from flush()
    yield from flush()


class CorpusIndex:
    """BM25 index over document passages, with mandatory point-in-time filtering."""

    K1 = 1.5
    B = 0.75

    def __init__(self) -> None:
        self.documents: dict[str, Document] = {}
        self.chunks: list[Chunk] = []
        self.postings: dict[str, list[tuple[int, int]]] = {}
        self.chunk_lengths: list[int] = []
        self.average_length: float = 0.0
        self._doc_chunks: dict[str, list[int]] = defaultdict(list)

    # -- construction ------------------------------------------------------

    @classmethod
    def build(cls, companies: Sequence[Company] | None = None) -> "CorpusIndex":
        index = cls()
        pool = companies if companies is not None else load_companies()
        postings: dict[str, list[tuple[int, int]]] = defaultdict(list)

        for company in pool:
            directory = company.corpus_dir
            if not directory.exists():
                raise ConfigurationError(f"Missing corpus folder: {directory}")
            for path in sorted(directory.rglob("*.md")):
                if path.name.upper() == "INDEX.MD":
                    continue
                index._ingest(path, company, postings)

        index.postings = {term: sorted(entries) for term, entries in postings.items()}
        index.average_length = (
            sum(index.chunk_lengths) / len(index.chunk_lengths) if index.chunk_lengths else 0.0
        )
        return index

    def _ingest(
        self,
        path: Path,
        company: Company,
        postings: dict[str, list[tuple[int, int]]],
    ) -> None:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        metadata, body = parse_frontmatter(normalise_text(raw))

        title = ""
        for line in body.splitlines():
            match = HEADING_RE.match(line)
            if match:
                title = match.group(2)
                break

        rel_path = path.relative_to(PATHS.root).as_posix()
        document = Document(
            doc_id=path.stem,
            rel_path=rel_path,
            company=str(metadata.get("company") or company.name),
            ticker=str(metadata.get("ticker") or company.ticker),
            company_slug=company.slug,
            published_at=parse_published_at(metadata.get("published_at")),
            document_type=str(metadata.get("document_type") or "UNKNOWN").upper(),
            period=str(metadata.get("period") or ""),
            title=title or path.stem,
            source_url=metadata.get("source_url") or None,
        )
        self.documents[document.doc_id] = document

        ordinal = 0
        for heading, text in _iter_chunks(body):
            if not is_informative(text):
                continue
            ordinal += 1
            chunk_id = len(self.chunks)
            self.chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    doc_id=document.doc_id,
                    ordinal=ordinal,
                    heading=heading,
                    text=text,
                )
            )
            self._doc_chunks[document.doc_id].append(chunk_id)

            counts: dict[str, int] = defaultdict(int)
            searchable = f"{heading}\n{text}" if heading else text
            for token in tokenize(searchable):
                counts[token] += 1
            self.chunk_lengths.append(sum(counts.values()))
            for term, count in counts.items():
                postings[term].append((chunk_id, count))

    # -- persistence -------------------------------------------------------

    @staticmethod
    def fingerprint(companies: Sequence[Company] | None = None) -> str:
        """Cheap corpus signature: file count, total size and newest mtime."""
        pool = companies if companies is not None else load_companies()
        count = 0
        total = 0
        newest = 0.0
        for company in pool:
            for path in company.corpus_dir.rglob("*.md"):
                stat = path.stat()
                count += 1
                total += stat.st_size
                newest = max(newest, stat.st_mtime)
        return f"v{INDEX_VERSION}:{count}:{total}:{int(newest)}"

    @classmethod
    def load_or_build(
        cls,
        companies: Sequence[Company] | None = None,
        *,
        cache_path: Path | None = None,
        rebuild: bool = False,
    ) -> "CorpusIndex":
        target = cache_path or (PATHS.cache / "corpus-index.pkl")
        signature = cls.fingerprint(companies)
        if not rebuild and target.exists():
            try:
                with target.open("rb") as handle:
                    payload = pickle.load(handle)
                if payload.get("signature") == signature:
                    return payload["index"]
            except (OSError, pickle.PickleError, KeyError, AttributeError):
                pass

        index = cls.build(companies)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("wb") as handle:
                pickle.dump(
                    {"signature": signature, "index": index},
                    handle,
                    protocol=pickle.HIGHEST_PROTOCOL,
                )
        except OSError:
            pass
        return index

    # -- retrieval ---------------------------------------------------------

    def visible_documents(
        self,
        *,
        company: str | None = None,
        document_types: Sequence[str] | None = None,
    ) -> list[Document]:
        """Documents allowed by the active cutoff, newest first."""
        guard = asof.get_guard()
        wanted_types = {t.upper() for t in document_types} if document_types else None
        results = [
            document
            for document in self.documents.values()
            if (company is None or document.company_slug == company.upper())
            and (wanted_types is None or document.document_type in wanted_types)
            and guard.is_allowed(document.published_at, source=document.rel_path)
        ]
        results.sort(key=lambda doc: (doc.published_at or date.min), reverse=True)
        return results

    def get_document(self, doc_id: str) -> Document:
        """Fetch a document by id, refusing anything past the cutoff."""
        document = self.documents.get(doc_id)
        if document is None:
            raise KeyError(f"Unknown document: {doc_id}")
        asof.get_guard().assert_allowed(document.published_at, source=document.rel_path)
        return document

    def read_document(self, doc_id: str) -> str:
        """Full text of a document, cutoff-checked before the file is opened."""
        document = self.get_document(doc_id)
        return document.path.read_text(encoding="utf-8", errors="replace")

    def search(
        self,
        query: str,
        *,
        company: str | None = None,
        document_types: Sequence[str] | None = None,
        period_contains: str | None = None,
        published_after: date | None = None,
        limit: int = 10,
        max_per_document: int = 3,
        recency_halflife_days: float | None = 540.0,
        recency_weight: float = 1.0,
    ) -> list[SearchHit]:
        """Rank passages for ``query`` within the active point-in-time window.

        Relevance alone is not enough for forecasting. Company boilerplate barely
        changes between reports, so a decade of near-identical quarterly
        statements all match the same terms and the newest one -- the only one
        carrying current guidance -- can be buried. A recency multiplier fixes
        that, measured against the cutoff rather than today's date so a backtest
        replay ranks exactly as the competition run would have at that time.

        Pass ``recency_halflife_days=None`` when building a historical series,
        where old documents are the point.
        """
        guard = asof.get_guard()
        terms = tokenize(query)
        if not terms:
            return []

        wanted_types = {t.upper() for t in document_types} if document_types else None
        allowed_docs: set[str] = set()
        for doc_id, document in self.documents.items():
            if company is not None and document.company_slug != company.upper():
                continue
            if wanted_types is not None and document.document_type not in wanted_types:
                continue
            if period_contains and period_contains.lower() not in document.period.lower():
                continue
            if published_after is not None and (
                document.published_at is None or document.published_at < published_after
            ):
                continue
            if not guard.is_allowed(document.published_at, source=document.rel_path):
                continue
            allowed_docs.add(doc_id)

        if not allowed_docs:
            return []

        total_chunks = len(self.chunks)
        scores: dict[int, float] = defaultdict(float)
        for term in set(terms):
            entries = self.postings.get(term)
            if not entries:
                continue
            document_frequency = len(entries)
            idf = math.log(1 + (total_chunks - document_frequency + 0.5) / (document_frequency + 0.5))
            for chunk_id, frequency in entries:
                if self.chunks[chunk_id].doc_id not in allowed_docs:
                    continue
                length = self.chunk_lengths[chunk_id] or 1
                denominator = frequency + self.K1 * (
                    1 - self.B + self.B * length / (self.average_length or 1)
                )
                scores[chunk_id] += idf * (frequency * (self.K1 + 1)) / denominator

        if not scores:
            return []

        # Reward passages containing the query as a phrase; exact wording in a
        # filing is a much stronger signal than scattered term matches.
        phrase = query.strip().lower()
        if len(phrase) > 8:
            for chunk_id in list(scores):
                if phrase in self.chunks[chunk_id].text.lower():
                    scores[chunk_id] *= 1.35

        if recency_halflife_days and recency_weight:
            for chunk_id in list(scores):
                published = self.documents[self.chunks[chunk_id].doc_id].published_at
                if published is None:
                    continue
                age_days = max(0, (guard.as_of - published).days)
                decay = 0.5 ** (age_days / recency_halflife_days)
                scores[chunk_id] *= 1.0 + recency_weight * decay

        ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)

        hits: list[SearchHit] = []
        per_document: dict[str, int] = defaultdict(int)
        for chunk_id, score in ordered:
            chunk = self.chunks[chunk_id]
            if per_document[chunk.doc_id] >= max_per_document:
                continue
            per_document[chunk.doc_id] += 1
            document = self.documents[chunk.doc_id]
            hits.append(
                SearchHit(
                    document=document,
                    chunk=chunk,
                    score=score,
                    numbers=extract_numbers(chunk.text),
                )
            )
            if len(hits) >= limit:
                break

        # Independent audit: filtering deciding correctly and this check agreeing
        # are two different failure modes.
        asof.assert_no_leak(
            hits,
            key=lambda hit: hit.document.published_at,
            source=lambda hit: hit.document.rel_path,
        )
        return hits

    def search_latest(
        self,
        query: str,
        *,
        limit: int = 6,
        candidate_pool: int = 40,
        relevance_floor: float = 0.6,
        **kwargs: Any,
    ) -> list[SearchHit]:
        """Find the most recent passages that genuinely answer the query.

        Guidance is restated almost verbatim in successive filings, so pure
        relevance can return a statement that has since been superseded. Pure
        recency is worse: it returns whatever was published last, answer or not.

        So: take a relevance-ranked pool, discard anything scoring below
        ``relevance_floor`` of the best hit, and only then prefer the newest.
        The floor is what stops a freshly filed 10-Q from displacing the earnings
        release that actually contains the guidance.

        The recency multiplier is switched off for the pool, because letting it
        inflate scores and then sorting by date would count recency twice --
        exactly the effect the floor exists to prevent.
        """
        kwargs.pop("limit", None)
        kwargs["recency_halflife_days"] = None
        pool = self.search(query, limit=candidate_pool, **kwargs)
        if not pool:
            return []
        threshold = max(hit.score for hit in pool) * relevance_floor
        relevant = [hit for hit in pool if hit.score >= threshold]
        relevant.sort(
            key=lambda hit: (hit.document.published_at or date.min, hit.score),
            reverse=True,
        )
        return relevant[:limit]

    # -- reporting ---------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        by_company: dict[str, int] = defaultdict(int)
        by_type: dict[str, int] = defaultdict(int)
        for document in self.documents.values():
            by_company[document.company_slug] += 1
            by_type[document.document_type] += 1
        return {
            "documents": len(self.documents),
            "chunks": len(self.chunks),
            "terms": len(self.postings),
            "average_chunk_tokens": round(self.average_length, 1),
            "by_company": dict(sorted(by_company.items())),
            "by_type": dict(sorted(by_type.items())),
        }

    def __getstate__(self) -> dict[str, Any]:
        state = self.__dict__.copy()
        state["_doc_chunks"] = dict(self._doc_chunks)
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.__dict__.update(state)
        self._doc_chunks = defaultdict(list, state.get("_doc_chunks", {}))


# --------------------------------------------------------------------------
# Process-wide shared index
# --------------------------------------------------------------------------

_index_lock = threading.Lock()
_index: CorpusIndex | None = None


def get_index(*, rebuild: bool = False) -> CorpusIndex:
    """Return the shared corpus index, building or loading it once per process."""
    global _index
    with _index_lock:
        if _index is None or rebuild:
            _index = CorpusIndex.load_or_build(rebuild=rebuild)
        return _index


def reset_index() -> None:
    """Drop the cached index. Used by tests."""
    global _index
    with _index_lock:
        _index = None
