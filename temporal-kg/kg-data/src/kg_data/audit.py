"""Stage 3½ — gold audit: gold is what the text ASSERTS, not what was planned.

docgen's coverage matrix picks which facts a doc must state, but the LLM
sometimes asserts more (often correctly — it guesses plausible world facts,
sometimes via coreference like "Aisha's team" → Payments). Those assertions
are invisible to gold, so a *correct* extractor scores false positives
against it.

This stage reconciles gold with the text. For each revision the candidate
set is the world's timeline facts (active at the date, plus recently
superseded ones) INCLUDING the planned facts; one LLM call selects which
candidates the text actually asserts (coreference-aware, hedged-flagged).
The LLM can only select known facts — everything is validated against the
timeline, so truth stays deterministic; the model only detects assertions.

Results per revision:
  - planned + asserted  → kept (hedged flag applied)
  - planned + !asserted → dropped from gold (rendering failure; counted)
  - discovered + asserted → new gold quads (discovered=true)
  - injected conflicts  → kept as-is (they document wrong claims)
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, timedelta

from pydantic import BaseModel, Field, model_validator

from . import annotate, llm
from .config import settings
from .schema import Doc, Mention, Quad, World

logger = logging.getLogger(__name__)

SUPERSEDED_WINDOW_DAYS = 730  # stale restatements worth catching


class AuditSelection(BaseModel):
    fact_id: str
    hedged: bool = False


class AuditResult(BaseModel):
    """Explicit two-bucket classification. Candidates in NEITHER bucket are
    'uncertain' and fail safe: planned quads are kept, nothing discovered.
    Models naming the list differently (asserted/selected/facts) or
    returning a bare array are normalized before validation."""

    assertions: list[AuditSelection] = Field(default_factory=list)
    not_asserted: list[AuditSelection] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _flexible_shape(cls, data):
        def coerce(items):
            out = []
            for it in items or []:
                if isinstance(it, str):
                    out.append({"fact_id": it})  # "f3" → {"fact_id": "f3"}
                elif isinstance(it, bool):
                    continue
                elif isinstance(it, (int, float)):
                    out.append({"fact_id": f"f{int(it)}"})  # 3 → "f3"
                else:
                    out.append(it)  # dicts and model instances pass through
            return out

        if isinstance(data, list):
            data = {"assertions": data}
        if isinstance(data, dict):
            for synonym in ("asserted", "selected", "facts"):
                if synonym in data and "assertions" not in data:
                    data = {**data, "assertions": data[synonym]}
            if isinstance(data.get("assertions"), list):
                data = {**data, "assertions": coerce(data["assertions"])}
            if isinstance(data.get("not_asserted"), list):
                data = {**data, "not_asserted": coerce(data["not_asserted"])}
        return data


AUDIT_SYSTEM = """You audit whether a document ASSERTS specific facts.

A fact is ASSERTED if the document states or confirms that it holds —
including via paraphrase, pronouns, or role/team references ("Aisha's team"
can assert a fact about the Payments team). Rules:
- A bare question is NOT an assertion ("who owns payments?" with no answer).
- A question answered affirmatively in the thread IS an assertion — the
  answer's "yeah"/"yep"/"still on X right?" + confirmation COUNTS.
- Parenthetical roles count: "Lucas Meyer (Core Platform lead)" asserts the
  leadership fact.
- Hedged language ("pretty sure", "i think so") → asserted with hedged=true.
- Facts the text never connects are NOT asserted, even if both entities
  appear separately.

Example — candidates: f0 "svc-a runs on PostgreSQL", f1 "svc-b is owned by
Payments", f2 "Payments is led by Aisha Bello". Text:
  "maya: does svc-a still run on PostgreSQL? need to check logs
   dev: yeah it does, since forever. also aisha's team owns svc-b right?
   maya: i think so"
Answer: f0 asserted (question + "yeah it does"), f1 asserted hedged
("aisha's team" = Payments; "i think so" = hedged), f2 NOT asserted
(Aisha leading Payments is never stated — owning svc-b is a different fact).

