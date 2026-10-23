"""Stage 6 — few-shot LLM baseline extractor.

Closes the build → extract → score loop: a channel-aware, few-shot prompted
extractor over the OpenRouter model, producing quads in the same neutral
schema as gold (mentions located verbatim; unmatched strings dropped and
counted). Also answers eval questions from gold documents as a QA baseline.
This is deliberately naive — it exists to validate the harness and set a
floor, not to be good.
"""

from __future__ import annotations

import asyncio
from datetime import date

from pydantic import BaseModel, Field

from . import llm
from .config import settings
from .schema import Doc, EvalQuestion, Mention, Quad

FEWSHOT = """Example input (channel: slack, date: 2024-05-02):
maya [10:02 AM]: heads up, billing-api officially runs on DynamoDB now, migration finally done
diego [10:04 AM]: took long enough. prod?
maya [10:05 AM]: yep prod since may 1st
Example output:
{"quads": [{"head": "billing-api", "relation": "runs on database", "tail": "DynamoDB", "valid_from": "2024-05-01", "valid_to": null}]}
Example input (channel: confluence, date: 2023-02-10):
# Auth service architecture
The auth-gateway is owned by the Identity team. It is deployed on GCP.
Example output:
{"quads": [{"head": "auth-gateway", "relation": "owned by", "tail": "Identity", "valid_from": null, "valid_to": null}, {"head": "auth-gateway", "relation": "deployed on", "tail": "GCP", "valid_from": null, "valid_to": null}]}"""


class ExtractedQuad(BaseModel):
    head: str
    relation: str
    tail: str
    valid_from: str | None = None
    valid_to: str | None = None


class ExtractionResult(BaseModel):
    quads: list[ExtractedQuad] = Field(default_factory=list)


SYSTEM = """You extract temporal knowledge-graph quads from enterprise artifacts.
For every stated relationship, output {"head", "relation", "tail", "valid_from",
"valid_to"}: head/tail are VERBATIM substrings of the input text; relation is a
short lowercase verb phrase (e.g. "runs on database", "led by", "owned by");
valid_from / valid_to are ISO dates ONLY if the text or its date makes them
derivable (YYYY-MM-DD or YYYY), else null. Extract only what the text actually
asserts. Output JSON only."""


def _find(text: str, needle: str) -> tuple[int, int] | None:
    idx = text.find(needle)
    if idx == -1:
        idx = text.lower().find(needle.lower())
    return None if idx == -1 else (idx, idx + len(needle))


def _parse_iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        try:
            return date.fromisoformat(value[:4] + "-01-01")
        except ValueError:
            return None


async def extract_doc(world: World | None, doc: Doc, *, use_llm: bool = True,
                      semaphore: asyncio.Semaphore | None = None) -> list[Quad]:
    """Extract quads from the doc's latest revision via the baseline LLM."""
    rev = doc.latest_revision
    user = (
        f"Channel: {doc.channel} ({doc.doc_type}). Document date: "
        f"{rev.sim_time.isoformat()}.\n\n{FEWSHOT}\n\n---\nInput "
        f"(channel: {doc.channel}, date: {rev.sim_time.isoformat()}):\n"
        f"{rev.text}\n\nOutput JSON:"
    )

    async def run() -> ExtractionResult:
        if semaphore:
            async with semaphore:
                result = await llm.structured(
                    ExtractionResult, system=SYSTEM, user=user,
                    temperature=0.0, max_tokens=900,
                )
        else:
            result = await llm.structured(
                ExtractionResult, system=SYSTEM, user=user,
                temperature=0.0, max_tokens=900,
            )
        return result

    result = await run()
    quads: list[Quad] = []
    for eq in result.quads:
        h_span = _find(rev.text, eq.head)
        t_span = _find(rev.text, eq.tail)
        if h_span is None or t_span is None:
            continue  # hallucinated / paraphrased mention — dropped
        quads.append(Quad(
            id="", world_id=doc.world_id, doc_id=doc.id, rev_id=rev.rev_id,
            head=Mention(entity_id=eq.head, text=rev.text[h_span[0]:h_span[1]],
                         start=h_span[0], end=h_span[1]),
            tail=Mention(entity_id=eq.tail, text=rev.text[t_span[0]:t_span[1]],
                         start=t_span[0], end=t_span[1]),
            relation=eq.relation,
            valid_from=_parse_iso(eq.valid_from),
            valid_to=_parse_iso(eq.valid_to),
            time_source="explicit_in_text" if eq.valid_from else "none",
            claim_status="accurate",
        ))
    for i, q in enumerate(quads):
        q.id = f"{doc.id}-pred{i:03d}"
    return quads


class Answer(BaseModel):
    answer: str
    reasoning: str = ""


ANSWER_SYSTEM = (
    "You answer temporal questions about a company from internal documents. "
    "Answer concisely (a name, a year, or yes/no + one clause). Use only the "
    "documents and the question's as-of date."
)


async def answer_question(question: EvalQuestion, docs: list[Doc], *,
                          semaphore: asyncio.Semaphore | None = None) -> str:
    """QA baseline: answer from the question's gold documents."""
    by_id = {d.id: d for d in docs}
    texts = []
    for doc_id in question.gold_doc_ids:
        d = by_id.get(doc_id)
        if d is not None:
            rev = d.latest_revision
            texts.append(f"[{d.channel} {rev.sim_time.isoformat()}] {rev.text}")
    user = (
        f"As-of date: {question.as_of.isoformat()}\n\nQuestion: "
        f"{question.question}\n\nDocuments:\n" +
        "\n\n".join(texts[:4]) + "\n\nAnswer:"
    )

    async def run() -> Answer:
        if semaphore:
            async with semaphore:
                return await llm.structured(
                    Answer, system=ANSWER_SYSTEM, user=user,
                    temperature=0.0, max_tokens=300,
                )
        return await llm.structured(
            Answer, system=ANSWER_SYSTEM, user=user,
            temperature=0.0, max_tokens=300,
        )

    return (await run()).answer
