"""Silver stage: real-text doc wrapping, self-consistency voting,
audit-rule adjudication, and the export layout."""

import json
from datetime import date

from kg_data import llm, silver
from kg_data.adapters import trex
from kg_data.audit import AuditResult, AuditSelection
from kg_data.extract import ExtractedQuad, ExtractionResult
from kg_data.schema import Author, Doc, Mention, Quad, Revision


def _enron_rows(n: int) -> list[dict]:
    return [{
        "subject": f"note {i}",
        "body": (f"Body number {i} about the trading desk forecast "
                 f"schedule. " * 3),
        "date": "2001-05-14 23:39:00+00:00",
        "from": f"user{i}@enron.com",
    } for i in range(n)]


def _quad(doc_id: str, head: str, rel: str, tail: str, *,
          valid_from: date | None = None, start: int = 0) -> Quad:
    return Quad(
        id="", world_id="silver_enron", doc_id=doc_id,
        rev_id=f"{doc_id}#r0",
        head=Mention(entity_id=head, text=head, start=start,
                     end=start + len(head)),
        tail=Mention(entity_id=tail, text=tail, start=start + 20,
                     end=start + 20 + len(tail)),
        relation=rel, valid_from=valid_from,
    )


def _silver_doc(text: str = "billing-api runs on DynamoDB since forever. "
                            "Unrelated paragraph about lunch plans.",
                doc_id: str = "sil-enron-0000") -> Doc:
    return Doc(
        id=doc_id, world_id="silver_enron", channel="email",
        doc_type="email", author=Author(name="user0@enron.com"),
        created_at=date(2001, 5, 14),
        revisions=[Revision(rev_id=f"{doc_id}#r0",
                            sim_time=date(2001, 5, 14), text=text)],
    )


# --- slug + doc wrapping ------------------------------------------------------


def test_slug_relation():
    assert silver.slug_relation("runs on database") == "runs_on_database"
    assert silver.slug_relation("  Owned  By ") == "owned_by"
    assert silver.slug_relation("???") == "related_to"


def test_build_docs_enron_wraps_and_dates():
    docs = silver.build_docs("enron", _enron_rows(3), cap=10)
    assert len(docs) == 3
    d = docs[0]
    assert d.id == "sil-enron-0000" and d.world_id == "silver_enron"
    assert d.channel == "email" and d.title == "note 0"
    assert d.author.name == "user0@enron.com"
    assert d.revisions[0].sim_time == date(2001, 5, 14)  # real send date
    assert d.revisions[0].rev_id == "sil-enron-0000#r0"
    assert "Subject: note 0" in d.revisions[0].text


def test_build_docs_fallback_date_and_dedupe():
    rows = _enron_rows(2)
    for r in rows:
        del r["date"]  # rows without dates fall back to the corpus window
    rows.append(dict(rows[0]))  # duplicate body must collapse
    docs = silver.build_docs("enron", rows, cap=10)
    assert len(docs) == 2
    assert all(d.revisions[0].sim_time == date(2001, 8, 1) for d in docs)


def test_build_docs_ubuntu_cleans_markers():
    rows = [{"Context": "we should patch the package before the release "
                        "goes out to users",
             "Utterance": "agreed, the package needs a version bump soon"}]
    docs = silver.build_docs("ubuntu", rows, cap=10)
    assert len(docs) == 1
    text = docs[0].revisions[0].text
    assert "__eou__" not in text and "\n" in text
    assert docs[0].channel == "slack"
    assert docs[0].revisions[0].sim_time == date(2018, 4, 1)


# --- self-consistency voting --------------------------------------------------


