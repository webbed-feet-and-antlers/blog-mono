"""Shared fixture builders. Spans are always computed with str.find so the
fixtures can never drift out of sync with the text."""
from __future__ import annotations

import json
from pathlib import Path


def _span(text: str, surface: str):
    i = text.find(surface)
    assert i >= 0, f"{surface!r} not in {text!r}"
    return i, i + len(surface)


def _write_jsonl(path: Path, rows: list) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


# --- ICEWS rows ---------------------------------------------------------------


def icews_rows():
    return [
        {
            "Event ID": "100000001",
            "Event Date": "2014-03-05",
            "Source Actor Name": "RUSSIA",
            "Source Actor Code": "RUSGOV",
            "Target Actor Name": "UNITED STATES",
            "Target Actor Code": "USAGOV",
            "Event Code": "190",
            "CAMEO Code Description": "Use conventional military force",
            "Story ID": "1",
            "Sentence Number": "1",
        },
        {
            # newer-release style lowercase column names
            "event id": "200000002",
            "event date": "2015-06-01",
            "source actor name": "POLICE",
            "source actor code": "POLGOV",
            "target actor name": "PROTESTERS",
            "target actor code": "REB",
            "event code": "183",
            "cameo code description": "arrest, detain",
            "sentence number": "2",
        },
        {
            # missing target actor -> row must be skipped + counted
            "Event ID": "300000003",
            "Event Date": "2016-01-15",
            "Source Actor Name": "JAPAN",
            "Target Actor Name": "",
            "Event Code": "050",
            "CAMEO Code Description": "Make statement",
        },
    ]


# --- kg-data export builders (kgdata bridge fixtures) -------------------------

SYNTH_TEXT_R0 = "The auth-gateway depends on inventory-sync since launch."
SYNTH_TEXT_R1 = "auth-gateway now depends on billing-api."
VAL_TEXT = "Payments runs on Postgres."


def synth_record():
    """One kg-data synthetic record: slack doc, two revisions, conflict quad."""
    r0, r1 = SYNTH_TEXT_R0, SYNTH_TEXT_R1
    h0, h1 = _span(r0, "auth-gateway")
    t0, t1 = _span(r0, "inventory-sync")
    b0, b1 = _span(r1, "auth-gateway")
    g0, g1 = _span(r1, "billing-api")
    doc = {
        "id": "w00-d000", "world_id": "w00", "channel": "slack",
        "doc_type": "deploy chatter", "title": "",
        "author": {"name": "Dana", "role": "SRE", "offboarded_on": None},
        "created_at": "2023-09-01", "deleted_at": None,
        "revisions": [
            {"rev_id": "w00-d000#r0", "sim_time": "2023-09-01", "text": r0,
             "content_sha256": "x", "revision_kind": "initial"},
            {"rev_id": "w00-d000#r1", "sim_time": "2023-10-01", "text": r1,
             "content_sha256": "y", "revision_kind": "fact_changing"},
        ],
    }
    quads = [
        {"id": "w00-q0000", "world_id": "w00",
         "head": {"entity_id": "SERVICE_1", "text": "auth-gateway", "start": h0, "end": h1},
         "relation": "depends_on",
         "tail": {"entity_id": "SERVICE_2", "text": "inventory-sync", "start": t0, "end": t1},
         "valid_from": "2015-01-01", "valid_to": None,
         "time_source": "explicit_in_text", "claim_status": "accurate",
         "doc_id": "w00-d000", "rev_id": "w00-d000#r0"},
        {"id": "w00-q0001", "world_id": "w00",
         "head": {"entity_id": "SERVICE_1", "text": "auth-gateway", "start": b0, "end": b1},
         "relation": "depends_on",
         "tail": {"entity_id": "SERVICE_0", "text": "billing-api", "start": g0, "end": g1},
         "valid_from": "2023-10-01", "valid_to": None,
         "time_source": "doc_date", "claim_status": "conflict_injected",
         "doc_id": "w00-d000", "rev_id": "w00-d000#r1"},
    ]
    return {
        "kind": "synthetic",
        "doc": doc,
        "quads": quads,
        "entity_types": {"SERVICE_1": "service", "SERVICE_2": "service",
                         "SERVICE_0": "service", "COMPANY": "company"},
    }


def public_trex_record(doc_id="trex-Berlin", text="Berlin is the capital of Germany.",
                       head_surface="Berlin", tail_surface="Germany", time_source="none"):
    b, e = _span(text, head_surface)
    g, h = _span(text, tail_surface)
    return {
        "kind": "trex",
        "doc": {
            "id": doc_id, "world_id": "trex", "channel": "confluence",
            "doc_type": "wikipedia abstract", "title": doc_id,
            "author": {"name": "wikipedia", "role": "public corpus", "offboarded_on": None},
            "created_at": "2020-01-01", "deleted_at": None,
            "revisions": [{"rev_id": f"{doc_id}#r0", "sim_time": "2020-01-01",
                           "text": text, "content_sha256": "z", "revision_kind": "initial"}],
        },
        "quads": [{
            "id": "q0", "world_id": "trex",
            "head": {"entity_id": head_surface, "text": head_surface, "start": b, "end": e},
            "relation": "P17",
            "tail": {"entity_id": tail_surface, "text": tail_surface, "start": g, "end": h},
            "valid_from": None, "valid_to": None,
            "time_source": time_source, "claim_status": "accurate",
            "doc_id": doc_id, "rev_id": f"{doc_id}#r0",
        }],
    }


