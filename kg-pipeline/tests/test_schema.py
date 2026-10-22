import pytest

from kg_pipeline.schema import (
    Document,
    Entity,
    Provenance,
    Quad,
    SchemaError,
    TimeBound,
)


def make_doc():
    text = "Obama was president of the United States from 2009 to 2017."
    o = text.find("Obama")
    u = text.find("United States")
    return Document(
        doc_id="t1",
        source="test",
        channel="wiki",
        text=text,
        doc_time="2017-01-20",
        entities=[
            Entity(o, o + 5, "Obama", "PER", "Q76"),
            Entity(u, u + 13, "United States", "LOC", "Q30"),
        ],
        quads=[
            Quad([o, o + 5], "P39", [u, u + 13], "Q76", "Q30",
                 TimeBound("2009-01-20", "2017-01-20", "day", False))
        ],
        provenance=Provenance("test-license", "orig-1", "gold"),
    )


def test_valid_document_passes():
    make_doc().validate()


def test_round_trip():
    doc = make_doc()
    restored = Document.from_dict(doc.to_dict())
    assert restored.to_dict() == doc.to_dict()
    restored.validate()


def test_bad_channel_rejected():
    doc = make_doc()
    doc.channel = "graffiti"
    with pytest.raises(SchemaError):
        doc.validate()


def test_entity_surface_mismatch_rejected():
    doc = make_doc()
    doc.entities[0].surface = "Obbama"
    with pytest.raises(SchemaError):
        doc.validate()


def test_entity_span_out_of_range_rejected():
    doc = make_doc()
    doc.entities.append(Entity(500, 900, "nowhere", "LOC"))
    with pytest.raises(SchemaError):
        doc.validate()


def test_identical_head_tail_rejected():
    doc = make_doc()
    span = doc.quads[0].head_span
    doc.quads[0].tail_span = list(span)
    with pytest.raises(SchemaError):
        doc.validate()


def test_rel_with_whitespace_rejected():
    doc = make_doc()
    doc.quads[0].rel = "lives in"
    with pytest.raises(SchemaError):
        doc.validate()


def test_rel_with_trainer_prefix_rejected():
    doc = make_doc()
    doc.quads[0].rel = "[REL] P39"
    with pytest.raises(SchemaError):
        doc.validate()


def test_time_granularity_mismatch_rejected():
    doc = make_doc()
    doc.quads[0].time.from_date = "2009"
    with pytest.raises(SchemaError):
        doc.validate()


def test_bad_extraction_kind_rejected():
    doc = make_doc()
    doc.provenance.extraction = "vibes"
    with pytest.raises(SchemaError):
        doc.validate()


def test_alias_parsing():
    e = Entity.from_dict({"start": 0, "end": 5, "entity": "Obama", "type": "PER", "qid": "Q76"})
    assert (e.span_start, e.span_end, e.surface, e.ner_label, e.el_id) == (0, 5, "Obama", "PER", "Q76")

    t = TimeBound.from_dict({"from_date": "2009-01-20", "to_date": "2017-01-20", "granularity": "day"})
    assert t.from_date == "2009-01-20" and t.is_interval

    q = Quad.from_dict({"h": [0, 5], "t": [10, 23], "predicate": "P39", "subject": "Q76", "object": "Q30"})
    assert q.rel == "P39" and q.head_id == "Q76"
