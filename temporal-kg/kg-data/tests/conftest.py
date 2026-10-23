"""Offline fixtures: a FakeLLM standing in for kg_data.llm.structured.

No network, no API key. Tests that exercise LLM paths get canned pydantic
responses; generation tests use use_llm=False (deterministic renderers).
"""

from __future__ import annotations

import pytest

from kg_data import llm
from kg_data.docgen import GeneratedBody
from kg_data.extract import Answer, ExtractionResult, ExtractedQuad
from kg_data.worldgen import WorldNaming


def fake_naming() -> WorldNaming:
    return WorldNaming(
        company="Northbeam Labs",
        services=["billing-api", "auth-gateway", "search-svc", "notify-worker",
                  "ledger-api", "insights-etl"],
        teams=["Payments", "Platform", "Identity", "Growth"],
        people=["Maya Chen", "Diego Alvarez", "Priya Nair", "Tom Okafor",
                "Sara Lindqvist", "Jun Park", "Amara Diallo"],
        databases=["PostgreSQL", "MySQL", "DynamoDB"],
        clouds=["AWS", "GCP"],
        cities=["Austin", "Toronto"],
        vendors=["Datadog", "Snowflake"],
        lore="A fictional B2B SaaS company.",
    )


class FakeStructured:
    """Records calls; returns canned responses per model class."""

    def __init__(self):
        self.calls: list[str] = []

    async def __call__(self, model_cls, system: str, user: str, **kw):
        self.calls.append(model_cls.__name__)
        name = model_cls.__name__
        if name == "WorldNaming":
            return fake_naming()
        if name == "GeneratedBody":
            return GeneratedBody(
                title="Stub doc", body="Stub body with enough length " * 3)
        if name == "ExtractionResult":
            return ExtractionResult(quads=[
                ExtractedQuad(head="billing-api", relation="runs on database",
                              tail="PostgreSQL", valid_from="2023-01-01",
                              valid_to=None),
                ExtractedQuad(head="nonexistent paraphrase", relation="x",
                              tail="y"),  # must be dropped (span check)
            ])
        if name == "Answer":
            return Answer(answer="PostgreSQL")
        raise AssertionError(f"unexpected structured model: {name}")


@pytest.fixture
def fake_llm(monkeypatch):
    fake = FakeStructured()
    monkeypatch.setattr(llm, "structured", fake)
    return fake


@pytest.fixture
def offline_world():
    from kg_data.worldgen import build_scaffold, instantiate_world
    import random

    scaffold = build_scaffold(random.Random(1234))
    return instantiate_world("w-test", 1234, scaffold, fake_naming())
