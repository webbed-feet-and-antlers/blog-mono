from adapters.icews.normalize import normalize, parse_tab
from kg_pipeline.verify import sanitize_docs
from tests.helpers import icews_rows


def test_normalize_old_style_columns():
    docs, issues = normalize(icews_rows())
    assert len(docs) == 2
    assert issues["missing-actor-or-code"] == 1

    d = docs[0]
    assert d.text == "RUSSIA Use conventional military force UNITED STATES."
    assert d.channel == "news"
    assert d.doc_time == "2014-03-05"
    q = d.quads[0]
    assert q.rel == "CAMEO:190"
    assert q.head_id == "RUSGOV" and q.tail_id == "USAGOV"
    assert q.time.from_date == "2014-03-05" and q.time.granularity == "day"
    assert q.time.explicit_in_text is False  # date not stated in reconstructed text
    assert d.provenance.extraction == "gold"
    d.validate()


def test_normalize_lowercase_column_aliases():
    docs, _ = normalize(icews_rows())
    d = docs[1]
    assert d.quads[0].rel == "CAMEO:183"
    assert d.quads[0].head_id == "POLGOV"
    assert d.doc_time == "2015-06-01"
    assert d.doc_id.startswith("200000002")
    d.validate()


def test_sanitize_passes_normalized_docs():
    docs, _ = normalize(icews_rows())
    cleaned, report = sanitize_docs(docs)
    assert len(cleaned) == 2
    assert report.entity_drops == 0 and report.quad_drops == 0


def test_parse_tab(tmp_path):
    tab = tmp_path / "events.tab"
    tab.write_text(
        "Event ID\tEvent Date\tSource Actor Name\tTarget Actor Name\tEvent Code\tCAMEO Code Description\n"
        "1\t2014-03-05\tRUSSIA\tUNITED STATES\t190\tFight\n"
        "broken-line-with-wrong-column-count\n",
        encoding="utf-8",
    )
    rows, skipped = parse_tab(tab)
    assert skipped == 1
    assert rows[0]["Source Actor Name"] == "RUSSIA"
    docs, _ = normalize(rows)
    assert docs[0].quads[0].rel == "CAMEO:190"
