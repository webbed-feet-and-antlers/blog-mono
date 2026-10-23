import json

from kg_pipeline.__main__ import main
from kg_pipeline.store import read_docs
from tests.helpers import build_synth_export, icews_rows


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


def test_cli_kgdata_synthetic_export_with_split(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    export = build_synth_export(tmp_path / "synthetic")

    rc = main(["--source", "kgdata", "--input", str(export),
               "--split", "train", "--out", "out"])
    assert rc == 0

    out = tmp_path / "out"
    docs_path = out / "normalized" / "kgdata_train" / "docs.jsonl"
    assert docs_path.exists()  # split-suffixed: never clobbers kgdata_val/test

    docs = list(read_docs(docs_path))
    assert len(docs) == 2  # both revisions of the w00 slack doc
    for d in docs:
        d.validate()
        assert d.channel == "slack"

    re_lines = (out / "unified" / "kgdata_train" / "re.jsonl").read_text(encoding="utf-8").splitlines()
    rec = json.loads(re_lines[0])
    assert rec["labels"][0]["r"] == "COMPANY:depends_on"

    el_lines = (out / "unified" / "kgdata_train" / "el.jsonl").read_text(encoding="utf-8").splitlines()
    mentions = json.loads(el_lines[0])["mentions"]
    assert "SERVICE_1" in {m["kb_id"] for m in mentions}

    assert "docs=2" in capsys.readouterr().out


def test_cli_split_unsupported_for_jsonl_adapters(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    inp = _write_jsonl(tmp_path / "rows.jsonl", icews_rows())
    try:
        main(["--source", "icews", "--input", str(inp), "--split", "train", "--out", "out"])
    except SystemExit as exc:
        assert "no load_input" in str(exc)
    else:
        raise AssertionError("expected SystemExit for --split on a JSONL adapter")
