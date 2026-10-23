import json
from pathlib import Path

import pytest

from kg_pipeline.paths import safe_out_dir
from kg_pipeline.registry import AdapterMeta, discover_adapters, mixture_weights
from kg_pipeline.store import read_docs, write_docs

REPO_ROOT = Path(__file__).resolve().parents[1]


def make_doc():
    from kg_pipeline.schema import Document, Entity, Provenance, Quad

    text = "Berlin is the capital of Germany."
    b = text.find("Berlin")
    g = text.find("Germany")
    return Document(
        doc_id="d1", source="test", channel="wiki", text=text, doc_time=None,
        entities=[Entity(b, b + 6, "Berlin", "LOC", "Q64"), Entity(g, g + 7, "Germany", "LOC", "Q183")],
        quads=[Quad([b, b + 6], "P17", [g, g + 7], "Q64", "Q183", None)],
        provenance=Provenance("test", "d1", "distant"),
    )


def test_discover_adapters():
    metas = discover_adapters(REPO_ROOT)
    assert set(metas) == {"icews", "kgdata"}
    assert all(isinstance(m, AdapterMeta) for m in metas.values())
    assert metas["icews"].url.startswith("https://dataverse.harvard.edu/")


def test_mixture_weights_normalized():
    metas = discover_adapters(REPO_ROOT)
    weights = mixture_weights(metas)
    assert weights == pytest.approx({"icews": 0.5, "kgdata": 0.5})


def test_store_round_trip(tmp_path):
    doc = make_doc()
    paths = write_docs([doc], tmp_path, "trex", tmp_path)
    assert paths["jsonl"].exists() and paths["manifest"].exists()

    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    assert manifest["doc_count"] == 1 and manifest["source"] == "trex"
    assert len(manifest["sha256"]) == 64

    restored = list(read_docs(paths["jsonl"]))
    assert restored[0].to_dict() == doc.to_dict()
    restored[0].validate()


def test_store_rejects_unsafe_source_name(tmp_path):
    with pytest.raises(ValueError):
        write_docs([], tmp_path, "../escape", tmp_path)


def test_safe_out_dir_guards(tmp_path):
    assert safe_out_dir(tmp_path / "sub", tmp_path) == (tmp_path / "sub").resolve()
    with pytest.raises(ValueError):
        safe_out_dir(tmp_path / ".." / "elsewhere", tmp_path)  # '..' outright
    with pytest.raises(ValueError):
        safe_out_dir("/etc", tmp_path)  # outside allowed root
