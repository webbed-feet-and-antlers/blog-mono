"""T-REx -> canonical Documents.

Accepts the canonical T-REx shape (per-property instances with aligned
entity spans) plus common mirror variants. Triples whose subject/object QIDs
have no aligned span in the text are skipped and counted — that is classic
distant-supervision noise, not a bug, and the counts feed the quality report.

Temporal bounds come from a qualifier cache (P580 start / P582 end), never
guessed from the abstract.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kg_pipeline.schema import Document, Entity, Provenance, Quad, TimeBound  # noqa: E402
from kg_pipeline.timeutil import coarser_granularity, normalize_time  # noqa: E402
from adapters.trex.qualifiers import QualifierLookup  # noqa: E402

SOURCE = "trex"
LICENSE = "CC BY-SA 4.0 (Wikipedia/Wikidata derived)"


def _first(d: dict, *keys):
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return None


def _parse_entity(e) -> Tuple[str, int, int, Optional[str]]:
    if isinstance(e, dict):
        surface = _first(e, "surface", "mention", "name", "entity")
        start = _first(e, "span_start", "start", "offset_start")
        end = _first(e, "span_end", "end", "offset_end")
        qid = _first(e, "el_id", "kb_id", "qid", "uri", "id")
    elif isinstance(e, (list, tuple)) and len(e) >= 4:
        surface, start, end, qid = e[0], e[1], e[2], e[3]
    elif isinstance(e, (list, tuple)) and len(e) == 3:
        surface, start, end, qid = e[0], e[1], e[2], None
    else:
        raise ValueError(f"unrecognized entity record {e!r}")
    if surface is None or start is None or end is None:
        raise ValueError(f"entity record missing surface or span: {e!r}")
    return str(surface), int(start), int(end), (str(qid) if qid else None)


def _parse_triples(t) -> List[Tuple[str, str, str]]:
    if not t:
        return []
    if isinstance(t, dict):
        t = [t]
    out = []
    for tr in t:
        if isinstance(tr, (list, tuple)) and len(tr) >= 3:
            s, p, o = str(tr[0]), str(tr[1]), str(tr[2])
        elif isinstance(tr, dict):
            s = _first(tr, "subject", "s", "head")
            p = _first(tr, "predicate", "p", "rel", "relation")
            o = _first(tr, "object", "o", "tail")
            if not (s and p and o):
                continue
            s, p, o = str(s), str(p), str(o)
        else:
            continue
        out.append((s, p, o))
    return out


def _time_from_qualifiers(raw: Optional[dict]) -> Optional[TimeBound]:
    if not raw:
        return None
    try:
        from_, _ = normalize_time(_first(raw, "from", "from_date", "P580"))
        to, _ = normalize_time(_first(raw, "to", "to_date", "P582"))
    except ValueError:
        return None
    if from_ is None and to is None:
        return None
    granularity = coarser_granularity(*[v for v in (from_, to) if v])
    # T-REx times come from Wikidata qualifiers, not from the abstract text.
    return TimeBound(from_, to, granularity, explicit_in_text=False)


def normalize(records, qualifier_lookup: Optional[QualifierLookup] = None):
    docs: List[Document] = []
    issues: Counter = Counter()

    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            issues["non-dict-record"] += 1
            continue
        text = _first(rec, "text", "content")
        if not text:
            issues["no-text"] += 1
            continue

        entities: List[Entity] = []
        for e in rec.get("entities") or []:
            try:
                surface, start, end, qid = _parse_entity(e)
                entities.append(Entity(start, end, surface, "ENTITY", qid))
            except (ValueError, TypeError):
                issues["bad-entity-record"] += 1

        qid_to_span = {e.el_id: [e.span_start, e.span_end] for e in entities if e.el_id}

        quads: List[Quad] = []
        for s, p, o in _parse_triples(rec.get("triples")):
            head_span = qid_to_span.get(s)
            tail_span = qid_to_span.get(o)
            if head_span is None or tail_span is None:
                issues["triple-without-spans"] += 1
                continue
            time = _time_from_qualifiers(qualifier_lookup.get((s, p, o)) if qualifier_lookup else None)
            quads.append(Quad(head_span, p, tail_span, s, o, time))

        doc_id = str(_first(rec, "docid", "doc_id", "id") or f"trex-{i}")
        docs.append(
            Document(
                doc_id=doc_id,
                source=SOURCE,
                channel="wiki",
                text=text,
                doc_time=None,
                entities=entities,
                quads=quads,
                provenance=Provenance(LICENSE, doc_id, "distant"),
            )
        )

    return docs, dict(issues)


def make_lookup(path) -> QualifierLookup:
    return QualifierLookup.from_json(Path(path))
