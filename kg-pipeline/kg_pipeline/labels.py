"""Label namespaces shared by every adapter.

Relation labels live in families that survive into the trainer's label space
verbatim (the trainer prepends its own task prefix):

- ``P108``           Wikidata properties (T-REx, DocRED, REBEL)
- ``CAMEO:190``      ICEWS event codes
- ``ENT:BEFORE``     MAVEN-ERE style event relations (later adapter)
- ``COMPANY:OWNS_SERVICE``  phase-2 enterprise relations
- ``located_in``     legacy DocRED-style string relations pass through

NER types stay fine-grained end to end; the notebook's MISC-squashing type
map is a trainer-side choice, not a pipeline one. DATE/TIME types are the
temporal anchors and must never be collapsed.
"""
from __future__ import annotations

import re

NER_PREFIX = "[NER] "
REL_PREFIX = "[REL] "
LINK_PREFIX = "[LINK] "

_P_RE = re.compile(r"^P\d+$")
_CAMEO_RE = re.compile(r"^\d{2,4}(?:-\d{2,4})?$")
_KNOWN_FAMILY = ("CAMEO:", "ENT:", "COMPANY:")


def namespace_rel(rel: str, family: str | None = None) -> str:
    """Return the namespaced relation label, idempotently.

    family="cameo" forces the CAMEO namespace (ICEWS event codes); otherwise
    bare 2-4 digit codes are also treated as CAMEO.
    """
    rel = str(rel).strip()
    if not rel:
        raise ValueError("empty relation label")
    if rel.startswith(_KNOWN_FAMILY) or _P_RE.match(rel):
        return rel
    if family == "cameo" or (family is None and _CAMEO_RE.match(rel)):
        return f"CAMEO:{rel}"
    return rel


def prefixed(task: str, label: str) -> str:
    """Prepend the trainer's task prefix exactly once."""
    prefix = {"NER": NER_PREFIX, "REL": REL_PREFIX, "LINK": LINK_PREFIX}[task]
    return label if label.startswith(prefix) else f"{prefix}{label}"
