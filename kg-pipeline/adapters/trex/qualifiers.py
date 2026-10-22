"""Wikidata P580/P582 qualifier lookup for T-REx triples.

The cache is a JSON file keyed "subject|predicate|object" with
{"from": ..., "to": ...} values. Build it from the Wikidata SPARQL endpoint
with fetch_qualifiers() (network, rate-limited) or a bulk dump job, then
commit/distribute the cache so normalization is fully offline and
reproducible.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kg_pipeline.httpguard import guarded_urlopen  # noqa: E402

TripleKey = Tuple[str, str, str]

WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
USER_AGENT = "kg-pipeline/0.1 (research; contact: repo owner)"


class QualifierLookup:
    def __init__(self, mapping: Dict = None):
        self.mapping = {}
        for k, v in (mapping or {}).items():
            key = tuple(k.split("|")) if isinstance(k, str) else tuple(k)
            self.mapping[key] = v

    def get(self, key: TripleKey) -> Optional[dict]:
        return self.mapping.get(tuple(key))

    @classmethod
    def from_json(cls, path) -> "QualifierLookup":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(raw)


def build_sparql(subject: str, predicate: str, obj: str) -> str:
    """SPARQL for the P580/P582 qualifiers of one statement."""
    return f"""SELECT ?from ?to WHERE {{
  wd:{subject} p:{predicate} ?stmt .
  ?stmt ps:{predicate} wd:{obj} .
  OPTIONAL {{ ?stmt pq:P580 ?from . }}
  OPTIONAL {{ ?stmt pq:P582 ?to . }}
}}"""


def fetch_qualifiers(
    triples: Iterable[TripleKey],
    endpoint: str = WIKIDATA_SPARQL,
    delay: float = 1.0,
) -> Dict[str, dict]:
    """Fetch qualifiers for triples from the Wikidata SPARQL endpoint.

    Network-only; be polite (one query per triple, delay between requests).
    Returns the cache dict ready to write as JSON. The endpoint must be a
    public http/https host (guarded_urlopen enforces this).
    """
    cache: Dict[str, dict] = {}
    for (s, p, o) in triples:
        query = build_sparql(s, p, o)
        params = urllib.parse.urlencode({"query": query, "format": "json"})
        req = urllib.request.Request(
            f"{endpoint}?{params}",
            headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"},
        )
        with guarded_urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        bindings = data.get("results", {}).get("bindings", [])
        if bindings:
            b = bindings[0]
            entry = {}
            if "from" in b:
                entry["from"] = b["from"]["value"][:10]
            if "to" in b:
                entry["to"] = b["to"]["value"][:10]
            if entry:
                cache["|".join((s, p, o))] = entry
        time.sleep(delay)
    return cache
