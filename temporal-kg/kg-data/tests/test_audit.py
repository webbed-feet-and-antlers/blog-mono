"""Gold audit: assertion reconciliation against the timeline."""

from datetime import date

from kg_data import audit
from kg_data.audit import AuditResult, AuditSelection
from kg_data.schema import (
    Author, Doc, Entity, Mention, Predicate, Quad, Revision, Segment,
    Timeline, World,
)


def _world() -> World:
    return World(
        id="w", seed=1, start=date(2022, 1, 1), end=date(2026, 6, 30),
        entities=[
            Entity(id="billing", name="billing-api", type="service"),
            Entity(id="auth", name="auth-gateway", type="service"),
            Entity(id="payments", name="Payments", type="team"),
            Entity(id="aisha", name="Aisha Bello", type="person"),
        ],
        predicates=[
            Predicate(id="owned_by_team", label="is owned by",
                      volatility="durable", head_type="service",
                      tail_type="team"),
            Predicate(id="depends_on", label="depends on", volatility="medium",
                      functional=False, head_type="service",
                      tail_type="service"),
        ],
        timelines=[
            Timeline(predicate_id="owned_by_team", segments=[
                Segment(head_id="billing", tail_id="payments",
                        valid_from=date(2015, 1, 1)),
            ]),
            Timeline(predicate_id="depends_on", segments=[
                Segment(head_id="auth", tail_id="billing",
                        valid_from=date(2015, 1, 1)),
            ]),
        ],
    )


def _doc_text():
    return ("maya [9:14 AM]: who owns payments for billing-api? aisha's "
            "team? and auth-gateway depends on billing-api right? pretty "
            "sure")


def _doc_quads():
    """One planned quad (asserted in text), one planned quad NOT asserted,
    one injected conflict."""
    text = _doc_text()
    return [
        Quad(id="w-q0", world_id="w", doc_id="w-d0", rev_id="w-d0#r0",
             head=Mention(entity_id="auth", text="auth-gateway",
                          start=text.index("auth-gateway"),
                          end=text.index("auth-gateway") + 12),
             tail=Mention(entity_id="billing", text="billing-api",
                          start=text.rindex("billing-api"),
                          end=text.rindex("billing-api") + 11),
             relation="depends_on", valid_from=date(2015, 1, 1)),
        Quad(id="w-q1", world_id="w", doc_id="w-d0", rev_id="w-d0#r0",
             head=Mention(entity_id="billing", text="billing-api",
                          start=text.index("billing-api"),
                          end=text.index("billing-api") + 11),
             tail=Mention(entity_id="aisha", text="aisha's",
                          start=text.index("aisha's"),
                          end=text.index("aisha's") + 7),
             relation="owned_by_team", valid_from=date(2015, 1, 1),
             claim_status="conflict_injected"),
    ]


def _doc():
    return Doc(id="w-d0", world_id="w", channel="slack",
               doc_type="thread", title="", author=Author(name="maya"),
               created_at=date(2024, 6, 1),
               revisions=[Revision(rev_id="w-d0#r0",
                                   sim_time=date(2024, 6, 1),
                                   text=_doc_text())])


async def test_offline_audit_keeps_planned_only():
    world, doc, quads = _world(), _doc(), _doc_quads()
    out = await audit.audit_world_corpus(world, [doc], quads, use_llm=False)
    # offline fallback: planned facts asserted, nothing discovered
    assert len(out) == 2  # planned depends_on + injected conflict


