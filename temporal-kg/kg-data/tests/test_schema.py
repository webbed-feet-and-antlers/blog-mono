"""Schema round-trips + ingestion log semantics."""

from datetime import date

from kg_data.schema import (
    Author, Doc, Revision, build_ingestion_log,
)


def _doc(**kw):
    base = dict(
        id="w00-d001", world_id="w00", channel="slack",
        doc_type="incident thread", title="", author=Author(name="Maya"),
        created_at=date(2023, 1, 5),
        revisions=[
            Revision(rev_id="w00-d001#r0", sim_time=date(2023, 1, 5),
                     text="first"),
            Revision(rev_id="w00-d001#r1", sim_time=date(2024, 2, 1),
                     text="second", revision_kind="fact_changing"),
        ],
    )
    base.update(kw)
    return Doc(**base)


def test_revision_hash_matches_text():
    rev = Revision(rev_id="x#r0", sim_time=date(2023, 1, 1), text="hello")
    import hashlib

    assert rev.content_sha256 == hashlib.sha256(b"hello").hexdigest()


def test_ingestion_log_ordering_and_completeness():
    docs = [
        _doc(),
        _doc(id="w00-d002", deleted_at=date(2025, 3, 3), revisions=[
            Revision(rev_id="w00-d002#r0", sim_time=date(2022, 6, 1),
                     text="gone soon"),
        ]),
    ]
    log = build_ingestion_log(docs)
    assert [(e.event, e.doc_id) for e in log] == [
        ("created", "w00-d002"),
        ("created", "w00-d001"),
        ("revised", "w00-d001"),
        ("deleted", "w00-d002"),
    ]
    times = [e.sim_time for e in log]
    assert times == sorted(times)


def test_model_round_trip(tmp_path):
    import json

    doc = _doc()
    restored = Doc.model_validate(json.loads(doc.model_dump_json()))
    assert restored == doc
