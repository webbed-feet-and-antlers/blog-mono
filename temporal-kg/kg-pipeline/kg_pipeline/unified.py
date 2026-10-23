"""Dual output: canonical Documents -> the raw formats UnifiedDataset parses.

The trainer (Ideal_KG_Training_new notebook) already knows how to ingest:
  NER: {"text": ..., "entities": [{"entity": ..., "type": ...}]}
  RE:  DocRED-style {"sents": [[tok, ...]], "labels": [{"h","r","t"}], "vertexSet": [...]}
  EL:  {"text": ..., "mentions": [{"text", "kb_id", "label"}]}

so adapters that produce canonical Documents get into training with zero
trainer changes. The RE reconstruction the notebook performs is
``" ".join(" ".join(s) for s in sents)``, which normalizes whitespace — we
pre-normalize the same way so surfaces always survive the round trip.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from .paths import safe_out_dir
from .schema import Document


def to_unified_ner(doc: Document) -> dict:
    return {
        "doc_id": doc.doc_id,
        "text": doc.text,
        "entities": [{"entity": e.surface, "type": e.ner_label} for e in doc.entities],
    }


def to_unified_re(doc: Document) -> dict:
    tokens = doc.text.split()
    vertex_set: List[list] = []
    name_to_vertex: Dict[str, int] = {}

    def vertex_for(surface: str, ner_label: str) -> int:
        # DocRED vertices are coreference clusters: same surface -> same vertex.
        if surface not in name_to_vertex:
            name_to_vertex[surface] = len(vertex_set)
            vertex_set.append([{"name": surface, "type": ner_label}])
        return name_to_vertex[surface]

    span_to_vertex = {}
    for e in doc.entities:
        idx = vertex_for(e.surface, e.ner_label)
        span_to_vertex[(e.span_start, e.span_end)] = idx

    labels = []
    for q in doc.quads:
        h = span_to_vertex.get(tuple(q.head_span))
        if h is None:
            surface = doc.text[q.head_span[0]:q.head_span[1]]
            h = vertex_for(surface, "ENTITY")
        t = span_to_vertex.get(tuple(q.tail_span))
        if t is None:
            surface = doc.text[q.tail_span[0]:q.tail_span[1]]
            t = vertex_for(surface, "ENTITY")
        labels.append({"h": h, "r": q.rel, "t": t})

    return {"doc_id": doc.doc_id, "sents": [tokens], "labels": labels, "vertexSet": vertex_set}


def to_unified_el(doc: Document) -> Optional[dict]:
    mentions = [
        {"text": e.surface, "kb_id": e.el_id, "label": e.surface}
        for e in doc.entities
        if e.el_id
    ]
    if not mentions:
        return None
    return {"doc_id": doc.doc_id, "text": doc.text, "mentions": mentions}


def write_unified(docs: List[Document], out_dir, allowed_root) -> Dict[str, Path]:
    out = safe_out_dir(out_dir, allowed_root)
    out.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, records in (
        ("ner", [to_unified_ner(d) for d in docs]),
        ("re", [to_unified_re(d) for d in docs]),
        ("el", [r for d in docs if (r := to_unified_el(d)) is not None]),
    ):
        path = out / f"{name}.jsonl"
        payload = "".join(json.dumps(rec, ensure_ascii=False) + "\n" for rec in records)
        path.write_text(payload, encoding="utf-8")
        paths[name] = path
    return paths
