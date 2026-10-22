"""Shared fixture builders. Spans are always computed with str.find so the
fixtures can never drift out of sync with the text."""
from __future__ import annotations


def _span(text: str, surface: str):
    i = text.find(surface)
    assert i >= 0, f"{surface!r} not in {text!r}"
    return i, i + len(surface)


def trex_records():
    t1 = "Berlin is the capital of Germany."
    b1, e1 = _span(t1, "Berlin")
    b2, e2 = _span(t1, "Germany")
    r1 = {
        "docid": "Berlin",
        "text": t1,
        "entities": [["Berlin", b1, e1, "Q64"], ["Germany", b2, e2, "Q183"]],
        "triples": [{"subject": "Q64", "predicate": "P17", "object": "Q183"}],
    }

    t2 = "Kolkata is the capital of India."
    k1, k2 = _span(t2, "Kolkata")
    i1, i2 = _span(t2, "India")
    r2 = {
        "docid": "Kolkata",
        "text": t2,
        "entities": [
            {"mention": "Kolkata", "start": k1, "end": k2, "uri": "Q1348"},
            {"mention": "India", "start": i1, "end": i2, "uri": "Q1834"},
        ],
        "triples": [["Q1348", "P17", "Q1834"]],
    }

    t3 = "Paris is the capital of France."
    p1, p2 = _span(t3, "Paris")
    r3 = {
        "docid": "Dangling",
        "text": t3,
        "entities": [["Paris", p1, p2, "Q90"]],
        # Q142 (France) has no aligned span -> triple must be skipped + counted
        "triples": [{"subject": "Q90", "predicate": "P17", "object": "Q142"}],
    }
    return [r1, r2, r3]


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
