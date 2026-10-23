"""Stage 2 — multi-channel document lifecycles.

A seeded coverage matrix assigns each document the timeline facts active
AS OF its date, so implicit obsolescence is structural: old docs state old
truths and are simply never revised. ~10% of docs carry a conflict injection
(an outdated or wrong tail), ~20% of formal docs get later revisions
(fact-changing or cosmetic), a few docs get deleted, and ~15% of facts are
never stated anywhere (recall targets). Rendering goes through the LLM with
real-corpus style exemplars when available, then messify roughs the text up
BEFORE span alignment so gold offsets stay valid.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from zlib import crc32

from pydantic import BaseModel, Field

from . import annotate, llm
from .config import settings
from .messify import messify
from .schema import Author, Doc, Quad, Revision, RevisionKind, World

logger = logging.getLogger(__name__)

CHANNEL_MIX = [("slack", 0.35), ("confluence", 0.25), ("drive", 0.13),
               ("email", 0.15), ("meeting_notes", 0.12)]

DOC_TYPES = {
    "slack": ["incident thread", "team thread", "deploy chatter", "support thread"],
    "confluence": ["architecture spec", "runbook", "team page", "onboarding doc"],
    "drive": ["migration plan", "design doc", "postmortem"],
    "email": ["status update", "announcement", "vendor update"],
    "meeting_notes": ["weekly sync notes", "incident review notes", "planning notes"],
}

FACT_CAP = {"slack": 3, "confluence": 6, "drive": 5, "email": 3, "meeting_notes": 4}

# probability a segment active at the doc date is stated, by volatility
STATE_PROB = {"immutable": 0.10, "durable": 0.35, "medium": 0.55, "ephemeral": 0.45}

ROLES = ["staff engineer", "senior engineer", "engineering manager",
         "platform lead", "SRE", "product manager"]

NEVER_STATED_FRAC = 0.15
CONFLICT_FRAC = 0.10
REVISION_FRAC = 0.20
DELETE_FRAC = 0.05


@dataclass
class FactRef:
    predicate_id: str
    head_id: str
    tail_id: str  # what the doc claims (override for conflicts)
    true_tail_id: str
    valid_from: date | None
    valid_to: date | None
    time_source: str = "doc_date"
    claim_status: str = "accurate"


@dataclass
class RevPlan:
    sim_time: date
    revision_kind: RevisionKind = "initial"
    facts: list[FactRef] = field(default_factory=list)


@dataclass
class DocPlan:
    doc_id: str
    channel: str
    doc_type: str
    created_at: date
    author: Author
    revisions: list[RevPlan]
    deleted_at: date | None = None


class GeneratedBody(BaseModel):
    title: str = ""
    body: str = Field(min_length=40)


def _pick_channel(rng: random.Random) -> str:
    r, acc = rng.random(), 0.0
    for channel, w in CHANNEL_MIX:
        acc += w
        if r <= acc:
            return channel
    return CHANNEL_MIX[-1][0]


def _keys(facts: list[FactRef]) -> set:
    return {(f.predicate_id, f.head_id, f.tail_id, f.valid_from) for f in facts}


def _rand_date(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randrange(max(1, (end - start).days)))


def _facts_as_of(world: World, at: date, channel: str, rng: random.Random,
                 never: set) -> list[FactRef]:
    facts: list[FactRef] = []
    cap = FACT_CAP[channel]
    candidates: list[tuple[str, object]] = []
    for tl in world.timelines:
        pred = world.predicate(tl.predicate_id)
        for seg in tl.segments:
            if seg.valid_from <= at and (seg.valid_to is None or at < seg.valid_to):
                candidates.append((pred, seg))
    rng.shuffle(candidates)
    for pred, seg in candidates:
        if len(facts) >= cap:
            break
        if (seg.head_id, seg.tail_id, seg.valid_from) in never:
            continue
        if rng.random() > STATE_PROB.get(pred.volatility, 0.4):
            continue
        explicit = pred.volatility in ("medium", "ephemeral") and rng.random() < 0.35
        facts.append(FactRef(
            predicate_id=pred.id, head_id=seg.head_id, tail_id=seg.tail_id,
            true_tail_id=seg.tail_id, valid_from=seg.valid_from,
            valid_to=seg.valid_to,
            time_source="explicit_in_text" if explicit else "doc_date",
        ))
    return facts


def _inject_conflict(world: World, rng: random.Random, rev: RevPlan) -> None:
    """Flip one stated functional fact to an outdated/wrong tail."""
    candidates = [f for f in rev.facts
                  if world.predicate(f.predicate_id).volatility == "medium"]
    if not candidates:
        return
    fact = rng.choice(candidates)
    # prefer the previous segment's tail (= outdated claim, most realistic)
    ordered = sorted(world.segments_for(fact.predicate_id, fact.head_id),
                     key=lambda s: s.valid_from)
    prev_tails = [s.tail_id for s in ordered if s.tail_id != fact.true_tail_id]
    if prev_tails and rng.random() < 0.7:
        fact.tail_id = rng.choice(prev_tails)
    else:  # otherwise a random same-type entity (wrong claim)
        same_type = [e for e in world.entities
                     if e.type == world.entity(fact.true_tail_id).type
                     and e.id != fact.true_tail_id]
        if not same_type:
            return
        fact.tail_id = rng.choice(same_type).id
    fact.claim_status = "conflict_injected"


def plan_world_docs(world: World, rng: random.Random,
                    docs_per_world: int) -> list[DocPlan]:
    """Deterministic doc plan: channels, dates, facts-as-of-date, lifecycles."""
    all_segments = [s for tl in world.timelines for s in tl.segments]
    never = {(s.head_id, s.tail_id, s.valid_from) for s in all_segments
             if rng.random() < NEVER_STATED_FRAC}
    people = [e for e in world.entities if e.type == "person"]

    plans: list[DocPlan] = []
    for i in range(docs_per_world):
        channel = _pick_channel(rng)
        created = _rand_date(rng, world.start, world.end)
        person = rng.choice(people)
        author = Author(
            name=person.name, role=rng.choice(ROLES),
            offboarded_on=_rand_date(rng, created, world.end)
            if rng.random() < 0.20 else None,
        )
        doc_id = f"{world.id}-d{i:03d}"
        revisions = [RevPlan(sim_time=created)]

        if channel in ("confluence", "drive") and rng.random() < REVISION_FRAC:
            for _ in range(rng.randint(1, 2)):
                latest = max(r.sim_time for r in revisions)
                span_days = (world.end - latest).days
                if span_days < 90:
                    break
                revisions.append(
                    RevPlan(sim_time=latest + timedelta(
                        days=rng.randrange(60, span_days))))
        deleted_at = None
        if rng.random() < DELETE_FRAC:
            last = max(r.sim_time for r in revisions)
            if (world.end - last).days > 60:
                deleted_at = _rand_date(rng, last, world.end)

        plans.append(DocPlan(
            doc_id=doc_id, channel=channel,
            doc_type=rng.choice(DOC_TYPES[channel]),
            created_at=created, author=author,
            revisions=revisions, deleted_at=deleted_at,
        ))

    for plan in plans:
        for j, rev in enumerate(plan.revisions):
            rev.facts = _facts_as_of(world, rev.sim_time, plan.channel, rng, never)
            rev.revision_kind = "initial" if j == 0 else "fact_changing"
            if j > 0 and _keys(plan.revisions[j - 1].facts) == _keys(rev.facts):
                rev.revision_kind = "cosmetic"
        if plan.revisions[0].facts and rng.random() < CONFLICT_FRAC:
            _inject_conflict(world, rng, plan.revisions[0])
    return plans


STYLE_GUIDES = {
    "slack": (
        "A Slack chat transcript. Format every line exactly as `Name [h:mm AM]: message`. "
        "4-9 short messages from 2-4 different people, casual tone, abbreviations "
        "and typos are fine, quick back-and-forth, no headers."
    ),
    "confluence": (
        "A Confluence wiki page in markdown. Start with '# Title', then 2-4 short "
        "sections with '## ' headers. Formal-ish engineering documentation, "
        "150-350 words."
    ),
    "drive": (
        "A Google Drive document in markdown. Start with '# Title'. A practical "
        "internal doc (plan/design/postmortem), 100-250 words, bullet points welcome."
    ),
    "email": (
        "An email: first line 'Subject: ...', blank line, then the body with a "
        "greeting and sign-off. 60-150 words, professional but human."
    ),
    "meeting_notes": (
        "Meeting notes in markdown: '# Title', an 'Attendees:' line, then "
        "bullet notes ('- ...'). 60-180 words, terse bullets."
    ),
}


def _company_name(world: World) -> str:
    return next(e.name for e in world.entities if e.type == "company")


def _fact_lines(world: World, facts: list[FactRef]) -> list[str]:
    lines = []
    for f in facts:
        head = world.entity(f.head_id).name
        tail = world.entity(f.tail_id).name
        label = world.predicate(f.predicate_id).label
        since = (f" (this has been the case since {f.valid_from.year})"
                 if f.time_source == "explicit_in_text" and f.valid_from else "")
        lines.append(f"- {head} {label} {tail}{since}")
    return lines


def _render_prompt(world: World, plan: DocPlan, rev: RevPlan, rev_id: str,
                   exemplar: str | None) -> tuple[str, str]:
    facts = _fact_lines(world, rev.facts)
    channel_note = ""
    if rev.revision_kind == "fact_changing":
        channel_note = (" This is a LATER REVISION of an existing internal page — "
                        "update it to reflect the current state.")
    elif rev.revision_kind == "cosmetic":
        channel_note = (" This is a cosmetic rewording of an existing page — same "
                        "facts, different phrasing.")
    exemplar_block = (f"\nStyle reference (imitate the register, not the content):\n"
                      f"---\n{exemplar}\n---\n") if exemplar else ""
    system = (
        "You write realistic enterprise artifacts for a synthetic company corpus. "
        "You will be given facts that MUST be stated. Mention entity names EXACTLY "
        "as written. Do NOT mention any other real-world products, companies, "
        "tools, or people — generic words are fine. Keep the body under 250 words."
    )
    user = (
        f"Company: {_company_name(world)}.\n"
        f"Artifact date: {rev.sim_time.isoformat()}. "
        f"Author: {plan.author.name} ({plan.author.role}).{channel_note}\n"
        f"Artifact kind: {plan.doc_type} on channel '{plan.channel}'.\n"
        f"Format requirements: {STYLE_GUIDES[plan.channel]}\n"
        f"{exemplar_block}"
        f"Facts that MUST be stated:\n" + "\n".join(facts) +
        "\nWrite the artifact now."
    )
    return system, user


def _fallback_render(world: World, plan: DocPlan, rev: RevPlan) -> GeneratedBody:
    """Deterministic template renderer (tests / offline mode)."""
    lines = [f"{world.entity(f.head_id).name} "
             f"{world.predicate(f.predicate_id).label} "
             f"{world.entity(f.tail_id).name}."
             for f in rev.facts]
    title = f"{plan.doc_type.title()} {rev.sim_time.isoformat()}"
    return GeneratedBody(title=title, body=" ".join(lines))


async def _render(world: World, plan: DocPlan, rev: RevPlan, rev_id: str,
                  exemplar: str | None, *, use_llm: bool) -> GeneratedBody:
    if not use_llm:
        return _fallback_render(world, plan, rev)
    system, user = _render_prompt(world, plan, rev, rev_id, exemplar)
    return await llm.structured(
        GeneratedBody, system=system, user=user, temperature=0.7,
        max_tokens=1600,
    )


@dataclass
class WorldCorpus:
    world: World
    docs: list[Doc]
    quads: list[Quad]
    stats: dict


def _assign_spans(world: World, text: str,
                  entity_ids: set[str]) -> tuple[dict[str, tuple[int, int]],
                                                 list[str]]:
    """Greedy non-overlapping mention assignment (messify requires protected
    spans to be disjoint). Entities whose every occurrence overlaps an
    already-taken span land in `missing`."""
    spans: dict[str, tuple[int, int]] = {}
    taken: list[tuple[int, int]] = []
    missing: list[str] = []
    for eid in sorted(entity_ids):
        ent = world.entity(eid)
        cursor = 0
        span = annotate.find_mention(text, ent, after=cursor)
        while span is not None and any(
                span[0] < t_end and t_start < span[1]
                for t_start, t_end in taken):
            cursor = span[1]
            span = annotate.find_mention(text, ent, after=cursor)
        if span is None:
            missing.append(ent.name)
        else:
            spans[eid] = span
            taken.append(span)
    return spans, missing


def _align_revision(
    world: World, plan: DocPlan, rev: RevPlan, rev_id: str, text: str,
    stats: dict,
) -> tuple[list[Quad], str] | None:
    """Locate mentions, messify, build quads. Pure sync code."""
    entity_ids = {f.head_id for f in rev.facts} | {f.tail_id for f in rev.facts}
    stats["span_attempts"] += len(entity_ids)
    if not rev.facts:
        result = messify(text, [], random.Random(crc32(rev_id.encode())),
                         channel=plan.channel)
        return [], result.text
    spans, missing = _assign_spans(world, text, entity_ids)
    stats["span_verified"] += len(spans)
    if missing:
        stats["dropped_facts"] += sum(
            1 for f in rev.facts
            if f.head_id not in spans or f.tail_id not in spans)
        return None

    ordered_ids = sorted(spans, key=lambda e: spans[e])
    result = messify(
        text, [spans[e] for e in ordered_ids],
        random.Random(crc32(f"{rev_id}:{plan.doc_id}".encode())),
        channel=plan.channel,
    )
    adjusted = {eid: result.spans[i] for i, eid in enumerate(ordered_ids)}
    new_quads, _ = annotate.next_quads(
        world, [f.__dict__ for f in rev.facts], plan.doc_id, rev_id,
        result.text, adjusted, start_index=0,
    )
    stats["dropped_facts"] += len(rev.facts) - len(new_quads)
    return new_quads, result.text


async def generate_world_corpus(
    world: World, docs_per_world: int, *, seed: int,
    exemplars: dict[str, str] | None = None,
    use_llm: bool = True,
) -> WorldCorpus:
    """Full stage-2/3 pipeline for one world: plan → render → align."""
    rng = random.Random(seed ^ 0x5D0C)
    plans = plan_world_docs(world, rng, docs_per_world)
    exemplars = exemplars or {}
    sem = asyncio.Semaphore(settings.concurrency)
    stats = {"docs": 0, "revisions": 0, "deletions": 0, "quads": 0,
             "conflict_quads": 0, "span_attempts": 0, "span_verified": 0,
             "dropped_facts": 0, "regenerations": 0}

    docs: list[Doc] = []
    quads: list[Quad] = []

    async def build_doc(plan: DocPlan) -> None:
        async with sem:
            revisions: list[Revision] = []
            doc_quads: list[Quad] = []
            for j, rev in enumerate(plan.revisions):
                rev_id = f"{plan.doc_id}#r{j}"
                try:
                    body = await _render(world, plan, rev, rev_id,
                                         exemplars.get(plan.channel),
                                         use_llm=use_llm)
                except llm.StructuredGenerationError as exc:
                    # one bad generation must not kill the run — degrade to
                    # the deterministic template for this revision
                    stats["fallback_renders"] = \
                        stats.get("fallback_renders", 0) + 1
                    logger.warning("render failed for %s (%s); using template",
                                   rev_id, str(exc)[:120])
                    body = _fallback_render(world, plan, rev)
                aligned = _align_revision(world, plan, rev, rev_id,
                                          body.body, stats)
                if aligned is None and use_llm:
                    # one strict regeneration: force the exact missing strings
                    stats["regenerations"] += 1
                    missing_names = sorted({
                        world.entity(e).name for e in
                        ({f.head_id for f in rev.facts} |
                         {f.tail_id for f in rev.facts})})
                    facts = _fact_lines(world, rev.facts)
                    try:
                        body = await llm.structured(
                            GeneratedBody,
                            system=("You write realistic enterprise artifacts. "
                                    "You MUST include the exact substrings "
                                    "listed below, character-for-character. "
                                    "Keep the body under 250 words."),
                            user=(f"Rewrite this {plan.doc_type} "
                                  f"({plan.channel}), date "
                                  f"{rev.sim_time.isoformat()}. Format: "
                                  f"{STYLE_GUIDES[plan.channel]}\nFacts:\n"
                                  + "\n".join(facts)
                                  + f"\nThese EXACT strings must appear: "
                                    f"{', '.join(missing_names)}."),
                            temperature=0.5, max_tokens=1600,
                        )
                    except llm.StructuredGenerationError as exc:
                        logger.warning("regen failed for %s (%s); dropping doc",
                                       rev_id, str(exc)[:120])
                        return
                    aligned = _align_revision(world, plan, rev, rev_id,
                                              body.body, stats)
                if aligned is None:
                    return  # drop the whole doc
                rev_quads, final_text = aligned
                doc_quads.extend(rev_quads)
                revisions.append(Revision(
                    rev_id=rev_id, sim_time=rev.sim_time, text=final_text,
                    revision_kind=rev.revision_kind,
                ))
            if not revisions:
                return
            docs.append(Doc(
                id=plan.doc_id, world_id=world.id,
                channel=plan.channel,  # type: ignore[arg-type]
                doc_type=plan.doc_type, title="", author=plan.author,
                created_at=plan.created_at, deleted_at=plan.deleted_at,
                revisions=revisions,
            ))
            quads.extend(doc_quads)
            stats["docs"] += 1
            stats["revisions"] += len(revisions)
            stats["conflict_quads"] += sum(
                1 for q in doc_quads if q.claim_status == "conflict_injected")
            if plan.deleted_at:
                stats["deletions"] += 1

    await asyncio.gather(*(build_doc(p) for p in plans))
    docs.sort(key=lambda d: d.id)
    quads.sort(key=lambda q: (q.doc_id, q.rev_id, q.relation))
    for i, q in enumerate(quads):
        q.id = f"{world.id}-q{i:04d}"
    stats["quads"] = len(quads)
    return WorldCorpus(world=world, docs=docs, quads=quads, stats=stats)
