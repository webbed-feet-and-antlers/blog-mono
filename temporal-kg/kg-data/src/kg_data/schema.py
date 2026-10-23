"""Neutral corpus schema — the pipeline's stable interchange format.

Everything is bi-temporal by construction: *valid time* (when the fact was
true in the world) comes from the ground-truth timeline; *transaction time*
(when the system saw the claim) is the sim_time of the doc revision carrying
it. Quads always record what a document CLAIMS; `claim_status` separates
accurate claims from deliberately injected conflicting ones.
"""

from __future__ import annotations

from datetime import date
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, Field

Volatility = Literal["immutable", "durable", "medium", "ephemeral"]
Channel = Literal["slack", "confluence", "drive", "email", "meeting_notes"]
TimeSource = Literal["explicit_in_text", "doc_date", "kb_qualifier", "none"]
ClaimStatus = Literal["accurate", "conflict_injected"]
RevisionKind = Literal["initial", "fact_changing", "cosmetic"]
QuestionKind = Literal[
    "point_in_time",
    "current_state",
    "history",
    "staleness_superseded",
    "staleness_still_true",
    "conflict",
]
Split = Literal["train", "val", "test"]


# --- Ground-truth world -----------------------------------------------------


class Entity(BaseModel):
    id: str
    name: str
    type: str  # company | service | team | person | tool | vendor | location
    aliases: list[str] = Field(default_factory=list)


class Predicate(BaseModel):
    id: str
    label: str  # human phrasing: "runs on database"
    volatility: Volatility
    functional: bool = True  # single occupant at a time (CEO) vs additive (depends on)
    head_type: str
    tail_type: str


class Segment(BaseModel):
    """One period where (head, predicate) had a specific tail/value."""

    head_id: str
    tail_id: str
    valid_from: date
    valid_to: date | None = None  # None = open-ended / current

    @property
    def is_open(self) -> bool:
        return self.valid_to is None


class Timeline(BaseModel):
    predicate_id: str
    segments: list[Segment] = Field(default_factory=list)


class World(BaseModel):
    id: str
    seed: int
    lore: str = ""
    start: date
    end: date
    entities: list[Entity]
    predicates: list[Predicate]
    timelines: list[Timeline]
    split: Split = "train"

    # --- lookups / timeline queries (pure code, used by evalgen + tests) ---

    def entity(self, entity_id: str) -> Entity:
        return next(e for e in self.entities if e.id == entity_id)

    def entity_by_name(self, name: str) -> Entity | None:
        low = name.strip().lower()
        for e in self.entities:
            if e.name.lower() == low or low in {a.lower() for a in e.aliases}:
                return e
        return None

    def predicate(self, predicate_id: str) -> Predicate:
        return next(p for p in self.predicates if p.id == predicate_id)

    def segments_for(self, predicate_id: str, head_id: str) -> list[Segment]:
        for tl in self.timelines:
            if tl.predicate_id == predicate_id:
                return [s for s in tl.segments if s.head_id == head_id]
        return []

    def segment_at(self, predicate_id: str, head_id: str, at: date) -> Segment | None:
        for seg in self.segments_for(predicate_id, head_id):
            if seg.valid_from <= at and (seg.valid_to is None or at < seg.valid_to):
                return seg
        return None


# --- Documents ---------------------------------------------------------------


class Author(BaseModel):
    name: str
    role: str = ""
    offboarded_on: date | None = None


class Revision(BaseModel):
    rev_id: str
    sim_time: date
    text: str
    content_sha256: str = ""
    revision_kind: RevisionKind = "initial"

    def model_post_init(self, _: object) -> None:
        if not self.content_sha256:
            self.content_sha256 = sha256(self.text.encode()).hexdigest()


class Doc(BaseModel):
    id: str
    world_id: str
    channel: Channel
    doc_type: str
    title: str = ""
    author: Author
    created_at: date
    deleted_at: date | None = None
    revisions: list[Revision] = Field(default_factory=list)

    @property
    def latest_revision(self) -> Revision:
        return self.revisions[-1]


# --- Gold quads --------------------------------------------------------------


class Mention(BaseModel):
    entity_id: str
    text: str  # verbatim substring of the doc text
    start: int  # char offset, inclusive
    end: int  # char offset, exclusive


class Quad(BaseModel):
    """A (head, relation, tail) claim with temporal bounds, as stated in one revision."""

    id: str
    world_id: str
    head: Mention
    relation: str  # predicate id (synthetic) or Wikidata P-id (public)
    tail: Mention
    valid_from: date | None = None
    valid_to: date | None = None
    time_source: TimeSource = "doc_date"
    claim_status: ClaimStatus = "accurate"
    doc_id: str
    rev_id: str
    # audit-stage flags: asserted by the render but not planned by the
    # coverage matrix (discovered), or asserted with uncertainty (hedged)
    discovered: bool = False
    hedged: bool = False


# --- Eval questions ----------------------------------------------------------


class EvalQuestion(BaseModel):
    id: str
    world_id: str
    kind: QuestionKind
    question: str
    as_of: date
    answer: str  # display answer
    answer_entity_id: str | None = None
    gold_doc_ids: list[str] = Field(default_factory=list)
    notes: str = ""
    split: Split = "train"


# --- Ingestion log -----------------------------------------------------------


class IngestionEvent(BaseModel):
    event: Literal["created", "revised", "deleted"]
    doc_id: str
    rev_id: str | None = None  # None only for deleted
    world_id: str
    sim_time: date
    content_sha256: str = ""


def build_ingestion_log(docs: list[Doc]) -> list[IngestionEvent]:
    """CDC stream: one event per doc creation / revision / deletion, sim-time ordered."""
    events: list[IngestionEvent] = []
    for doc in docs:
        for i, rev in enumerate(doc.revisions):
            events.append(IngestionEvent(
                event="created" if i == 0 else "revised",
                doc_id=doc.id,
                rev_id=rev.rev_id,
                world_id=doc.world_id,
                sim_time=rev.sim_time,
                content_sha256=rev.content_sha256,
            ))
        if doc.deleted_at is not None:
            events.append(IngestionEvent(
                event="deleted",
                doc_id=doc.id,
                rev_id=None,
                world_id=doc.world_id,
                sim_time=doc.deleted_at,
            ))
    events.sort(key=lambda e: (e.sim_time, e.doc_id))
    return events
