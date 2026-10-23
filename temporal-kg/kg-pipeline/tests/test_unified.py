from kg_pipeline.schema import Document, Entity, Provenance, Quad
from kg_pipeline.unified import to_unified_el, to_unified_ner, to_unified_re


def make_doc(text="Berlin is the capital of Germany."):
    b = text.find("Berlin")
    g = text.find("Germany")
    return Document(
        doc_id="d1",
        source="test",
        channel="wiki",
        text=text,
        doc_time=None,
        entities=[
            Entity(b, b + 6, "Berlin", "LOC", "Q64"),
            Entity(g, g + 7, "Germany", "LOC", "Q183"),
        ],
        quads=[Quad([b, b + 6], "P17", [g, g + 7], "Q64", "Q183", None)],
        provenance=Provenance("test", "d1", "distant"),
    )


def test_to_unified_ner():
    rec = to_unified_ner(make_doc())
    assert rec["entities"] == [
        {"entity": "Berlin", "type": "LOC"},
        {"entity": "Germany", "type": "LOC"},
    ]


def test_to_unified_re_survives_notebook_reconstruction():
    doc = make_doc()
    rec = to_unified_re(doc)
    # exactly what UnifiedDataset._build_re does
    text = " ".join(" ".join(s) for s in rec["sents"])
    label = rec["labels"][0]
    h_name = rec["vertexSet"][label["h"]][0]["name"]
    t_name = rec["vertexSet"][label["t"]][0]["name"]
    assert label["r"] == "P17"
    assert h_name == "Berlin" and text.find(h_name) >= 0
    assert t_name == "Germany" and text.find(t_name) >= 0


def test_to_unified_re_normalizes_whitespace():
    text = "Berlin  is the capital of\nGermany."  # double space + newline
    doc = make_doc(text)
    rec = to_unified_re(doc)
    reconstructed = " ".join(" ".join(s) for s in rec["sents"])
    for label in rec["labels"]:
        for idx in (label["h"], label["t"]):
            assert reconstructed.find(rec["vertexSet"][idx][0]["name"]) >= 0


def test_to_unified_el():
    rec = to_unified_el(make_doc())
    assert [m["kb_id"] for m in rec["mentions"]] == ["Q64", "Q183"]

    doc = make_doc()
    for e in doc.entities:
        e.el_id = None
    assert to_unified_el(doc) is None


def test_same_surface_merges_to_one_vertex():
    text = "Berlin and Berlin again."
    b1 = text.find("Berlin")
    b2 = text.find("Berlin", b1 + 1)
    doc = Document(
        doc_id="d2", source="test", channel="wiki", text=text, doc_time=None,
        entities=[Entity(b1, b1 + 6, "Berlin", "LOC", "Q64"), Entity(b2, b2 + 6, "Berlin", "LOC", "Q64")],
        quads=[Quad([b1, b1 + 6], "P1759", [b2, b2 + 6], "Q64", "Q64", None)],
        provenance=Provenance("test", "d2", "distant"),
    )
    doc.quads[0].head_span = [b1, b1 + 6]  # spans differ, surface same
    rec = to_unified_re(doc)
    assert len(rec["vertexSet"]) == 1
    assert rec["labels"][0]["h"] == rec["labels"][0]["t"] == 0
