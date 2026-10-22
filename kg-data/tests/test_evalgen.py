"""Evalgen: answers computed correctly from timelines (pure logic)."""

from kg_data.evalgen import generate_eval_questions
from kg_data.schema import Mention, Quad


async def _setup(offline_world):
    from kg_data.docgen import generate_world_corpus

    corpus = await generate_world_corpus(offline_world, 50, seed=4242,
                                         use_llm=False)
    questions = generate_eval_questions(
        offline_world, corpus.quads, corpus.docs, seed=4242, max_total=30)
    return corpus, questions


async def test_question_kinds_present(offline_world):
    corpus, questions = await _setup(offline_world)
    kinds = {q.kind for q in questions}
    assert "point_in_time" in kinds
    assert "current_state" in kinds
    assert "staleness_superseded" in kinds or "staleness_still_true" in kinds


async def test_point_in_time_answers_match_timeline(offline_world):
    corpus, questions = await _setup(offline_world)
    checked = 0
    for q in questions:
        if q.kind != "point_in_time":
            continue
        checked += 1
        # find the (predicate, head) the question is about via answer entity
        seg = None
        for tl in offline_world.timelines:
            for s in tl.segments:
                if s.tail_id == q.answer_entity_id and \
                        offline_world.segment_at(tl.predicate_id, s.head_id,
                                                 q.as_of) is s:
                    seg = s
        assert seg is not None, f"answer not the active segment at {q.as_of}"
    assert checked >= 1


async def test_current_state_uses_final_segment(offline_world):
    corpus, questions = await _setup(offline_world)
    for q in questions:
        if q.kind != "current_state":
            continue
        assert offline_world.entity(q.answer_entity_id).name in q.answer


async def test_staleness_polarities(offline_world):
    corpus, questions = await _setup(offline_world)
    for q in questions:
        if q.kind == "staleness_superseded":
            assert q.answer.startswith("No")
            assert "since" in q.answer
        elif q.kind == "staleness_still_true":
            assert q.answer.startswith("Yes")
            assert "since" in q.answer
        elif q.kind == "conflict":
            # expected winner is the accurate entity
            assert offline_world.entity(q.answer_entity_id).name in q.answer


async def test_conflict_question_gives_both_docs(offline_world):
    corpus, questions = await _setup(offline_world)
    for q in questions:
        if q.kind == "conflict":
            assert len(q.gold_doc_ids) >= 2
