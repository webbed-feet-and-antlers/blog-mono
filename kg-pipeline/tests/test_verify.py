from kg_pipeline.schema import Document, Entity, Provenance, Quad
from kg_pipeline.verify import dedup_quads, sanitize_docs


def make_doc(text="Berlin is the capital of Germany.", doc_id="d1"):
    b = text.find("Berlin")
    g = text.find("Germany")
    return Document(
        doc_id=doc_id, source="test", channel="wiki", text=text, doc_time=None,
        entities=[Entity(b, b + 6, "Berlin", "LOC", "Q64"), Entity(g, g + 7, "Germany", "LOC", "Q183")],
        quads=[Quad([b, b + 6], "P17", [g, g + 7], "Q64", "Q183", None)],
        provenance=Provenance("test", doc_id, "distant"),
    )


def test_sanitize_drops_invalid_entity_and_its_quad():
    doc = make_doc()
    doc.entities[1].surface = "Germania"  # span mismatch -> entity dropped
    docs, report = sanitize_docs([doc])
    assert report.entity_drops == 1
    assert report.quad_drops == 1  # the quad referenced the dropped entity
    assert report.docs_out == 1
    assert len(docs[0].quads) == 0
    assert report.reasons["quad-span-not-an-entity"] == 1


def test_sanitize_drops_invalid_quad_only():
    doc = make_doc()
    doc.quads[0].rel = "bad rel"  # valid spans, invalid label
    docs, report = sanitize_docs([doc])
    assert report.entity_drops == 0
    assert report.quad_drops == 1
    assert report.reasons["invalid-quad"] == 1


def test_sanitize_drops_empty_text_doc():
    docs, report = sanitize_docs([Document("d", "test", "wiki", "", None, [], [], Provenance())])
    assert docs == []
    assert report.doc_drops == 1


def test_dedup_across_docs():
    d1, d2 = make_doc(doc_id="d1"), make_doc(doc_id="d2")  # identical quad
    docs, removed = dedup_quads([d1, d2])
    assert removed == 1
    assert len(docs[1].quads) == 0
