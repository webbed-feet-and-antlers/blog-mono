from adapters.trex.normalize import normalize
from adapters.trex.qualifiers import QualifierLookup, build_sparql
from kg_pipeline.verify import sanitize_docs
from tests.helpers import trex_records


def test_normalize_basic_and_dangling_triple():
    docs, issues = normalize(trex_records())
    assert len(docs) == 3
    assert issues["triple-without-spans"] == 1  # the Dangling record

    d = docs[0]
    assert d.channel == "wiki"
    assert d.provenance.extraction == "distant"
    assert [e.el_id for e in d.entities] == ["Q64", "Q183"]
    q = d.quads[0]
    assert q.rel == "P17" and q.head_id == "Q64" and q.tail_id == "Q183"
    assert q.time is None  # no qualifier lookup -> no invented time
    d.validate()


def test_alias_record_shapes():
    docs, _ = normalize(trex_records())
    d = docs[1]  # dict-style entities (mention/start/end/uri), list-style triples
    assert [e.surface for e in d.entities] == ["Kolkata", "India"]
    assert d.quads[0].rel == "P17" and d.quads[0].head_id == "Q1348"
    d.validate()


def test_qualifier_enrichment():
    lookup = QualifierLookup({"Q64|P17|Q183": {"from": "1871"}})
    docs, _ = normalize(trex_records(), qualifier_lookup=lookup)
    t = docs[0].quads[0].time
    assert t.from_date == "1871"
    assert t.granularity == "year"
    assert t.explicit_in_text is False  # database time, not stated in abstract
    assert not t.is_interval


def test_qualifier_interval_and_granularity_coarsening():
    lookup = QualifierLookup({"Q64|P17|Q183": {"from": "2009-01-20", "to": "2017-01-20"}})
    docs, _ = normalize(trex_records(), qualifier_lookup=lookup)
    t = docs[0].quads[0].time
    assert t.is_interval and t.granularity == "day"

    mixed = QualifierLookup({"Q64|P17|Q183": {"from": "1871", "to": "1990-10-03"}})
    docs, _ = normalize(trex_records(), qualifier_lookup=mixed)
    assert docs[0].quads[0].time.granularity == "year"  # coarser bound wins


def test_sanitize_passes_all_normalized_docs():
    docs, _ = normalize(trex_records())
    cleaned, report = sanitize_docs(docs)
    assert len(cleaned) == 3
    assert report.entity_drops == 0 and report.quad_drops == 0


def test_make_lookup_reads_json(tmp_path):
    cache = tmp_path / "qualifiers.json"
    cache.write_text('{"Q64|P17|Q183": {"from": "1871"}}', encoding="utf-8")
    from adapters.trex.normalize import make_lookup

    lookup = make_lookup(cache)
    assert lookup.get(("Q64", "P17", "Q183")) == {"from": "1871"}


def test_build_sparql_mentions_qualifiers():
    q = build_sparql("Q64", "P17", "Q183")
    assert "pq:P580" in q and "pq:P582" in q and "wd:Q64" in q
