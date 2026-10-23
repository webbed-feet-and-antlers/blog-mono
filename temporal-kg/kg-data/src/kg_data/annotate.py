"""Stage 3 — gold alignment.

Locates head/tail mentions verbatim in rendered text (span verification),
builds gold quads with temporal bounds, and reports what couldn't be aligned.
Pure code — no LLM.
"""

from __future__ import annotations

from datetime import date

from .schema import Entity, Quad, Segment, World

Span = tuple[int, int]


def find_mention(text: str, entity: Entity, after: int = 0) -> Span | None:
    """First verbatim occurrence of an entity name/alias at/after `after`;
    exact case first. Used with a rising `after` cursor to build a
    non-overlapping span assignment."""
    for needle in [entity.name, *entity.aliases]:
        idx = text.find(needle, after)
        if idx != -1:
            return idx, idx + len(needle)
    low = text.lower()
    for needle in [entity.name, *entity.aliases]:
        idx = low.find(needle.lower(), after)
        if idx != -1:
            return idx, idx + len(needle)
    return None


def segments_as_of(world: World, at: date) -> list[Segment]:
    """All segments active at `at` across every timeline."""
    out: list[Segment] = []
    for tl in world.timelines:
        for seg in tl.segments:
            if seg.valid_from <= at and (seg.valid_to is None or at < seg.valid_to):
                out.append(seg)
    return out


def next_quads(
    world: World, facts: list[dict], doc_id: str, rev_id: str, text: str,
    spans_by_entity: dict[str, Span], *, start_index: int,
) -> tuple[list[Quad], int]:
    """Build quads for facts whose head+tail spans were located.

    `facts` are dicts with keys: predicate_id, head_id, tail_id (the claim),
    valid_from, valid_to, time_source, claim_status. Returns quads plus the
    next quad id index.
    """
    quads: list[Quad] = []
    n = start_index
    for fact in facts:
        head_span = spans_by_entity.get(fact["head_id"])
        tail_span = spans_by_entity.get(fact["tail_id"])
        if head_span is None or tail_span is None:
            continue
        conflict = fact.get("claim_status", "accurate") == "conflict_injected"
        quads.append(Quad(
            id=f"q{n}", world_id=world.id, doc_id=doc_id, rev_id=rev_id,
            head=_mention(world, fact["head_id"], text, head_span),
            tail=_mention(world, fact["tail_id"], text, tail_span),
            relation=fact["predicate_id"],
            valid_from=None if conflict else fact.get("valid_from"),
            valid_to=None,
            time_source="none" if conflict
            else fact.get("time_source", "doc_date"),
            claim_status=fact.get("claim_status", "accurate"),
        ))
        n += 1
    return quads, n


def _mention(world: World, entity_id: str, text: str, span: Span):
    from .schema import Mention

    return Mention(
        entity_id=entity_id, text=text[span[0]:span[1]],
        start=span[0], end=span[1],
    )