async def test_audit_discovers_and_drops(monkeypatch):
    """Fake audit LLM: asserts depends_on (planned) + owned_by_team
    (NOT planned → discovered), skips nothing else."""
    world, doc, quads = _world(), _doc(), _doc_quads()

    async def fake_structured(model_cls, system, user, **kw):
        assert model_cls is AuditResult
        assert "depends on" in user and "is owned by" in user
        # fact ids depend on candidate order: find them from the prompt
        ids = {}
        for line in user.splitlines():
            if line.startswith("- f") and ":" in line:
                fid, rest = line[2:].split(":", 1)
                ids[rest.strip()] = fid
        return AuditResult(assertions=[
            AuditSelection(fact_id=ids["auth-gateway depends on billing-api"]),
            AuditSelection(
                fact_id=ids["billing-api is owned by Payments"], hedged=True),
        ])

    from kg_data import llm

    monkeypatch.setattr(llm, "structured", fake_structured)
    stats: dict = {}
    out = await audit.audit_world_corpus(world, [doc], quads, use_llm=True,
                                         stats=stats)

    by_relation = {(q.relation, q.claim_status): q for q in out}
    # planned asserted → kept, not discovered
    kept = by_relation[("depends_on", "accurate")]
    assert kept.discovered is False
    # injected conflict → kept as-is
    assert ("owned_by_team", "conflict_injected") in by_relation
    # discovered: owned_by_team accurate, hedged, anchored spans
    disc = by_relation[("owned_by_team", "accurate")]
    assert disc.discovered is True and disc.hedged is True
    assert doc.latest_revision.text[disc.head.start:disc.head.end] == \
        disc.head.text
    assert world.entity(disc.tail.entity_id).name == "Payments"
    assert stats["discovered"] == 1 and stats["hedged"] >= 1


async def test_audit_buckets_fail_safe(monkeypatch):
    """Explicit not_asserted drops a planned quad ONLY when the pair never
    co-occurs in a sentence (mechanical corroboration); same-sentence
    denials are kept. Omission (uncertain) keeps."""
    world, doc, quads = _world(), _doc(), _doc_quads()

    async def fake_structured(model_cls, system, user, **kw):
        ids = {}
        for line in user.splitlines():
            if line.startswith("- f") and ":" in line:
                fid, rest = line[2:].split(":", 1)
                ids[rest.strip()] = fid
        return AuditResult(
            assertions=[],
            not_asserted=[AuditSelection(
                fact_id=ids["auth-gateway depends on billing-api"])],
        )

    from kg_data import audit as audit_mod, llm

    monkeypatch.setattr(llm, "structured", fake_structured)
    # text asserts the dependency in one sentence → denial must NOT drop it
    stats: dict = {}
    out = await audit_mod.audit_world_corpus(world, [doc], quads,
                                             use_llm=True, stats=stats)
    assert ("depends_on", "accurate") in \
        {(q.relation, q.claim_status) for q in out}
    assert stats["planned_denied_comention"] == 1
    assert stats["planned_dropped"] == 0

    # same denial, but no sentence contains both entities → dropped
    def no_comention(*a, **kw):
        return False

    monkeypatch.setattr(audit_mod, "_same_sentence", no_comention)
    stats2: dict = {}
    out2 = await audit_mod.audit_world_corpus(world, [doc], quads,
                                              use_llm=True, stats=stats2)
    assert ("depends_on", "accurate") not in \
        {(q.relation, q.claim_status) for q in out2}
    assert stats2["planned_dropped"] == 1
    assert ("owned_by_team", "conflict_injected") in \
        {(q.relation, q.claim_status) for q in out2}


async def test_audit_result_accepts_flexible_shapes():
    assert AuditResult.model_validate(
        [{"fact_id": "f0"}, {"fact_id": "f1", "hedged": True}]
    ).assertions[1].hedged is True
    assert AuditResult.model_validate(
        {"asserted": [{"fact_id": "f0"}]}
    ).assertions[0].fact_id == "f0"
    assert AuditResult.model_validate(
        {"facts": [{"fact_id": "f0"}]}
    ).assertions[0].fact_id == "f0"
    assert AuditResult.model_validate(
        {"assertions": ["f0", "f1"], "not_asserted": ["f2"]}
    ).assertions[1].fact_id == "f1"


async def test_audit_fails_open(monkeypatch):
    world, doc, quads = _world(), _doc(), _doc_quads()

    async def boom(*a, **kw):
        raise RuntimeError("rate limited")

    from kg_data import llm

    monkeypatch.setattr(llm, "structured", boom)
    stats: dict = {}
    out = await audit.audit_world_corpus(world, [doc], quads, use_llm=True,
                                         stats=stats)
    assert len(out) == len(quads)  # planned gold untouched on failure
    assert stats["audit_failures"] == 1
