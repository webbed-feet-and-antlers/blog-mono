import json

from kg_pipeline.__main__ import main
from kg_pipeline.store import read_docs
from tests.helpers import icews_rows, trex_records


def _write_jsonl(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def test_cli_icews_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    inp = _write_jsonl(tmp_path / "rows.jsonl", icews_rows())

    rc = main(["--source", "icews", "--input", str(inp), "--out", "out"])
    assert rc == 0

    out = tmp_path / "out"
    docs_path = out / "normalized" / "icews" / "docs.jsonl"
    assert docs_path.exists()
    assert (out / "normalized" / "icews" / "manifest.json").exists()

    docs = list(read_docs(docs_path))
    assert len(docs) == 2
    for d in docs:
        d.validate()

    re_lines = (out / "unified" / "icews" / "re.jsonl").read_text(encoding="utf-8").splitlines()
    rec = json.loads(re_lines[0])
    assert rec["labels"][0]["r"] == "CAMEO:190"
    text = " ".join(" ".join(s) for s in rec["sents"])  # notebook reconstruction
    assert text.find(rec["vertexSet"][rec["labels"][0]["h"]][0]["name"]) >= 0

    stdout = capsys.readouterr().out
    assert "docs=2" in stdout and "missing-actor-or-code=1" in stdout


def test_cli_trex_with_qualifier_cache(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    inp = _write_jsonl(tmp_path / "records.jsonl", trex_records())
    cache = tmp_path / "qualifiers.json"
    cache.write_text('{"Q64|P17|Q183": {"from": "1871"}}', encoding="utf-8")

    rc = main([
        "--source", "trex", "--input", str(inp),
        "--qualifiers", str(cache), "--out", "out",
    ])
    assert rc == 0

    docs = list(read_docs(tmp_path / "out" / "normalized" / "trex" / "docs.jsonl"))
    assert len(docs) == 3
    t = docs[0].quads[0].time
    assert t is not None and t.from_date == "1871" and t.granularity == "year"
    assert t.explicit_in_text is False

    el_lines = (tmp_path / "out" / "unified" / "trex" / "el.jsonl").read_text(encoding="utf-8").splitlines()
    mentions = json.loads(el_lines[0])["mentions"]
    assert {m["kb_id"] for m in mentions} == {"Q64", "Q183"}