async def test_label_doc_majority_vote_and_date_merge(monkeypatch):
    from kg_data import extract as extract_mod

    doc = _silver_doc()
    a1 = _quad(doc.id, "billing-api", "runs on database", "DynamoDB")
    a2 = _quad(doc.id, "billing-api", "runs on database", "DynamoDB",
               valid_from=date(2024, 1, 1))
    a3 = _quad(doc.id, "billing-api", "runs on database", "DynamoDB",
               valid_from=date(2024, 1, 1))
    b = _quad(doc.id, "auth-gateway", "owned by", "Identity")
    c = _quad(doc.id, "search-svc", "depends on", "ledger-api")
    samples = [[a1, b], [a2, c], [a3]]

    async def fake_extract(world, d, *, use_llm=True, semaphore=None,
                           temperature=0.0):
        assert temperature == 0.5  # voting samples must not be greedy
        return samples.pop(0)

    monkeypatch.setattr(extract_mod, "extract_doc", fake_extract)
    kept = await silver.label_doc(doc, votes=3)
    assert len(kept) == 1  # A agreed by all three; B and C appeared once
    q = kept[0]
    assert q.relation == "runs_on_database"  # slugified
    assert q.valid_from == date(2024, 1, 1)  # majority among instances
    assert q.time_source == "explicit_in_text"


async def test_label_doc_survives_one_failed_sample(monkeypatch):
    from kg_data import extract as extract_mod

    doc = _silver_doc()
    a1 = _quad(doc.id, "billing-api", "runs on database", "DynamoDB")
    a2 = _quad(doc.id, "billing-api", "runs on database", "DynamoDB")
    calls = {"n": 0}

    async def fake_extract(world, d, *, use_llm=True, semaphore=None,
                           temperature=0.0):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("empty response")
        return [a1 if calls["n"] == 1 else a2]

    monkeypatch.setattr(extract_mod, "extract_doc", fake_extract)
    kept = await silver.label_doc(doc, votes=3)
    assert len(kept) == 1  # 2-of-2 successful samples agree → majority


async def test_label_doc_no_dates_uses_doc_date(monkeypatch):
    from kg_data import extract as extract_mod

    doc = _silver_doc()
    doc.world_id = "silver_ubuntu"
    q = _quad(doc.id, "billing-api", "runs on database", "DynamoDB")

    async def fake_extract(world, d, *, use_llm=True, semaphore=None,
                           temperature=0.0):
        return [q]

    monkeypatch.setattr(extract_mod, "extract_doc", fake_extract)
    kept = await silver.label_doc(doc, votes=1)
    assert kept[0].time_source == "none"  # ubuntu has no real doc dates


# --- adjudication -------------------------------------------------------------


async def test_adjudicate_buckets(monkeypatch):
    doc = _silver_doc()  # "billing-api runs on DynamoDB" share a sentence
    same_sent = _quad(doc.id, "billing-api", "runs_on_database", "DynamoDB")
    no_comention = _quad(doc.id, "billing-api", "runs_on_database",
                         "Snowflake")  # tail never appears in any sentence

    async def fake_structured(model_cls, system, user, **kw):
        assert model_cls is AuditResult
        return AuditResult(
            assertions=[AuditSelection(fact_id="f0", hedged=True)],
            not_asserted=[AuditSelection(fact_id="f1")],
        )

    monkeypatch.setattr(llm, "structured", fake_structured)
    kept, stats = await silver.adjudicate_doc(doc, [same_sent, no_comention])
    assert [q.relation for q in kept] == ["runs_on_database"]
    assert kept[0].hedged is True  # asserted + hedged flag applied
    assert stats["dropped"] == 1  # denied + no co-occurrence → dropped


async def test_adjudicate_same_sentence_denial_kept(monkeypatch):
    doc = _silver_doc()
    q = _quad(doc.id, "billing-api", "runs_on_database", "DynamoDB")

    async def fake_structured(model_cls, system, user, **kw):
        return AuditResult(not_asserted=[AuditSelection(fact_id="f0")])

    monkeypatch.setattr(llm, "structured", fake_structured)
    kept, stats = await silver.adjudicate_doc(doc, [q])
    assert len(kept) == 1  # denial + same-sentence pair → corroborated, kept
    assert stats["denied_comention"] == 1 and stats["dropped"] == 0