def build_synth_export(root: Path) -> Path:
    """Miniature kg-data stage-7 synthetic export: w00 train, w01 val."""
    v0, v1 = _span(VAL_TEXT, "Payments")
    p0, p1 = _span(VAL_TEXT, "Postgres")
    w00 = {"id": "w00", "seed": 1, "lore": "", "start": "2015-01-01", "end": "2024-01-01",
           "split": "train",
           "entities": [{"id": "COMPANY", "name": "Acme", "type": "company", "aliases": []},
                        {"id": "SERVICE_1", "name": "auth-gateway", "type": "service", "aliases": []},
                        {"id": "SERVICE_2", "name": "inventory-sync", "type": "service", "aliases": []},
                        {"id": "SERVICE_0", "name": "billing-api", "type": "service", "aliases": []}],
           "predicates": [{"id": "depends_on", "label": "depends on", "volatility": "medium",
                           "functional": False, "head_type": "service", "tail_type": "service"}],
           "timelines": []}
    w01 = {"id": "w01", "seed": 2, "lore": "", "start": "2015-01-01", "end": "2024-01-01",
           "split": "val",
           "entities": [{"id": "SERVICE_9", "name": "Payments", "type": "service", "aliases": []}],
           "predicates": [], "timelines": []}
    val_doc = {
        "id": "w01-d000", "world_id": "w01", "channel": "confluence",
        "doc_type": "runbook", "title": "", "author": {"name": "Kim", "role": "", "offboarded_on": None},
        "created_at": "2023-01-01", "deleted_at": None,
        "revisions": [{"rev_id": "w01-d000#r0", "sim_time": "2023-01-01",
                       "text": VAL_TEXT, "content_sha256": "v", "revision_kind": "initial"}],
    }
    val_quad = {"id": "w01-q0000", "world_id": "w01",
                "head": {"entity_id": "SERVICE_9", "text": "Payments", "start": v0, "end": v1},
                "relation": "runs_on_database",
                "tail": {"entity_id": "SERVICE_9", "text": "Postgres", "start": p0, "end": p1},
                "valid_from": "2016-06-01", "valid_to": None,
                "time_source": "explicit_in_text", "claim_status": "accurate",
                "doc_id": "w01-d000", "rev_id": "w01-d000#r0"}
    rec = synth_record()
    _write_jsonl(root / "worlds.jsonl", [w00, w01])
    _write_jsonl(root / "corpus" / "train" / "docs.jsonl", [rec["doc"]])
    _write_jsonl(root / "gold" / "train" / "quads.jsonl", rec["quads"])
    _write_jsonl(root / "corpus" / "val" / "docs.jsonl", [val_doc])
    _write_jsonl(root / "gold" / "val" / "quads.jsonl", [val_quad])
    return root


def build_public_export(root: Path, name: str = "trex") -> Path:
    """Miniature kg-data public export (train + val splits)."""
    train = public_trex_record("trex-Berlin")
    val = public_trex_record("trex-Kolkata", "Kolkata is the capital of India.",
                            head_surface="Kolkata", tail_surface="India")
    _write_jsonl(root / "train" / "docs.jsonl", [train["doc"]])
    _write_jsonl(root / "train" / "quads.jsonl", train["quads"])
    _write_jsonl(root / "val" / "docs.jsonl", [val["doc"]])
    _write_jsonl(root / "val" / "quads.jsonl", val["quads"])
    return root


SILVER_TEXT = "Subject: gas schedule\n\nThe gas desk reports to John Arnold."


def silver_record(doc_id="sil-enron-0000", text=SILVER_TEXT):
    """One kg-data silver record: real Enron email, LLM-extracted quad with
    slug relation, null bounds, doc_date time source, real send date."""
    b, e = _span(text, "gas desk")
    g, h = _span(text, "John Arnold")
    return {
        "kind": "silver",
        "doc": {
            "id": doc_id, "world_id": "silver_enron", "channel": "email",
            "doc_type": "email", "title": "gas schedule",
            "author": {"name": "user0@enron.com", "role": "real corpus",
                       "offboarded_on": None},
            "created_at": "2001-05-14", "deleted_at": None,
            "revisions": [{"rev_id": f"{doc_id}#r0", "sim_time": "2001-05-14",
                           "text": text, "content_sha256": "s",
                           "revision_kind": "initial"}],
        },
        "quads": [{
            "id": "silq-enron-00000", "world_id": "silver_enron",
            "head": {"entity_id": "gas desk", "text": "gas desk", "start": b, "end": e},
            "relation": "reports_to",
            "tail": {"entity_id": "John Arnold", "text": "John Arnold", "start": g, "end": h},
            "valid_from": None, "valid_to": None,
            "time_source": "doc_date", "claim_status": "accurate",
            "doc_id": doc_id, "rev_id": f"{doc_id}#r0",
            "discovered": False, "hedged": False,
        }],
    }


def build_silver_export(root: Path, name: str = "silver_enron") -> Path:
    """Miniature kg-data silver export (train + val + a holdout/ dir that
    the bridge must never read)."""
    train = silver_record()
    holdout = silver_record("sil-enron-9999")
    _write_jsonl(root / name / "train" / "docs.jsonl", [train["doc"]])
    _write_jsonl(root / name / "train" / "quads.jsonl", train["quads"])
    _write_jsonl(root / name / "val" / "docs.jsonl", [holdout["doc"]])
    _write_jsonl(root / name / "val" / "quads.jsonl", holdout["quads"])
    _write_jsonl(root / name / "holdout" / "docs.jsonl", [holdout["doc"]])
    _write_jsonl(root / name / "holdout" / "quads.jsonl", holdout["quads"])
    return root / name
