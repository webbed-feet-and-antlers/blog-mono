"""Baseline extractor: canned LLM output → spans located, bad mentions dropped."""

from datetime import date

from kg_data.extract import extract_doc
from kg_data.schema import Author, Doc, Revision


def _doc():
    return Doc(
        id="w00-d001", world_id="w00", channel="confluence",
        doc_type="architecture spec", title="",
        author=Author(name="Maya", role="staff engineer"),
        created_at=date(2024, 1, 10),
        revisions=[Revision(rev_id="w00-d001#r0", sim_time=date(2024, 1, 10),
                            text="The billing-api runs on PostgreSQL in prod. "
                                 "It is owned by Payments.")],
    )


async def test_extract_drops_unlocatable_mentions(fake_llm):
    quads = await extract_doc(None, _doc())
    # canned response: one locatable quad + one hallucinated mention
    assert len(quads) == 1
    q = quads[0]
    doc = _doc()
    text = doc.latest_revision.text
    assert text[q.head.start:q.head.end] == "billing-api"
    assert text[q.tail.start:q.tail.end] == "PostgreSQL"
    assert q.valid_from == date(2023, 1, 1)
    assert fake_llm.calls.count("ExtractionResult") == 1


async def test_extract_spans_verbatim(fake_llm):
    for q in await extract_doc(None, _doc()):
        doc = _doc()
        text = doc.latest_revision.text
        assert q.head.text in text
        assert q.tail.text in text
