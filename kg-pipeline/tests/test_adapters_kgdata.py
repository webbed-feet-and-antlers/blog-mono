import json

import pytest

from adapters.kgdata.normalize import load_input, normalize
from kg_pipeline.verify import sanitize_docs
from tests.helpers import (
    SYNTH_TEXT_R0,
    build_public_export,
    build_synth_export,
    public_trex_record,
    synth_record,
)


def test_normalize_synthetic_revisions_and_namespacing():
    docs, issues = normalize([synth_record()])
    assert not issues
    assert len(docs) == 2  # one canonical Document per revision

    d0, d1 = docs
    assert d0.channel == "slack" and d0.doc_time == "2023-09-01"
    assert d0.text == SYNTH_TEXT_R0
    assert d0.quads[0].rel == "COMPANY:depends_on"
    assert d0.quads[0].time.explicit_in_text is True  # time_source explicit_in_text
    assert d0.quads[0].time.from_date == "2015-01-01"
    assert d0.provenance.extraction == "synthetic"

    assert d1.doc_time == "2023-10-01"
    assert d1.quads[0].time.explicit_in_text is False  # time_source doc_date
    assert d1.quads[0].claim_status == "conflict_injected"

    ent = next(e for e in d0.entities if e.el_id == "SERVICE_1")
    assert ent.ner_label == "SERVICE"  # entity type from world definitions
    d0.validate()
    d1.validate()


def test_normalize_public_trex_passes_pids_through():
    docs, _ = normalize([public_trex_record()])
    d = docs[0]
    assert d.quads[0].rel == "P17"  # never COMPANY-namespaced
    assert d.quads[0].time is None  # no temporal bounds in relbert/t_rex rows
    assert d.channel == "confluence"
    assert d.provenance.extraction == "distant"
    d.validate()


def test_normalize_wikidata_kb_qualifier_time():
    rec = public_trex_record(time_source="kb_qualifier")
    rec["kind"] = "wikidata"
    rec["quads"][0]["valid_from"] = "2009-01-20"
    rec["quads"][0]["valid_to"] = "2017-01-20"
    docs, _ = normalize([rec])
    t = docs[0].quads[0].time
    assert t.is_interval and t.granularity == "day"
    assert t.explicit_in_text is False  # database time, not stated in text


def test_load_input_synthetic_split_filter(tmp_path):
    root = build_synth_export(tmp_path / "synthetic")
    all_records = load_input(root)
    assert len(all_records) == 2  # w00 + w01

    train = load_input(root, split="train")
    assert [r["doc"]["world_id"] for r in train] == ["w00"]
    assert train[0]["entity_types"]["SERVICE_1"] == "service"

    val = load_input(root, split="val")
    assert [r["doc"]["world_id"] for r in val] == ["w01"]
    assert val[0]["quads"][0]["relation"] == "runs_on_database"


def test_load_input_public_export(tmp_path):
    root = build_public_export(tmp_path / "trex")
    train = load_input(root, split="train")
    assert len(train) == 1 and train[0]["kind"] == "trex"
    assert train[0]["quads"][0]["relation"] == "P17"

    everything = load_input(root)
    assert len(everything) == 2  # train + val


def test_load_input_merged_file_rejects_split(tmp_path):
    path = tmp_path / "records.jsonl"
    path.write_text(json.dumps(synth_record()) + "\n", encoding="utf-8")
    assert len(load_input(path)) == 1
    with pytest.raises(SystemExit):
        load_input(path, split="train")


def test_sanitize_passes_normalized_exports(tmp_path):
    records = load_input(build_synth_export(tmp_path / "synthetic"))
    docs, report = sanitize_docs(normalize(records)[0])
    assert report.docs_out == 3  # w00's two revisions + w01's one
    assert report.entity_drops == 0 and report.quad_drops == 0
