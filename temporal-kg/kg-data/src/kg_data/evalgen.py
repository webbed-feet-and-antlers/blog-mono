"""Stage 4 — eval questions derived purely from the ground-truth timelines.

No LLM involved: every question's expected answer is computed from the
world's segment database, so the eval set is exactly reproducible. Covers
point-in-time, current-state, history, both staleness polarities
(superseded facts AND old-but-still-true durable facts), and injected
conflicts.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from .schema import EvalQuestion, Quad, World

MAX_PER_KIND = {"point_in_time": 4, "current_state": 4, "history": 3,
                "staleness_superseded": 3, "staleness_still_true": 3,
                "conflict": 2}

PHRASE = {
    "founded_in": "What year was {head} founded?",
    "headquartered_in": "Where is {head} headquartered?",
    "vendor_contract_with": "Which vendor does {head} have a contract with?",
    "owned_by_team": "Which team owns {head}?",
    "runs_on_database": "Which database does {head} run on?",
    "deployed_on_cloud": "Which cloud is {head} deployed on?",
    "led_by": "Who leads {head}?",
    "has_ceo": "Who is the CEO of {head}?",
    "member_of_team": "Which team is {head} on?",
    "depends_on": "What does {head} depend on?",
    "runbook_owner": "Who owns the runbook for {head}?",
}


def _name(world: World, entity_id: str) -> str:
    return world.entity(entity_id).name


def _phrase(world: World, predicate_id: str, head_id: str) -> str:
    tmpl = PHRASE.get(predicate_id, "What is the {rel} of {head}?")
    return tmpl.format(head=_name(world, head_id),
                       rel=world.predicate(predicate_id).label)


def _quads_for_segment(world: World, quads: list[Quad], predicate_id: str,
                       head_id: str, tail_id: str) -> list[Quad]:
    return [q for q in quads
            if q.relation == predicate_id and q.head.entity_id == head_id
            and q.tail.entity_id == tail_id and q.claim_status == "accurate"]


def _accurate_for(world: World, quads: list[Quad], predicate_id: str,
                  head_id: str) -> list[Quad]:
    return [q for q in quads
            if q.relation == predicate_id and q.head.entity_id == head_id
            and q.claim_status == "accurate"]


def _doc_date(docs_by_id: dict, quad: Quad) -> date:
    doc = docs_by_id.get(quad.doc_id)
    if doc is None:
        return world_end_fallback
    for rev in doc.revisions:
        if rev.rev_id == quad.rev_id:
            return rev.sim_time
    return doc.created_at


world_end_fallback = date(2026, 6, 30)


def generate_eval_questions(
    world: World, quads: list[Quad], docs: list,
    *, seed: int, max_total: int = 20,
) -> list[EvalQuestion]:
    rng = random.Random(seed ^ 0xE0A1)
    docs_by_id = {d.id: d for d in docs}
    out: list[EvalQuestion] = []
    counts = {k: 0 for k in MAX_PER_KIND}

    def add(kind: str, question: str, as_of: date, answer: str,
            answer_entity_id: str | None, gold_doc_ids: list[str],
            notes: str = "") -> None:
        if counts[kind] >= MAX_PER_KIND[kind] or len(out) >= max_total:
            return
        counts[kind] += 1
        out.append(EvalQuestion(
            id=f"{world.id}-eq{len(out):03d}", world_id=world.id, kind=kind,
            question=question, as_of=as_of, answer=answer,
            answer_entity_id=answer_entity_id, gold_doc_ids=gold_doc_ids,
            notes=notes, split=world.split,
        ))

    # candidates: functional predicates with transitions
    transitioned: list[tuple[str, str]] = []
    for tl in world.timelines:
        pred = world.predicate(tl.predicate_id)
        if not pred.functional:
            continue
        for head_id in {s.head_id for s in tl.segments}:
            segs = sorted(world.segments_for(tl.predicate_id, head_id),
                          key=lambda s: s.valid_from)
            if len(segs) >= 2:
                transitioned.append((tl.predicate_id, head_id))
    rng.shuffle(transitioned)

    for predicate_id, head_id in transitioned:
        pred = world.predicate(predicate_id)
        segs = sorted(world.segments_for(predicate_id, head_id),
                      key=lambda s: s.valid_from)
        head_name = _name(world, head_id)

        # point-in-time: just before a transition, inside the corpus window
        if counts["point_in_time"] < MAX_PER_KIND["point_in_time"]:
            for newer, older in zip(segs[1:], segs[:-1]):
                at = newer.valid_from - timedelta(days=1)
                if at < world.start:  # unobservable in the corpus
                    continue
                gold = _quads_for_segment(world, quads, predicate_id, head_id,
                                          older.tail_id)
                add("point_in_time",
                    f"{_phrase(world, predicate_id, head_id)} (as of "
                    f"{at.isoformat()})",
                    at, _name(world, older.tail_id), older.tail_id,
                    [q.doc_id for q in gold])
                break

        # current state at world end
        if counts["current_state"] < MAX_PER_KIND["current_state"]:
            cur = world.segment_at(predicate_id, head_id, world.end)
            if cur is not None:
                gold = _quads_for_segment(world, quads, predicate_id, head_id,
                                          cur.tail_id)
                add("current_state",
                    f"{_phrase(world, predicate_id, head_id)} (current, "
                    f"{world.end.isoformat()})",
                    world.end, _name(world, cur.tail_id), cur.tail_id,
                    [q.doc_id for q in gold])

        # history: the tail before a boundary
        if counts["history"] < MAX_PER_KIND["history"] and len(segs) >= 2:
            newer = segs[-1]
            older = segs[-2]
            gold = _quads_for_segment(world, quads, predicate_id, head_id,
                                      older.tail_id)
            add("history",
                f"Which {pred.tail_type} did {head_name} have before "
                f"{newer.valid_from.isoformat()} ({pred.label})?",
                newer.valid_from - timedelta(days=1),
                _name(world, older.tail_id), older.tail_id,
                [q.doc_id for q in gold])

        # staleness — superseded: doc states a segment that has since ended
        if counts["staleness_superseded"] < MAX_PER_KIND["staleness_superseded"]:
            ended = [s for s in segs
                     if s.valid_to is not None and s.valid_to <= world.end]
            if ended:
                seg = rng.choice(ended)
                stating = _quads_for_segment(world, quads, predicate_id, head_id,
                                             seg.tail_id)
                if stating:
                    q0 = stating[0]
                    cur = world.segment_at(predicate_id, head_id, world.end)
                    cur_name = _name(world, cur.tail_id) if cur else "unknown"
                    add("staleness_superseded",
                        f"A document from {_doc_date(docs_by_id, q0).isoformat()} "
                        f"says {head_name} {pred.label} {_name(world, seg.tail_id)}. "
                        f"Is that still the case as of {world.end.isoformat()}?",
                        world.end,
                        f"No — {head_name} {pred.label} {cur_name} since "
                        f"{(cur.valid_from if cur else seg.valid_to).isoformat()}",
                        cur.tail_id if cur else None,
                        [q.doc_id for q in stating],
                        notes="superseded claim never retracted in text")

        # staleness — old but still true (durable/immutable facts only)
        if counts["staleness_still_true"] < MAX_PER_KIND["staleness_still_true"] \
                and pred.volatility in ("immutable", "durable"):
            cur = world.segment_at(predicate_id, head_id, world.end)
            if cur is not None:
                stating = _quads_for_segment(world, quads, predicate_id, head_id,
                                             cur.tail_id)
                old = [q for q in stating
                       if _doc_date(docs_by_id, q) < world.end - timedelta(days=365)]
                if old:
                    q0 = old[0]
                    add("staleness_still_true",
                        f"A document from {_doc_date(docs_by_id, q0).isoformat()} "
                        f"says {head_name} {pred.label} "
                        f"{_name(world, cur.tail_id)}. Is that still the case as "
                        f"of {world.end.isoformat()}?",
                        world.end, f"Yes — unchanged since {cur.valid_from.year}.",
                        cur.tail_id, [q.doc_id for q in stating],
                        notes="durable fact; age alone must not invalidate it")

    # conflicts: injected wrong claims vs accurate claims
    conflicts = [q for q in quads if q.claim_status == "conflict_injected"]
    rng.shuffle(conflicts)
    for cq in conflicts:
        if counts["conflict"] >= MAX_PER_KIND["conflict"]:
            break
        accurate = [q for q in _accurate_for(world, quads, cq.relation,
                                             cq.head.entity_id)
                    if q.tail.entity_id != cq.tail.entity_id]
        if not accurate:
            continue
        aq = accurate[0]
        pred = world.predicate(cq.relation)
        head_name = _name(world, cq.head.entity_id)
        add("conflict",
            f"One source says {head_name} {pred.label} "
            f"{cq.tail.text}; another says {_name(world, aq.tail.entity_id)}. "
            f"Which reflects the current state as of {world.end.isoformat()}?",
            world.end, _name(world, aq.tail.entity_id), aq.tail.entity_id,
            [aq.doc_id, cq.doc_id],
            notes="injected conflict; accurate claim should win "
                  "(formal channel / newer source)")
    rng.shuffle(out)
    for i, q in enumerate(out):
        q.id = f"{world.id}-eq{i:03d}"
    return out