Classify EVERY candidate fact explicitly:
- "assertions": the fact_ids the text asserts (with hedged true/false).
- "not_asserted": the fact_ids the text clearly does NOT assert.
- Omit any fact_id you are genuinely unsure about from BOTH lists."""


def _candidates_for(world: World, at: date) -> list[tuple[str, str, str, object]]:
    """(predicate_id, head_id, tail_id, segment) active at `at` or recently
    superseded — the only facts an audit may select."""
    out = []
    for tl in world.timelines:
        for seg in tl.segments:
            active = seg.valid_from <= at and (seg.valid_to is None
                                               or at < seg.valid_to)
            superseded = (seg.valid_to is not None
                          and 0 <= (at - seg.valid_to).days
                          <= SUPERSEDED_WINDOW_DAYS)
            if active or superseded:
                out.append((tl.predicate_id, seg.head_id, seg.tail_id, seg))
    return out


def _same_sentence(text: str, surfaces_a: list[str],
                   surfaces_b: list[str]) -> bool:
    """Do the two entities ever co-occur within one sentence/line? An LLM
    denial of a planned quad is only trusted when this is False — auditor
    drop precision measured ~50% on same-sentence pairs (participles and
    parentheticals it misses), while no-sentence-co-occurrence is
    mechanically certain non-assertion."""
    for sent in re.split(r"(?<=[.!?])\s+|\n", text):
        if any(s in sent for s in surfaces_a if s) and \
                any(s in sent for s in surfaces_b if s):
            return True
    return False


def _quad_from_candidate(world: World, pred_id: str, seg, doc_id: str,
                         rev_id: str, text: str, hedged: bool) -> Quad | None:
    """Anchor a discovered fact in the doc text; None if a mention is not
    locatable (pure-coreference reference with no name anywhere)."""
    head = world.entity(seg.head_id)
    tail = world.entity(seg.tail_id)
    h_span = annotate.find_mention(text, head)
    t_span = annotate.find_mention(text, tail)
    if h_span is None or t_span is None:
        return None
    if h_span[0] < t_span[1] and t_span[0] < h_span[1]:
        return None  # overlapping mentions — unusable anchors
    return Quad(
        id="", world_id=world.id, doc_id=doc_id, rev_id=rev_id,
        head=Mention(entity_id=head.id, text=text[h_span[0]:h_span[1]],
                     start=h_span[0], end=h_span[1]),
        tail=Mention(entity_id=tail.id, text=text[t_span[0]:t_span[1]],
                     start=t_span[0], end=t_span[1]),
        relation=pred_id, valid_from=seg.valid_from, valid_to=None,
        time_source="doc_date", claim_status="accurate",
        discovered=True, hedged=hedged,
    )


def _audit_prompt(world: World, doc: Doc, rev, candidates) -> str:
    lines = []
    for i, (pred_id, head_id, tail_id, seg) in enumerate(candidates):
        lines.append(f"- f{i}: {world.entity(head_id).name} "
                     f"{world.predicate(pred_id).label} "
                     f"{world.entity(tail_id).name}")
    return (f"Document date: {rev.sim_time.isoformat()} · channel: "
            f"{doc.channel}\n\nText:\n{rev.text}\n\nCandidate facts:\n"
            + "\n".join(lines))


async def audit_world_corpus(world: World, docs: list[Doc],
                             quads: list[Quad], *,
                             use_llm: bool = True,
                             stats: dict | None = None) -> list[Quad]:
    """Reconcile gold quads with what each revision actually asserts."""
    stats = stats if stats is not None else {}
    stats.setdefault("revisions_audited", 0)
    stats.setdefault("discovered", 0)
    stats.setdefault("hedged", 0)
    stats.setdefault("planned_dropped", 0)
    stats.setdefault("planned_uncertain", 0)
    stats.setdefault("unanchorable", 0)
    stats.setdefault("audit_failures", 0)
    stats.setdefault("asserted_planned", 0)

    docs_by_id = {d.id: d for d in docs}
    sem = asyncio.Semaphore(settings.concurrency)
    revs = [(d, r) for d in docs for r in d.revisions]

    async def audit_rev(doc: Doc, rev) -> tuple[dict[str, set[bool]], set[str]]:
        """Returns ({fact_id: {hedged values}} asserted, denied fact_ids)."""
        candidates = _candidates_for(world, rev.sim_time)
        if not use_llm:
            # offline fallback: every planned fact asserted, nothing discovered
            planned_keys = {(q.relation, q.head.entity_id,
                             q.tail.entity_id) for q in quads
                            if q.rev_id == rev.rev_id
                            and q.claim_status == "accurate"}
            return ({f"f{i}": {False} for i, c in enumerate(candidates)
                     if (c[0], c[1], c[2]) in planned_keys}, set())
        async with sem:
            result = await llm.structured(
                AuditResult, system=AUDIT_SYSTEM,
                user=_audit_prompt(world, doc, rev, candidates),
                temperature=0.0, max_tokens=900,
            )
        valid_ids = {f"f{i}" for i in range(len(candidates))}
        asserted: dict[str, set[bool]] = {}
        for sel in result.assertions:
            if sel.fact_id in valid_ids:
                asserted.setdefault(sel.fact_id, set()).add(sel.hedged)
        denied = {sel.fact_id for sel in result.not_asserted
                  if sel.fact_id in valid_ids and sel.fact_id not in asserted}
        return asserted, denied

    results = await asyncio.gather(
        *(audit_rev(d, r) for d, r in revs),
        return_exceptions=True)

    kept: list[Quad] = []
    for (doc, rev), outcome in zip(revs, results):
        if isinstance(outcome, BaseException):
            # audit failed → fail open: keep planned gold as-is for this rev
            stats["audit_failures"] += 1
            kept.extend(q for q in quads if q.rev_id == rev.rev_id)
            continue
        picked, denied_ids = outcome
        stats["revisions_audited"] += 1
        candidates = _candidates_for(world, rev.sim_time)
        cand_by_key = {(c[0], c[1], c[2]): c for c in candidates}
        denied_keys = {(candidates[int(fid[1:])][0],
                        candidates[int(fid[1:])][1],
                        candidates[int(fid[1:])][2]) for fid in denied_ids}
        asserted_hedged: dict[tuple, bool] = {}
        for fid, hedged_set in picked.items():
            c = candidates[int(fid[1:])]
            key = (c[0], c[1], c[2])
            # any unhedged confirmation beats hedged-only
            asserted_hedged[key] = all(hedged_set)

        for q in quads:
            if q.rev_id != rev.rev_id:
                continue
            if q.claim_status == "conflict_injected":
                kept.append(q)  # documents the wrong claim as such
                continue
            key = (q.relation, q.head.entity_id, q.tail.entity_id)
            if key in asserted_hedged:
                q.hedged = q.hedged or asserted_hedged[key]
                stats["asserted_planned"] += 1
                stats["hedged"] += 1 if q.hedged else 0
                kept.append(q)
            elif key in denied_keys:
                h = world.entity(q.head.entity_id)
                t = world.entity(q.tail.entity_id)
                if _same_sentence(rev.text, [h.name, *h.aliases],
                                  [t.name, *t.aliases]):
                    # auditor denied, but the pair co-occurs in a sentence —
                    # keep; the auditor's drop precision is too low to trust
                    stats["planned_denied_comention"] = \
                        stats.get("planned_denied_comention", 0) + 1
                    kept.append(q)
                else:
                    stats["planned_dropped"] += 1  # denial + no co-assertion
            else:
                stats["planned_uncertain"] += 1  # omitted → fail safe, keep
                kept.append(q)

        planned_keys = {(q.relation, q.head.entity_id, q.tail.entity_id)
                        for q in quads if q.rev_id == rev.rev_id}
        for key, hedged in asserted_hedged.items():
            if key in planned_keys or key not in cand_by_key:
                continue
            pred_id, head_id, tail_id, seg = cand_by_key[key]
            quad = _quad_from_candidate(world, pred_id, seg, doc.id,
                                        rev.rev_id, rev.text, hedged)
            if quad is None:
                stats["unanchorable"] += 1
                continue
            kept.append(quad)
            stats["discovered"] += 1
            if hedged:
                stats["hedged"] += 1

    kept.extend(q for q in quads if q.doc_id not in docs_by_id)
    kept.sort(key=lambda q: (q.doc_id, q.rev_id, q.relation))
    for i, q in enumerate(kept):
        q.id = f"{world.id}-q{i:04d}"
    return kept


async def apply_to_export(split: str | None = None, *,
                          use_llm: bool = True) -> dict:
    """Audit an already-exported corpus in place: rewrite gold quads,
    regenerate eval questions from them, refresh the manifest."""
    import json

    from . import evalgen, export
    from .config import DATA_DIR

    worlds = export.load_worlds()
    all_docs = export.load_docs(split)
    all_quads = export.load_quads(split)
    questions = export.load_questions(split)
    questions_all = export.load_questions()

    stats: dict = {}
    new_quads: list[Quad] = []
    for w in worlds:
        w_docs = [d for d in all_docs if d.world_id == w.id]
        w_quads = [q for q in all_quads if q.world_id == w.id]
        if not w_docs:
            continue
        new_quads.extend(await audit_world_corpus(
            w, w_docs, w_quads, use_llm=use_llm, stats=stats))

    # rewrite per-split gold files
    for s in ("train", "val", "test"):
        wids = {w.id for w in worlds if split in (None, s)
                and w.split == s}
        if not wids:
            continue
        path = DATA_DIR / "synthetic" / "gold" / s / "quads.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [q for q in new_quads if q.world_id in wids]
        with path.open("w") as f:
            for q in rows:
                f.write(q.model_dump_json() + "\n")

    # regenerate questions against audited quads
    if split is None:
        qs = questions_all
    else:
        qs = [q for q in questions_all if q.split != split]
    for w in worlds:
        w_docs = [d for d in all_docs if d.world_id == w.id]
        if not w_docs:
            continue
        w_quads = [q for q in new_quads if q.world_id == w.id]
        qs.extend(evalgen.generate_eval_questions(
            w, w_quads, w_docs, seed=w.seed, max_total=20))
    qs.sort(key=lambda q: q.id)
    (DATA_DIR / "synthetic" / "evals" / "questions.jsonl").parent.mkdir(
        parents=True, exist_ok=True)
    with (DATA_DIR / "synthetic" / "evals" / "questions.jsonl").open("w") as f:
        for q in qs:
            f.write(q.model_dump_json() + "\n")

    export.refresh_manifest()
    return stats
