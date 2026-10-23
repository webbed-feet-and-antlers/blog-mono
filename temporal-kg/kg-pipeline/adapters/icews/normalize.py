"""ICEWS -> canonical Documents.

Input: parsed rows from ICEWS .tab files (TSV with header) across release
generations — column names vary, so fields resolve through an alias table
with exact-then-normalized matching. The event sentence is reconstructed
from the coded fields (source actor + CAMEO description + target actor);
no narrative is invented. The date lives in the record, not the text, so
explicit_in_text is computed, not assumed.
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kg_pipeline.labels import namespace_rel  # noqa: E402
from kg_pipeline.schema import Document, Entity, Provenance, Quad, TimeBound  # noqa: E402
from kg_pipeline.timeutil import normalize_time, time_appears_in_text  # noqa: E402

SOURCE = "icews"
LICENSE = "CC BY 4.0 (Harvard Dataverse, DOI 10.7910/DVN/28075 - verify at fetch)"

# field -> candidate column names, tried exact then punctuation-insensitive
FIELDS = {
    "date": ("Event Date", "event_date", "date"),
    "event_id": ("Event ID", "event_id"),
    "sentence": ("Sentence Number", "sentence_number"),
    "src_name": ("Source Actor Name", "sourceActorName", "source_actor_name"),
    "src_code": ("Source Actor Code", "sourceActorCode", "source_actor_code"),
    "tgt_name": ("Target Actor Name", "targetActorName", "target_actor_name"),
    "tgt_code": ("Target Actor Code", "targetActorCode", "target_actor_code"),
    "code": ("Event Code", "CAMEO Code", "event_code", "cameo_code"),
    "desc": ("CAMEO Code Description", "CAMEO Code Description (raw)", "event_text", "cameo_text"),
}

_NORMALIZE = lambda s: re.sub(r"[^a-z0-9]", "", str(s).lower())


def _get(row: dict, *names: str):
    """Exact match first, then alnum-normalized key match."""
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
    normalized = {_NORMALIZE(k): v for k, v in row.items() if isinstance(k, str)}
    for n in names:
        v = normalized.get(_NORMALIZE(n))
        if v not in (None, ""):
            return v
    return None


def normalize(records):
    docs: List[Document] = []
    issues: Counter = Counter()

    for i, row in enumerate(records):
        if not isinstance(row, dict):
            issues["non-dict-row"] += 1
            continue

        src = _get(row, *FIELDS["src_name"])
        tgt = _get(row, *FIELDS["tgt_name"])
        code = _get(row, *FIELDS["code"])
        if not (src and tgt and code):
            issues["missing-actor-or-code"] += 1
            continue

        desc = _get(row, *FIELDS["desc"]) or "interacted with"
        text = f"{src} {desc} {tgt}."

        src_span = (0, len(src))
        desc_end = len(src) + 1 + len(desc)
        tgt_span = (desc_end + 1, desc_end + 1 + len(tgt))

        date_raw = _get(row, *FIELDS["date"])
        try:
            iso, granularity = normalize_time(date_raw) if date_raw else (None, None)
        except ValueError:
            iso, granularity = None, None
            issues["unparseable-date"] += 1

        time: Optional[TimeBound] = None
        if iso:
            time = TimeBound(iso, None, granularity, time_appears_in_text(iso, text))

        event_id = _get(row, *FIELDS["event_id"])
        sentence = _get(row, *FIELDS["sentence"])
        doc_id = f"{event_id or 'icews'}-{sentence or i}"

        docs.append(
            Document(
                doc_id=doc_id,
                source=SOURCE,
                channel="news",
                text=text,
                doc_time=iso,
                entities=[
                    Entity(src_span[0], src_span[1], src, "ACTOR", None),
                    Entity(tgt_span[0], tgt_span[1], tgt, "ACTOR", None),
                ],
                quads=[
                    Quad(
                        list(src_span),
                        namespace_rel(str(code), family="cameo"),
                        list(tgt_span),
                        _get(row, *FIELDS["src_code"]),
                        _get(row, *FIELDS["tgt_code"]),
                        time,
                    )
                ],
                provenance=Provenance(LICENSE, str(event_id or i), "gold"),
            )
        )

    return docs, dict(issues)


def parse_tab(path) -> Tuple[List[dict], int]:
    """Parse an ICEWS .tab (header TSV) into row dicts; returns (rows, skipped)."""
    rows = []
    skipped = 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        header = None
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t")
            if header is None:
                header = parts
                continue
            if len(parts) != len(header):
                skipped += 1
                continue
            rows.append(dict(zip(header, parts)))
    return rows, skipped
