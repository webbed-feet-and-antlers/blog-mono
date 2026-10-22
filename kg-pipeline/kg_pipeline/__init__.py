"""kg-pipeline: data pipeline for temporal knowledge-graph extraction."""
from .labels import namespace_rel, prefixed
from .schema import Document, Entity, Provenance, Quad, TimeBound

__all__ = [
    "Document",
    "Entity",
    "Provenance",
    "Quad",
    "TimeBound",
    "namespace_rel",
    "prefixed",
]
