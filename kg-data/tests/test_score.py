"""Score: triple/temporal matching, question accuracy, stream semantics."""

from datetime import date

from kg_data.schema import EvalQuestion, Mention, Quad
from kg_data.score import (
    Resolver, norm, score_questions, score_quads, score_stream,
)


def _quad(world, doc, head, rel, tail, vf=None, vt=None, status="accurate"):
    return Quad(
        id="", world_id=world, doc_id=doc, rev_id=f"{doc}#r0",
        head=Mention(entity_id=head, text=head, start=0, end=len(head)),
        tail=Mention(entity_id=tail, text=tail, start=0, end=len(tail)),
        relation=rel, valid_from=vf, valid_to=vt, claim_status=status,
    )


WORLDS = []


def test_norm_underscores_and_case():
    assert norm("runs_on_database") == norm("Runs On Database")


def test_triple_f1_perfect_and_missed():
    gold = [_quad("w0", "d1", "billing-api", "runs_on_database", "PostgreSQL")]
    pred = [_quad("w0", "d1", "billing-api", "runs on database", "PostgreSQL")]
    r = score_quads(gold, pred, WORLDS)
    assert r["f1"] == 1.0
    # wrong tail → miss
    bad = [_quad("w0", "d1", "billing-api", "runs on database", "MySQL")]
    r = score_quads(gold, bad, WORLDS)
    assert r["f1"] == 0.0 and r["n_pred"] == 1


def test_relation_matches_label_form():
    from kg_data.schema import Entity, Predicate, Timeline, World

    w = World(id="w0", seed=1, start=date(2022, 1, 1), end=date(2026, 6, 30),
              entities=[Entity(id="a", name="billing-api", type="service"),
                        Entity(id="b", name="PostgreSQL", type="database")],
              predicates=[Predicate(id="runs_on_database", label="runs on",
                                    volatility="medium", head_type="service",
                                    tail_type="database")],
              timelines=[])
    gold = [_quad("w0", "d1", "billing-api", "runs_on_database", "PostgreSQL")]
    pred = [_quad("w0", "d1", "billing-api", "runs on", "PostgreSQL")]
    assert score_quads(gold, pred, [w])["f1"] == 1.0


def test_temporal_match_accounting():
    gold = [_quad("w0", "d1", "h", "r", "t", vf=date(2023, 1, 1),
                  vt=date(2024, 1, 1))]
    exact = [_quad("w0", "d1", "h", "r", "t", vf=date(2023, 1, 1),
                   vt=date(2024, 1, 1))]
    overlap = [_quad("w0", "d1", "h", "r", "t", vf=date(2023, 6, 1),
                     vt=None)]
    r = score_quads(gold, exact, WORLDS)
    assert r["temporal"]["exact_match"] == 1
    r = score_quads(gold, overlap, WORLDS)
    assert r["temporal"]["exact_match"] == 0
    assert r["temporal"]["overlap_match"] == 1


def test_stream_deletion_removes_support():
    from kg_data.schema import Author, Doc, Revision

    gold = [_quad("w0", "d1", "h", "r", "t", vf=date(2023, 1, 1))]
    pred = [_quad("w0", "d1", "h", "r", "t")]
    live = Doc(id="d1", world_id="w0", channel="slack", doc_type="thread",
               title="", author=Author(name="x"),
               created_at=date(2023, 1, 1),
               revisions=[Revision(rev_id="d1#r0",
                                   sim_time=date(2023, 1, 1), text="t")])
    deleted = live.model_copy(deep=True)
    deleted.deleted_at = date(2025, 1, 1)

    r_live = score_stream(gold, pred, [live], WORLDS)
    r_gone = score_stream(gold, pred, [deleted], WORLDS)
    assert r_live["f1"] == 1.0
    assert r_gone["n_gold_current"] == 0  # support dropped with the doc


def test_stream_excludes_superseded_and_conflicts():
    from kg_data.schema import Author, Doc, Revision

    doc = Doc(id="d1", world_id="w0", channel="slack", doc_type="thread",
              title="", author=Author(name="x"), created_at=date(2023, 1, 1),
              revisions=[Revision(rev_id="d1#r0", sim_time=date(2023, 1, 1),
                                  text="t")])
    old_fact = _quad("w0", "d1", "h", "r", "old", vf=date(2022, 1, 1),
                     vt=date(2023, 1, 1))  # ended → superseded
    conflict = _quad("w0", "d1", "h", "r", "wrong", status="conflict_injected")
    current = _quad("w0", "d1", "h", "r", "now", vf=date(2023, 1, 1))
    r = score_stream([old_fact, conflict, current], [], [doc], WORLDS)
    assert r["n_gold_current"] == 1


def test_question_scoring_alias_aware():
    from kg_data.schema import Entity, World

    w = World(id="w0", seed=1, start=date(2022, 1, 1), end=date(2026, 6, 30),
              entities=[Entity(id="pg", name="PostgreSQL", type="database",
                               aliases=["Postgres"])],
              predicates=[], timelines=[])
    q = EvalQuestion(id="q1", world_id="w0", kind="current_state",
                     question="? ", as_of=date(2026, 6, 30),
                     answer="PostgreSQL", answer_entity_id="pg")
    assert score_questions([q], {"q1": "Postgres"}, [w])["accuracy"] == 1.0
    assert score_questions([q], {"q1": "MySQL"}, [w])["accuracy"] == 0.0
