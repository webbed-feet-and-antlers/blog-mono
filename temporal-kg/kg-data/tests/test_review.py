"""Review page builder: payload correctness + HTML sanity."""

import json
import re

from kg_data.review import (
    _noise_signals, _unlisted_proper_nouns, build_payload, render_html,
)
from kg_data.schema import Segment


def test_noise_signals():
    s = _noise_signals("hey can u check the billing-api pls\n"
                       "yeah gimme a sec 😅")
    assert s["lc_start_rate"] > 0.4
    assert s["abbrev"] >= 1
    assert s["emoji"] == 1


def test_unlisted_proper_nouns_flags_brands_not_entities():
    text = ("We moved the queue to Kafka last week. Ask Maya about "
            "billing-api. The Payments team approved it on Monday.")
    known = {"billing-api", "payments", "maya", "we", "the", "team", "ask",
             "about", "moved", "queue", "last", "week", "approved", "it",
             "on"}.union({"monday"})
    flagged = _unlisted_proper_nouns(text, known)
    assert "Kafka" in flagged
    assert not any(w in flagged for w in ("Maya", "Payments", "Monday"))


def test_build_payload_and_html(offline_world):
    import asyncio

    from conftest import fake_naming
    from kg_data.docgen import generate_world_corpus
    from kg_data.evalgen import generate_eval_questions
    from kg_data.worldgen import build_scaffold, instantiate_world
    import random as _r

    scaffold = build_scaffold(_r.Random(1234))
    w0 = instantiate_world("w-test", 1234, scaffold, fake_naming())
    w0.split = "train"
    corpus = asyncio.run(generate_world_corpus(w0, 15, seed=7, use_llm=False))
    questions = generate_eval_questions(w0, corpus.quads, corpus.docs,
                                        seed=7, max_total=10)

    payload = build_payload([w0], corpus.docs, corpus.quads, questions)
    assert payload["docs"], "no doc payloads"
    assert payload["n_quads"] == len(corpus.quads)

    html = render_html(payload)
    assert html.startswith("<!doctype html>")
    # embedded JSON parses (the __DATA__ substitution produced valid JSON)
    m = re.search(r"const DATA = (\{.*?\});\n", html, re.S)
    assert m, "DATA blob not found"
    parsed = json.loads(m.group(1))
    assert len(parsed["docs"]) == len(payload["docs"])
    assert parsed["n_quads"] == payload["n_quads"]

    # every doc id appears in the page source
    for d in payload["docs"]:
        assert d["id"] in html

    # quad current flags: accurate quads true, conflicts rendered as expected-✗
    doc0 = payload["docs"][0]
    for rev in doc0["revisions"]:
        assert len(rev["quad_current"]) == len(rev["quads"])
        for q, cur in zip(rev["quads"], rev["quad_current"]):
            if q["claim_status"] == "accurate":
                assert cur is True, f"accurate quad not current: {q}"
            else:
                assert cur is None


def test_current_check_handles_nonfunctional_predicates():
    """depends_on holds several tails at once; the stated tail must match
    ANY active segment, not the first (regression from pilot review)."""
    from datetime import date

    from kg_data.schema import (
        Author, Doc, Entity, Mention, Predicate, Quad, Revision, Timeline,
        World,
    )

    w = World(id="w", seed=1, start=date(2022, 1, 1), end=date(2026, 6, 30),
              entities=[Entity(id="a", name="svc-a", type="service"),
                        Entity(id="b", name="svc-b", type="service"),
                        Entity(id="c", name="svc-c", type="service")],
              predicates=[Predicate(id="depends_on", label="depends on",
                                    volatility="medium", functional=False,
                                    head_type="service", tail_type="service")],
              timelines=[Timeline(predicate_id="depends_on", segments=[
                  Segment(head_id="a", tail_id="b", valid_from=date(2015, 1, 1)),
                  Segment(head_id="a", tail_id="c", valid_from=date(2015, 1, 1)),
              ])])
    doc = Doc(id="w-d0", world_id="w", channel="slack", doc_type="thread",
              title="", author=Author(name="x"),
              created_at=date(2024, 1, 1),
              revisions=[Revision(rev_id="w-d0#r0", sim_time=date(2024, 1, 1),
                                  text="svc-a depends on svc-c")])
    quad = Quad(id="q", world_id="w", doc_id="w-d0", rev_id="w-d0#r0",
                head=Mention(entity_id="a", text="svc-a", start=0, end=6),
                tail=Mention(entity_id="c", text="svc-c", start=19, end=25),
                relation="depends_on", valid_from=date(2015, 1, 1))
    payload = build_payload([w], [doc], [quad], [])
    assert payload["docs"][0]["revisions"][0]["quad_current"] == [True]
