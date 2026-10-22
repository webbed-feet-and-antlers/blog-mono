"""Worldgen: determinism, timeline validity, name substitution."""

import random

from kg_data.worldgen import (
    PREDICATE_CATALOG, WINDOW_END, WINDOW_START,
    build_scaffold, generate_world, instantiate_world,
)
from conftest import fake_naming


def test_scaffold_deterministic():
    a = build_scaffold(random.Random(7))
    b = build_scaffold(random.Random(7))
    assert a == b


def test_functional_segments_sorted_and_non_overlapping():
    world = instantiate_world("w", 7, build_scaffold(random.Random(7)),
                              fake_naming())
    functional = {p.id for p in PREDICATE_CATALOG if p.functional}
    for tl in world.timelines:
        if tl.predicate_id not in functional:
            continue
        by_head: dict[str, list] = {}
        for s in tl.segments:
            by_head.setdefault(s.head_id, []).append(s)
        for segs in by_head.values():
            segs = sorted(segs, key=lambda s: s.valid_from)
            for cur, nxt in zip(segs, segs[1:]):
                assert cur.valid_to == nxt.valid_from, \
                    f"gap/overlap in {tl.predicate_id}/{cur.head_id}"
            for s in segs:
                assert s.valid_from < (s.valid_to or WINDOW_END)
                assert WINDOW_START.year - 10 <= s.valid_from.year
    # immutable facts never change
    for tl in world.timelines:
        if tl.predicate_id == "founded_in":
            assert len(tl.segments) == 1


def test_at_least_one_database_migration():
    for seed in (1, 2, 3, 4, 5):
        world = instantiate_world(
            "w", seed, build_scaffold(random.Random(seed)), fake_naming())
        db_tl = next(tl for tl in world.timelines
                     if tl.predicate_id == "runs_on_database")
        assert any(s.valid_from >= WINDOW_START for s in db_tl.segments), \
            f"seed {seed} forced migration missing"


def test_company_name_not_truncated():
    """Regression: singular naming fields (company: str) were indexed
    character-wise, turning 'Lumina Analytics' into entity 'L'."""
    naming = fake_naming()
    world = instantiate_world("w", 3, build_scaffold(random.Random(3)), naming)
    company = next(e for e in world.entities if e.type == "company")
    assert company.name == naming.company
    assert len(company.name) >= 3


def test_every_tail_is_a_named_entity():
    world = instantiate_world("w", 11, build_scaffold(random.Random(11)),
                              fake_naming())
    ids = {e.id for e in world.entities}
    for tl in world.timelines:
        for s in tl.segments:
            assert s.head_id in ids, f"unknown head {s.head_id}"
            assert s.tail_id in ids, f"unknown tail {s.tail_id}"
    names = [e.name for e in world.entities]
    assert all(not n.startswith(("SERVICE_", "TEAM_", "PERSON_", "DB_",
                                 "CLOUD_", "CITY_", "VENDOR_", "YEAR_"))
               for n in names), "placeholder leaked into names"


def test_segment_at_lookup(offline_world):
    w = offline_world
    for tl in w.timelines:
        for seg in tl.segments:
            mid = seg.valid_from
            found = w.segment_at(tl.predicate_id, seg.head_id, mid)
            assert found is not None and found.tail_id == seg.tail_id


async def test_generate_world_offline():
    world = await generate_world("w-off", 99, use_llm=False)
    assert world.lore
    assert any(p.id == "has_ceo" for p in world.predicates)
    ceo_tl = next(tl for tl in world.timelines if tl.predicate_id == "has_ceo")
    assert len(ceo_tl.segments) >= 2  # succession exists
