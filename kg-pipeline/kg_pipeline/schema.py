"""Canonical quad schema — the single contract every adapter emits.

Document is the only shape downstream code (UnifiedDataset converters, the
parquet store, the eval harness) is allowed to consume. from_dict accepts
common key aliases because every source dataset spells things differently;
drift here is normal, silently discarding data is not.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .timeutil import granularity_of, valid_iso

SCHEMA_VERSION = "1"

CHANNELS = {"wiki", "news", "doc", "chat", "email", "issue"}
GRANULARITIES = {"year", "month", "day"}
EXTRACTION_KINDS = {"gold", "distant", "synthetic"}

_REL_RE = re.compile(r"^\S+$")


class SchemaError(ValueError):
    pass


def _first(d: dict, *keys, default=None):
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return default


@dataclass
class TimeBound:
    from_date: Optional[str] = None  # serialized as "from" (keyword-safe alias)
    to: Optional[str] = None
    granularity: str = "day"
    explicit_in_text: bool = False

    def validate(self) -> None:
        if self.granularity not in GRANULARITIES:
            raise SchemaError(f"unknown granularity {self.granularity!r}")
        for name, value in (("from", self.from_date), ("to", self.to)):
            if value is None:
                continue
            if not valid_iso(value):
                raise SchemaError(f"time.{name}={value!r} is not an ISO date (YYYY[-MM[-DD]])")
            if granularity_of(value) != self.granularity:
                raise SchemaError(
                    f"time.{name}={value!r} has {granularity_of(value)} granularity "
                    f"but the bound declares {self.granularity!r}"
                )

    @property
    def is_interval(self) -> bool:
        return self.from_date is not None and self.to is not None

    def to_dict(self) -> dict:
        return {
            "from": self.from_date,
            "to": self.to,
            "granularity": self.granularity,
            "explicit_in_text": self.explicit_in_text,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "TimeBound":
        d = d or {}
        return cls(
            from_date=_first(d, "from", "from_date", "start"),
            to=_first(d, "to", "to_date", "end"),
            granularity=d.get("granularity") or "day",
            explicit_in_text=bool(d.get("explicit_in_text", False)),
        )


@dataclass
class Entity:
    span_start: int
    span_end: int
    surface: str
    ner_label: str = "ENTITY"
    el_id: Optional[str] = None

    def validate(self, text: str) -> None:
        if not (0 <= self.span_start < self.span_end <= len(text)):
            raise SchemaError(f"entity span [{self.span_start},{self.span_end}) out of range")
        if text[self.span_start:self.span_end] != self.surface:
            raise SchemaError(
                f"entity surface {self.surface!r} does not match text at "
                f"[{self.span_start},{self.span_end})"
            )
        if not self.ner_label:
            raise SchemaError("entity ner_label must be non-empty")

    def to_dict(self) -> dict:
        return {
            "span_start": self.span_start,
            "span_end": self.span_end,
            "surface": self.surface,
            "ner_label": self.ner_label,
            "el_id": self.el_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Entity":
        start = _first(d, "span_start", "start", "offset_start")
        end = _first(d, "span_end", "end", "offset_end")
        if start is None or end is None:
            raise SchemaError("entity missing span start/end")
        return cls(
            span_start=int(start),
            span_end=int(end),
            surface=_first(d, "surface", "entity", "name", "mention"),
            ner_label=_first(d, "ner_label", "type", "label", default="ENTITY"),
            el_id=_first(d, "el_id", "kb_id", "qid", "uri", "id"),
        )


@dataclass
class Quad:
    head_span: list  # [start, end) char offsets into Document.text
    rel: str
    tail_span: list
    head_id: Optional[str] = None
    tail_id: Optional[str] = None
    time: Optional[TimeBound] = None

    def validate(self, text: str) -> None:
        for name, span in (("head_span", self.head_span), ("tail_span", self.tail_span)):
            if len(span) != 2 or not (0 <= span[0] < span[1] <= len(text)):
                raise SchemaError(f"quad {name}={span} out of range")
        if self.head_span == self.tail_span:
            raise SchemaError("quad head and tail spans are identical")
        if not self.rel or not _REL_RE.match(self.rel):
            raise SchemaError(f"quad rel {self.rel!r} must be non-empty with no whitespace")
        if "[REL]" in self.rel:
            raise SchemaError(f"quad rel {self.rel!r} must not carry the trainer prefix")
        if self.time is not None:
            self.time.validate()

    def to_dict(self) -> dict:
        return {
            "head_span": list(self.head_span),
            "rel": self.rel,
            "tail_span": list(self.tail_span),
            "head_id": self.head_id,
            "tail_id": self.tail_id,
            "time": self.time.to_dict() if self.time is not None else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Quad":
        head = _first(d, "head_span", "h")
        tail = _first(d, "tail_span", "t")
        if head is None or tail is None:
            raise SchemaError("quad missing head/tail span")
        time = d.get("time")
        return cls(
            head_span=list(head),
            rel=str(_first(d, "rel", "predicate", "r")),
            tail_span=list(tail),
            head_id=_first(d, "head_id", "subject", "s"),
            tail_id=_first(d, "tail_id", "object", "o"),
            time=TimeBound.from_dict(time) if time else None,
        )


@dataclass
class Provenance:
    license: str = "unknown"
    original_id: str = ""
    extraction: str = "distant"

    def validate(self) -> None:
        if self.extraction not in EXTRACTION_KINDS:
            raise SchemaError(f"extraction kind {self.extraction!r} not in {sorted(EXTRACTION_KINDS)}")

    def to_dict(self) -> dict:
        return {"license": self.license, "original_id": self.original_id, "extraction": self.extraction}

    @classmethod
    def from_dict(cls, d: dict) -> "Provenance":
        d = d or {}
        return cls(
            license=d.get("license", "unknown"),
            original_id=str(d.get("original_id", "")),
            extraction=d.get("extraction", "distant"),
        )


@dataclass
class Document:
    doc_id: str
    source: str
    channel: str
    text: str
    doc_time: Optional[str]  # publication/statement date, ISO
    entities: list = field(default_factory=list)
    quads: list = field(default_factory=list)
    provenance: Provenance = field(default_factory=Provenance)

    def validate(self) -> None:
        if not self.doc_id:
            raise SchemaError("doc_id must be non-empty")
        if not self.source:
            raise SchemaError("source must be non-empty")
        if self.channel not in CHANNELS:
            raise SchemaError(f"channel {self.channel!r} not in {sorted(CHANNELS)}")
        if self.doc_time is not None and not valid_iso(self.doc_time):
            raise SchemaError(f"doc_time {self.doc_time!r} is not an ISO date")
        for e in self.entities:
            e.validate(self.text)
        for q in self.quads:
            q.validate(self.text)
        self.provenance.validate()

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "source": self.source,
            "channel": self.channel,
            "doc_time": self.doc_time,
            "text": self.text,
            "entities": [e.to_dict() for e in self.entities],
            "quads": [q.to_dict() for q in self.quads],
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Document":
        time = d.get("doc_time")
        return cls(
            doc_id=str(_first(d, "doc_id", "docid", "id")),
            source=d.get("source", ""),
            channel=d.get("channel", ""),
            text=_first(d, "text", "content"),
            doc_time=time,
            entities=[Entity.from_dict(e) for e in d.get("entities", [])],
            quads=[Quad.from_dict(q) for q in d.get("quads", [])],
            provenance=Provenance.from_dict(d.get("provenance")),
        )
