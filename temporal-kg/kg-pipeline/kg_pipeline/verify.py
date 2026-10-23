"""Drop-and-count sanitation: never discard quietly.

Adapters emit their best-effort Documents; the pipeline stage then removes
invalid entities/quads (surface not in text, dangling spans, bad dates) while
recording why, so dataset-quality reports are exact rather than vibes.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Iterable, List, Optional, Tuple

from .schema import Document, SchemaError


@dataclass
class SanitizeReport:
    docs_in: int = 0
    docs_out: int = 0
    entity_drops: int = 0
    quad_drops: int = 0
    doc_drops: int = 0
    reasons: Counter = field(default_factory=Counter)

    def add(self, reason: str) -> None:
        self.reasons[reason] += 1

    def summary(self) -> str:
        reasons = ", ".join(f"{k}={v}" for k, v in sorted(self.reasons.items())) or "none"
        return (
            f"docs {self.docs_in}->{self.docs_out} "
            f"(dropped {self.doc_drops}), entity_drops={self.entity_drops}, "
            f"quad_drops={self.quad_drops}; reasons: {reasons}"
        )


def sanitize_doc(doc: Document, report: SanitizeReport) -> Optional[Document]:
    report.docs_in += 1
    if not doc.text or not doc.text.strip():
        report.doc_drops += 1
        report.add("empty-text")
        return None

    entities = []
    for e in doc.entities:
        try:
            e.validate(doc.text)
            entities.append(e)
        except SchemaError:
            report.entity_drops += 1
            report.add("invalid-entity-span")

    kept_spans = {(e.span_start, e.span_end) for e in entities}
    quads = []
    for q in doc.quads:
        if tuple(q.head_span) not in kept_spans or tuple(q.tail_span) not in kept_spans:
            report.quad_drops += 1
            report.add("quad-span-not-an-entity")
            continue
        try:
            q.validate(doc.text)
            quads.append(q)
        except SchemaError:
            report.quad_drops += 1
            report.add("invalid-quad")

    cleaned = replace(doc, entities=entities, quads=quads)
    try:
        cleaned.validate()
    except SchemaError as exc:
        report.doc_drops += 1
        report.add(f"invalid-document: {exc}")
        return None
    report.docs_out += 1
    return cleaned


def sanitize_docs(docs: Iterable[Document]) -> Tuple[List[Document], SanitizeReport]:
    report = SanitizeReport()
    out = []
    for doc in docs:
        cleaned = sanitize_doc(doc, report)
        if cleaned is not None:
            out.append(cleaned)
    return out, report


def dedup_quads(docs: List[Document]) -> Tuple[List[Document], int]:
    """Drop duplicate (head, rel, tail, time) quads across the whole corpus."""
    seen = set()
    removed = 0
    for doc in docs:
        kept = []
        for q in doc.quads:
            key = (
                tuple(q.head_span),
                q.rel,
                tuple(q.tail_span),
                q.head_id,
                q.tail_id,
                json.dumps(q.time.to_dict(), sort_keys=True) if q.time else None,
            )
            if key in seen:
                removed += 1
                continue
            seen.add(key)
            kept.append(q)
        doc.quads = kept
    return docs, removed
