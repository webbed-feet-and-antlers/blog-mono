"""T-REx adapter parser over fixture rows (both known shapes)."""

from datetime import date

from kg_data.adapters.trex import _iter_trex_triples, split_for


def test_flat_shape():
    row = {"text": "Barack Obama was the president of the United States.",
           "head": "Barack Obama", "relation": "P39", "tail": "United States"}
    triples = _iter_trex_triples(row)
    assert triples == [("Barack Obama", "P39", "United States")]


def test_raw_trex_shape():
    row = {
        "text": "Aristotle studied at Plato's Academy in Athens.",
        "entities": [
            {"surfaceform": "Aristotle", "uris": ["Q868"]},
            {"surfaceform": "Athens", "uris": ["Q1524"]},
        ],
        "relations": [{"surfaceform": "educated at", "uris": ["P69"]}],
        "pairs": [{"subject": 0, "predicate": 0, "object": 1}],
    }
    triples = _iter_trex_triples(row)
    assert triples == [("Aristotle", "P69", "Athens")]


def test_pairs_list_shape():
    row = {
        "text": "X did Y at Z.",
        "entities": [{"surfaceform": "X", "uris": ["Q1"]},
                     {"surfaceform": "Z", "uris": ["Q2"]}],
        "relations": [{"surfaceform": "did", "uris": ["P1"]}],
        "pairs": [[0, 0, 1]],
    }
    assert _iter_trex_triples(row) == [("X", "P1", "Z")]


def test_unparseable_row_returns_empty():
    assert _iter_trex_triples({"foo": "bar"}) == []


def test_split_stable():
    assert split_for("a") == split_for("a")
    assert split_for("trex-1") in ("train", "val", "test")


def test_wd_date_parsing():
    from kg_data.adapters.trex import _wd_date

    assert _wd_date("2009-01-20T00:00:00Z") == date(2009, 1, 20)
    assert _wd_date(None) is None
    assert _wd_date("not-a-date") is None
