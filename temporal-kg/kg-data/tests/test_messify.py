"""Messify: protected spans survive byte-identical, offsets adjust."""

import random

from kg_data.messify import messify


def test_protected_spans_untouched():
    text = "The billing-api service runs on PostgreSQL and it is fine"
    spans = [(4, 15), (32, 42)]  # billing-api, PostgreSQL
    rng = random.Random(1)
    for _ in range(20):
        result = messify(text, spans, rng, channel="slack", intensity=0.5)
        for (s0, _), (s1, e1) in zip(spans, result.spans):
            assert result.text[s1:e1] == text[s0:s0 + (e1 - s1)]
        # protected substrings still present verbatim
        assert "billing-api" in result.text
        assert "PostgreSQL" in result.text


def test_offsets_valid_after_corruption():
    text = "auth-gateway is owned by Identity. notify-worker deploys on AWS."
    spans = [(0, 12), (27, 35), (55, 58)]
    result = messify(text, spans, random.Random(3), channel="slack",
                     intensity=0.9)
    for (s, e) in result.spans:
        assert 0 <= s < e <= len(result.text)
        snippet = result.text[s:e]
        assert snippet in text  # protected content unchanged


def test_zero_intensity_is_identity():
    text = "billing-api runs on MySQL"
    result = messify(text, [(15, 20)], random.Random(9), channel="slack",
                     intensity=0.0)
    assert result.text == text
    assert result.spans == [(15, 20)]
