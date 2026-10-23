"""Export: split disjointness by world, manifest determinism."""

import random

from kg_data.export import assign_splits, export_corpus
from kg_data.schema import Author, Doc, EvalQuestion, Revision


def _worlds(n=5):
    from conftest import fake_naming
    from kg_data.worldgen import build_scaffold, instantiate_world

    out = []
    for i in range(n):
        scaffold = build_scaffold(random.Random(100 + i))
        out.append(instantiate_world(f"w{i:02d}", 100 + i, scaffold,
                                     fake_naming()))
    return out


def _docs_quads(worlds):
    docs, quads, questions = [], [], []
    for w in worlds:
        d = Doc(id=f"{w.id}-d000", world_id=w.id, channel="slack",
                doc_type="thread", title="", author=Author(name="x"),
                created_at=w.start,
                revisions=[Revision(rev_id=f"{w.id}-d000#r0",
                                    sim_time=w.start, text="t")])
        docs.append(d)
        questions.append(EvalQuestion(
            id=f"{w.id}-eq000", world_id=w.id, kind="current_state",
            question="?", as_of=w.end, answer="a"))
    return docs, quads, questions


def test_splits_disjoint_by_world(tmp_path, monkeypatch):
    monkeypatch.setattr("kg_data.export.DATA_DIR", tmp_path)
    monkeypatch.setattr("kg_data.export.SYNTH_DIR", tmp_path / "synthetic")
    worlds = _worlds(6)
    docs, quads, questions = _docs_quads(worlds)
    export_corpus(worlds, docs, quads, questions, seed=42)

    seen = [w.id for w in worlds]
    assert len(seen) == len(set(seen))
    splits = [w.split for w in worlds]
    assert set(splits) <= {"train", "val", "test"}

    # docs land only in their world's split
    for split in ("train", "val", "test"):
        path = tmp_path / "synthetic" / "corpus" / split / "docs.jsonl"
        if path.exists():
            wids = {w.id for w in worlds if w.split == split}
            for line in path.read_text().splitlines():
                import json

                assert json.loads(line)["world_id"] in wids


def test_manifest_deterministic(tmp_path, monkeypatch):
    def run(outdir):
        monkeypatch.setattr("kg_data.export.DATA_DIR", outdir)
        monkeypatch.setattr("kg_data.export.SYNTH_DIR", outdir / "synthetic")
        worlds = _worlds(4)
        docs, quads, questions = _docs_quads(worlds)
        return export_corpus(worlds, docs, quads, questions, seed=42)

    m1 = run(tmp_path / "a")
    m2 = run(tmp_path / "b")
    assert m1["files"] == m2["files"]
    assert m1["splits"] == m2["splits"]
