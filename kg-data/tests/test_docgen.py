"""Docgen coverage-matrix semantics + gold span validity (offline)."""

from datetime import date

from kg_data.docgen import generate_world_corpus, plan_world_docs
from kg_data.schema import build_ingestion_log


async def _corpus(world, docs=40, seed=1234):
    return await generate_world_corpus(world, docs, seed=seed, use_llm=False)


async def test_offline_pipeline_produces_aligned_quads(offline_world):
    corpus = await _corpus(offline_world)
    assert corpus.stats["docs"] > 0
    assert corpus.stats["quads"] > 0
    docs_by_id = {d.id: d for d in corpus.docs}
    for q in corpus.quads:
        doc = docs_by_id[q.doc_id]
        rev = next(r for r in doc.revisions if r.rev_id == q.rev_id)
        # span verification: offsets point at the verbatim mention
        assert rev.text[q.head.start:q.head.end] == q.head.text
        assert rev.text[q.tail.start:q.tail.end] == q.tail.text
        # mention resolves to the intended entity surface
        ent = offline_world.entity(q.head.entity_id)
        assert q.head.text.lower() in {ent.name.lower(),
                                       *(a.lower() for a in ent.aliases)}


async def test_facts_stated_as_of_doc_date(offline_world):
    """Accurate quads state the segment active at the revision's date;
    conflict quads are the only exception (and are flagged)."""
    corpus = await _corpus(offline_world)
    docs_by_id = {d.id: d for d in corpus.docs}
    n_conflict = 0
    for q in corpus.quads:
        doc = docs_by_id[q.doc_id]
        rev = next(r for r in doc.revisions if r.rev_id == q.rev_id)
        seg = offline_world.segment_at(q.relation, q.head.entity_id,
                                       rev.sim_time)
        if q.claim_status == "conflict_injected":
            n_conflict += 1
            assert seg is None or seg.tail_id != q.tail.entity_id
        else:
            assert seg is not None and seg.tail_id == q.tail.entity_id, \
                f"{q.relation} at {rev.sim_time}: stated tail not active"
    assert n_conflict == corpus.stats["conflict_quads"]


async def test_revisions_and_deletions_present(offline_world):
    corpus = await _corpus(offline_world, docs=60, seed=77)
    kinds = {r.revision_kind for d in corpus.docs for r in d.revisions}
    assert "fact_changing" in kinds or "cosmetic" in kinds
    assert any(d.deleted_at for d in corpus.docs)
    log = build_ingestion_log(corpus.docs)
    assert any(e.event == "deleted" for e in log)
    assert any(e.event == "revised" for e in log)


async def test_determinism_same_seed_same_output(offline_world):
    a = await _corpus(offline_world, docs=25, seed=5)
    b = await _corpus(offline_world, docs=25, seed=5)
    assert [d.model_dump() for d in a.docs] == [d.model_dump() for d in b.docs]
    assert [q.model_dump() for q in a.quads] == \
           [q.model_dump() for q in b.quads]


async def test_never_stated_facts_absent(offline_world):
    import random

    from kg_data.docgen import NEVER_STATED_FRAC

    rng = random.Random(1234)
    plans = plan_world_docs(offline_world, rng, 40)
    stated = {(f.predicate_id, f.head_id, f.tail_id)
              for p in plans for r in p.revisions for f in r.facts}
    total = sum(len(tl.segments) for tl in offline_world.timelines)
    assert len(stated) < total  # some facts were held back
    assert NEVER_STATED_FRAC > 0