async def test_adjudicate_fails_open(monkeypatch):
    doc = _silver_doc()
    quads = [_quad(doc.id, "billing-api", "runs_on_database", "DynamoDB")]

    async def boom(*a, **kw):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(llm, "structured", boom)
    kept, stats = await silver.adjudicate_doc(doc, quads)
    assert kept == quads and stats["audit_failures"] == 1


# --- export + end-to-end ------------------------------------------------------


def test_export_layout_and_holdout(tmp_path, monkeypatch):
    monkeypatch.setattr(silver, "DATA_DIR", tmp_path)
    monkeypatch.setattr(trex, "DATA_DIR", tmp_path)
    docs = silver.build_docs("enron", _enron_rows(12), cap=12)
    quads = [_quad(d.id, "trading desk", "reported to", "forecast schedule")
             for d in docs]
    counts = silver.export_silver("enron", docs, quads, holdout=3)

    base = tmp_path / "public" / "silver_enron"
    split_ids: set[str] = set()
    for split in ("train", "val", "test"):
        lines = (base / split / "docs.jsonl").read_text().splitlines()
        split_ids.update(json.loads(l)["id"] for l in lines)
        assert (base / split / "quads.jsonl").exists()
    holdout_ids = {json.loads(l)["id"] for l in
                   (base / "holdout" / "docs.jsonl").read_text().splitlines()}
    assert len(holdout_ids) == 3
    assert split_ids == {d.id for d in docs} - holdout_ids  # partition
    h_quads = [json.loads(l) for l in
               (base / "holdout" / "quads.jsonl").read_text().splitlines()]
    assert {q["doc_id"] for q in h_quads} <= holdout_ids
    assert counts["holdout/docs"] == 3


async def test_run_silver_offline_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(silver, "DATA_DIR", tmp_path)
    monkeypatch.setattr(trex, "DATA_DIR", tmp_path)
    monkeypatch.setattr(silver, "load_rows", lambda src: _enron_rows(8))

    async def fake_structured(model_cls, system, user, **kw):
        if model_cls is ExtractionResult:
            return ExtractionResult(quads=[
                ExtractedQuad(head="Body", relation="runs on database",
                              tail="trading", valid_from=None, valid_to=None),
                ExtractedQuad(head="nonexistent paraphrase", relation="x",
                              tail="y"),  # span check must drop this
            ])
        if model_cls is AuditResult:
            return AuditResult()  # empty buckets → fail-safe keep
        raise AssertionError(model_cls)

    monkeypatch.setattr(llm, "structured", fake_structured)
    stats = await silver.run_silver("enron", docs_cap=5, votes=3,
                                    use_llm=True, holdout=2)

    assert stats["docs"] == 7  # cap 5 + holdout 2
    assert stats["docs_with_quads"] == 7
    assert stats["voted_quads"] == 7  # one locatable quad per doc
    base = tmp_path / "public" / "silver_enron"
    holdout = [json.loads(l) for l in
               (base / "holdout" / "docs.jsonl").read_text().splitlines()]
    assert len(holdout) == 2
    quad_lines = (base / "train" / "quads.jsonl").read_text().splitlines()
    first = json.loads(quad_lines[0])
    assert first["relation"] == "runs_on_database"
    assert first["time_source"] == "doc_date"  # real Enron date, no explicit


def test_build_silver_review_page(tmp_path, monkeypatch):
    from kg_data import review

    monkeypatch.setattr(silver, "DATA_DIR", tmp_path)
    monkeypatch.setattr(trex, "DATA_DIR", tmp_path)
    monkeypatch.setattr(review, "DATA_DIR", tmp_path)
    docs = silver.build_docs("enron", _enron_rows(6), cap=6)
    quads = [_quad(d.id, "trading desk", "reported to", "forecast schedule")
             for d in docs]
    silver.export_silver("enron", docs, quads, holdout=2)

    out = review.build_silver_review("enron")
    html = out.read_text(encoding="utf-8")
    assert out.name == "review_silver_enron.html"
    assert '"silver": true' in html  # page knows: no gold-timeline checks
    assert "sil-enron-0000" in html  # train docs present
    assert "sil-enron-0004" in html  # holdout docs present too (the audit slice)
