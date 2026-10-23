"""kg-data bridge — kg-data's stage-7 export as canonical Documents.

Convergence decision (2026-10-23): kg-data is the single home for the
synthetic-enterprise generator (worldgen/docgen/messify/evalgen) AND for
T-REx/Wikidata fetching+enrichment; this adapter is the only seam between
the projects. It reads kg-data's exported JSONLs and emits
kg_pipeline.schema.Documents, so the trainer consumes synthetic enterprise
text, real Wikipedia text, and ICEWS news events through one contract.

Layouts handled by load_input():
  synthetic root   (contains worlds.jsonl + corpus/{split} + gold/{split})
  public root      (data/public/<trex|wikidata>/{split}/{docs,quads}.jsonl)
  merged file      JSONL of {"kind", "doc", "quads", "entity_types"} records

Each doc REVISION becomes one canonical Document (its own text, doc_time =
revision sim_time, quads as claimed in that revision). Synthetic predicates
namespace into COMPANY:...; Wikidata P-ids pass through bare. time_source
"explicit_in_text" is the only one that sets explicit_in_text=True.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kg_pipeline.labels import namespace_rel  # noqa: E402
from kg_pipeline.schema import Document, Entity, Provenance, Quad, TimeBound  # noqa: E402
from kg_pipeline.timeutil import normalize_time  # noqa: E402

SOURCE = "kgdata"

KIND_LICENSE = {
    "synthetic": "generated corpus (kg-data worldgen, in-repo)",
    "trex": "T-REx / Wikipedia derived (CC BY-SA 4.0)",
    "wikidata": "Wikidata / Wikipedia derived (CC BY-SA 4.0)",
}
KIND_EXTRACTION = {"synthetic": "synthetic", "trex": "distant", "wikidata": "distant"}
_P_RE = re.compile(r"^P\d+$")


def _read_jsonl(path: Path) -> list:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _entity_types_from_worlds(worlds: list) -> Dict[str, Tuple[str, str]]:
    """{entity_id: (type, split)} across all worlds."""
    out = {}
    for w in worlds:
        split = w.get("split", "train")
        for e in w.get("entities", []):
            out[e["id"]] = (e.get("type", "entity"), split)
    return out


def load_input(path, split: Optional[str] = None) -> List[dict]:
    """Merge a kg-data export into normalized-ready records.

    split filters synthetic worlds / public splits; None merges everything.
    """
    p = Path(path)
    if p.is_file():
        records = _read_jsonl(p)
        if split is not None:
            raise SystemExit(f"--split requires an export directory, not a file: {p}")
        return records

    if not p.is_dir():
        raise SystemExit(f"input path is neither file nor directory: {p}")

    if (p / "worlds.jsonl").exists() or (p / "corpus").is_dir():  # synthetic root
        worlds = _read_jsonl(p / "worlds.jsonl") if (p / "worlds.jsonl").exists() else []
        etypes = _entity_types_from_worlds(worlds)
        if split is not None:
            wids = {w["id"] for w in worlds if w.get("split") == split}
        else:
            wids = {w["id"] for w in worlds}
        records = []
        for sp in ([split] if split else ["train", "val", "test"]):
            docs_path = p / "corpus" / sp / "docs.jsonl"
            quads_path = p / "gold" / sp / "quads.jsonl"
            if not docs_path.exists():
                continue
            quads_by_doc = defaultdict(list)
            for q in _read_jsonl(quads_path) if quads_path.exists() else []:
                quads_by_doc[q["doc_id"]].append(q)
            for d in _read_jsonl(docs_path):
                if wids and d["world_id"] not in wids:
                    continue
                records.append({
                    "kind": "synthetic",
                    "doc": d,
                    "quads": quads_by_doc.get(d["id"], []),
                    "entity_types": {eid: t for eid, (t, _) in etypes.items()},
                })
        return records

    if (p / "train").is_dir() or (p / "val").is_dir():  # public export root
        kind = p.name if p.name in KIND_LICENSE else "trex"
        records = []
        for sp in ([split] if split else ["train", "val", "test"]):
            docs_path = p / sp / "docs.jsonl"
            quads_path = p / sp / "quads.jsonl"
            if not docs_path.exists():
                continue
            quads_by_doc = defaultdict(list)
            for q in _read_jsonl(quads_path) if quads_path.exists() else []:
                quads_by_doc[q["doc_id"]].append(q)
            for d in _read_jsonl(docs_path):
                records.append({"kind": kind, "doc": d, "quads": quads_by_doc.get(d["id"], [])})
        return records

    raise SystemExit(f"unrecognized kg-data export layout at {p}")


def _mention_to_entity(m: dict, text: str, etypes: dict) -> Optional[Entity]:
    surface = m.get("text", "")
    start, end = m.get("start"), m.get("end")
    if start is None or end is None or not surface:
        return None
    ner = etypes.get(m.get("entity_id", ""), "entity")
    return Entity(int(start), int(end), surface, str(ner).upper(), m.get("entity_id"))


def _quad_time(q: dict) -> Optional[TimeBound]:
    from_raw, _ = normalize_time(q.get("valid_from"))
    to_raw, _ = normalize_time(q.get("valid_to"))
    if from_raw is None and to_raw is None:
        return None
    explicit = q.get("time_source") == "explicit_in_text"
    return TimeBound(from_raw, to_raw, "day", explicit)


def _canonical_quad(q: dict, synthetic: bool) -> Tuple[Optional[Quad], Optional[str]]:
    """Returns (quad, None) or (None, issue_key) for drop accounting."""
    head, tail = q.get("head", {}), q.get("tail", {})
    if head.get("start") is None or tail.get("start") is None:
        return None, "unusable-quad"
    rel = str(q.get("relation", "")).strip()
    if not rel:
        return None, "unusable-quad"
    if _P_RE.match(rel):
        namespaced = rel  # Wikidata P-id: passes through bare
    elif synthetic:
        namespaced = namespace_rel(rel, family="company")
    elif re.search(r"\s", rel):
        # relbert/t_rex mixes in relation templates like "[City] is in
        # [Country]" — not P-ids, not trainable labels; skip + count.
        return None, "template-relation"
    else:
        namespaced = rel
    return Quad(
        [int(head["start"]), int(head["end"])],
        namespaced,
        [int(tail["start"]), int(tail["end"])],
        head.get("entity_id"),
        tail.get("entity_id"),
        _quad_time(q),
        q.get("claim_status"),
    ), None


def normalize(records):
    docs: List[Document] = []
    issues: Counter = Counter()

    for i, rec in enumerate(records):
        if not isinstance(rec, dict) or "doc" not in rec:
            issues["malformed-record"] += 1
            continue
        d = rec["doc"]
        quads = rec.get("quads", [])
        etypes = rec.get("entity_types") or {}
        kind = rec.get("kind", "synthetic")
        license_ = KIND_LICENSE.get(kind, KIND_LICENSE["synthetic"])
        extraction = KIND_EXTRACTION.get(kind, "synthetic")

        by_rev: Dict[str, list] = defaultdict(list)
        for q in quads:
            by_rev[q.get("rev_id")].append(q)

        for rev in d.get("revisions", []):
            text = rev.get("text", "")
            if not text:
                issues["empty-revision"] += 1
                continue
            entities: list = []
            seen_spans = set()
            rev_quads = []
            for q in by_rev.get(rev.get("rev_id"), []):
                cq, why = _canonical_quad(q, synthetic=(kind == "synthetic"))
                if cq is None:
                    issues[why] += 1
                    continue
                rev_quads.append(cq)
                for m in (q.get("head", {}), q.get("tail", {})):
                    span = (m.get("start"), m.get("end"))
                    if span in seen_spans:
                        continue
                    ent = _mention_to_entity(m, text, etypes)
                    if ent is not None:
                        seen_spans.add(span)
                        entities.append(ent)

            doc_id = f"{d.get('id', 'kgdata')}-{rev.get('rev_id', i)}"
            docs.append(Document(
                doc_id=doc_id,
                source=SOURCE,
                channel=d.get("channel", "doc"),
                text=text,
                doc_time=rev.get("sim_time"),
                entities=entities,
                quads=rev_quads,
                provenance=Provenance(license_, str(d.get("id", i)), extraction),
            ))

    return docs, dict(issues)
