import pytest

from kg_pipeline.labels import namespace_rel, prefixed
from kg_pipeline.timeutil import (
    coarser_granularity,
    granularity_of,
    normalize_time,
    time_appears_in_text,
    valid_iso,
)


def test_normalize_time_variants():
    assert normalize_time("2009") == ("2009", "year")
    assert normalize_time("2009-01") == ("2009-01", "month")
    assert normalize_time("2009-01-20") == ("2009-01-20", "day")
    assert normalize_time("2009-01-20T12:00:00Z") == ("2009-01-20", "day")
    assert normalize_time(2009) == ("2009", "year")
    assert normalize_time("") == (None, None)
    assert normalize_time(None) == (None, None)
    with pytest.raises(ValueError):
        normalize_time("09")
    with pytest.raises(ValueError):
        normalize_time("2009-13")
    with pytest.raises(ValueError):
        normalize_time("2009-02-30")


def test_granularity_helpers():
    assert granularity_of("2009") == "year"
    assert granularity_of("2009-01") == "month"
    assert granularity_of("2009-01-20") == "day"
    assert coarser_granularity("2009-01-20", "2009") == "year"
    assert coarser_granularity("2009-01-20", "2017-01-20") == "day"


def test_valid_iso():
    assert valid_iso("2009-01-20")
    assert not valid_iso("2009-1-20")
    assert not valid_iso(None)
    assert not valid_iso("2009-01-20T00:00:00Z")


def test_time_appears_in_text():
    assert time_appears_in_text("2009-01-20", "took office on 2009-01-20")
    assert time_appears_in_text("2009-01-20", "served from 2009 to 2017")  # year match
    assert time_appears_in_text("2009-01", "during 2009-01 things happened")
    assert not time_appears_in_text("2009", "invoice 120091 was filed")  # digit boundaries
    assert not time_appears_in_text("2009-01-20", "nothing temporal here")


def test_namespace_rel():
    assert namespace_rel("P17") == "P17"
    assert namespace_rel("190") == "CAMEO:190"
    assert namespace_rel("190-03", family="cameo") == "CAMEO:190-03"
    assert namespace_rel("CAMEO:190") == "CAMEO:190"  # idempotent
    assert namespace_rel("ENT:BEFORE") == "ENT:BEFORE"
    assert namespace_rel("located_in") == "located_in"
    with pytest.raises(ValueError):
        namespace_rel("   ")


def test_prefixed():
    assert prefixed("NER", "person") == "[NER] person"
    assert prefixed("REL", "CAMEO:190") == "[REL] CAMEO:190"
    assert prefixed("NER", "[NER] person") == "[NER] person"  # idempotent
    with pytest.raises(KeyError):
        prefixed("BOGUS", "x")
