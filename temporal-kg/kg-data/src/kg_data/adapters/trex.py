"""Stage 5 — public real-text quads.

Two sources, both verified live at planning time:

  1. relbert/t_rex (HF) — real Wikipedia abstracts aligned with Wikidata
     triples. Real text, real relation P-ids, no temporal bounds
     (time_source="none").
  2. Wikidata SPARQL enrichment — real (head, P-id, tail, P580/P582) quads
     for functional temporal predicates, aligned to Wikipedia summary
     sentences by co-occurrence (distant supervision; time_source=
     "kb_qualifier"). Raw responses cached under data/raw/.

HF community mirrors drift schemas, so the T-REx parser introspects the
first rows and tries several shapes; if none match it fails loudly with the
observed fields rather than emitting garbage.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date
from pathlib import Path
from urllib.parse import quote

import httpx

from ..config import DATA_DIR
from ..schema import Author, Doc, Mention, Quad, Revision, Split

logger = logging.getLogger(__name__)

PUBLIC_DIR = DATA_DIR / "public" / "trex"
RAW_DIR = DATA_DIR / "raw"

# Immutable pin of relbert/t_rex's auto-converted parquet. `refs/convert/parquet`
# is a moving branch (HF re-converts on upstream changes), which would silently
# change what the manifest hashes describe. This is that branch's targetCommit
# as of 2026-10-23; bump deliberately and re-run `adapt` when you want new data.
TREX_REPO = "relbert/t_rex"
TREX_PARQUET_REVISION = "5baa776c0e65c1cc49d4fde8298d5b81478b549f"

# functional Wikidata properties with P580/P582 qualifiers worth enriching
TEMPORAL_PROPERTIES = ["P39", "P108", "P54", "P463", "P127"]

WDQS_URL = "https://query.wikidata.org/sparql"
WIKI_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
# WMF UA policy: tool name, contact URL, mailto
UA = {"User-Agent": "kg-data/0.1 (https://github.com/nathanp99/blog-mono "
      "temporal-KG research corpus; mailto:nathan@nathanp.dev)"}

SPLIT_FRACS: list[tuple[Split, float]] = \
    [("train", 0.8), ("val", 0.1), ("test", 0.1)]


def split_for(doc_id: str) -> Split:
    """Stable article-level split by doc id hash."""
    h = int(hashlib.sha256(doc_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    acc = 0.0
    for name, frac in SPLIT_FRACS:
        acc += frac
        if h <= acc:
            return name
    return "test"


def _find(text: str, needle: str) -> tuple[int, int] | None:
    idx = text.find(needle)
    if idx == -1:
        idx = text.lower().find(needle.lower())
    if idx == -1:
        return None
    return idx, idx + len(needle)


# --- T-REx -------------------------------------------------------------------


def _iter_trex_triples(row: dict) -> list[tuple[str, str, str]]:
    """Yield (head_surface, relation_uri, tail_surface) from one T-REx row.

    Handles both the raw T-REx shape (text + entities + relations + pairs)
    and flat pre-resolved shapes (head/relation/tail string fields).
    """
    text = row.get("text") or row.get("passage") or ""
    if not text:
        return []

    # flat shape
    if all(k in row for k in ("head", "relation", "tail")) and \
            isinstance(row["head"], str):
        return [(row["head"], str(row["relation"]), row["tail"])]

    # raw T-REx shape
    entities = row.get("entities") or []
    relations = row.get("relations") or []
    pairs = row.get("pairs") or row.get("triples") or []
    out: list[tuple[str, str, str]] = []

    def surface(ent: dict) -> str:
        s = ent.get("surfaceform") or ent.get("surface_form") or ""
        return s if isinstance(s, str) else ""

    def uri(ent: dict) -> str:
        uris = ent.get("uris") or []
        if isinstance(uris, list) and uris:
            u = uris[0]
            return u if isinstance(u, str) else ""
        return ""

    for pair in pairs:
        try:
            if isinstance(pair, dict):
                h = entities[int(pair.get("subject", pair.get("head", 0)))]
                t = entities[int(pair.get("object", pair.get("tail", 1)))]
                r = relations[int(pair.get("predicate", pair.get("relation", 0)))]
            else:
                h = entities[int(pair[0])]
                r = relations[int(pair[1])]
                t = entities[int(pair[2])]
        except (IndexError, KeyError, TypeError, ValueError):
            continue
        head_s, rel, tail_s = surface(h), uri(r), surface(t)
        if head_s and rel and tail_s:
            out.append((head_s, rel, tail_s))
    return out


def _make_doc(doc_id: str, text: str, world: str, doc_type: str,
              sim_time: date, title: str = "") -> Doc:
    return Doc(
        id=doc_id, world_id=world, channel="confluence", doc_type=doc_type,
        title=title, author=Author(name="wikipedia", role="public corpus"),
        created_at=sim_time,
        revisions=[Revision(rev_id=f"{doc_id}#r0", sim_time=sim_time,
                            text=text)],
    )


def adapt_trex(cap: int = 3000) -> tuple[list[Doc], list[Quad]]:
    """Stream relbert/t_rex at the pinned parquet commit (the script loader
    is unsupported by datasets>=3), group rows by Wikipedia article into
    docs, and emit quads with verbatim span alignment.

    Verified row shape: {relation: P-id, head: str, tail: str, title: str,
    text: str} — one triple per row, abstract repeated per triple.
    """
    from datasets import load_dataset

    ds = load_dataset(TREX_REPO, split="train", streaming=True,
                      revision=TREX_PARQUET_REVISION)

    by_title: dict[str, dict] = {}
    n_rows = 0
    for row in ds:
        n_rows += 1
        if len(by_title) >= cap:
            break
        row = dict(row)
        title = str(row.get("title") or "").strip()
        text = str(row.get("text") or "").strip()
        head, rel, tail = (str(row.get("head") or ""),
                           str(row.get("relation") or ""),
                           str(row.get("tail") or ""))
        if not (title and text and head and rel and tail):
            continue
        entry = by_title.setdefault(title, {"text": text, "triples": []})
        entry["triples"].append((head, rel, tail))
        if len(by_title) > cap:
            break

    if not by_title:
        raise RuntimeError("relbert/t_rex (parquet) yielded no usable rows; "
                           "the mirror's schema changed again")

    docs: list[Doc] = []
    quads: list[Quad] = []
    for title, entry in by_title.items():
        text = entry["text"]
        doc_id = "trex-" + re.sub(r"[^A-Za-z0-9_.-]", "_", title)[:60]
        doc_quads = []
        for head_s, rel, tail_s in entry["triples"]:
            h_span = _find(text, head_s)
            t_span = _find(text, tail_s)
            if h_span is None or t_span is None:
                continue
            doc_quads.append(Quad(
                id="", world_id="trex", doc_id=doc_id,
                rev_id=f"{doc_id}#r0",
                head=Mention(entity_id=head_s,
                             text=text[h_span[0]:h_span[1]],
                             start=h_span[0], end=h_span[1]),
                tail=Mention(entity_id=tail_s,
                             text=text[t_span[0]:t_span[1]],
                             start=t_span[0], end=t_span[1]),
                relation=rel, valid_from=None, valid_to=None,
                time_source="none",
            ))
        if not doc_quads:
            continue
        docs.append(_make_doc(doc_id, text, "trex", "wikipedia abstract",
                              date(2020, 1, 1), title=title))
        quads.extend(doc_quads)

    for i, q in enumerate(quads):
        q.id = f"trex-q{i:05d}"
    logger.info("trex@%s: %d docs, %d quads (from %d rows)",
                TREX_PARQUET_REVISION[:8], len(docs), len(quads), n_rows)
    return docs, quads


# --- Wikidata temporal enrichment --------------------------------------------


def _sparql_for(pid: str, limit: int) -> str:
    return f"""
SELECT ?s ?sLabel ?o ?oLabel ?start ?end WHERE {{
  ?s p:{pid} ?st .
  ?st ps:{pid} ?o .
  ?st pq:P580 ?start .
  OPTIONAL {{ ?st pq:P582 ?end }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
LIMIT {limit}
"""


def _wd_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _wiki_summary(client: httpx.Client, title: str,
                  cache_dir: Path) -> dict | None:
    cache = cache_dir / f"{quote(title, safe='')}.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text())
        except json.JSONDecodeError:
            pass
    try:
        resp = client.get(
            WIKI_SUMMARY_URL.format(title=quote(title.replace(" ", "_"),
                                                 safe="")),
            headers=UA, timeout=20, follow_redirects=True)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        cache.write_text(json.dumps(data, ensure_ascii=False))
        return data
    except httpx.HTTPError as exc:
        logger.debug("summary fetch failed for %s: %s", title, exc)
        return None


def _sentence_with(summary_text: str, *needles: str) -> tuple[int, int] | None:
    """Char span of the first sentence containing all needles (caseless)."""
    low = summary_text.lower()
    lowered = [n.lower() for n in needles if n]
    for match in re.finditer(r"[^.!?]+[.!?]?", summary_text):
        seg = match.span()
        chunk = low[seg[0]:seg[1]]
        if all(n in chunk for n in lowered):
            return seg
    del low
    return None


def enrich_wikidata(cap: int = 300, per_property: int = 800) \
        -> tuple[list[Doc], list[Quad]]:
    """Distant supervision: temporal WD quads aligned to Wikipedia summaries."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    cache_dir = RAW_DIR / "wikipedia"
    cache_dir.mkdir(exist_ok=True)
    docs: list[Doc] = []
    quads: list[Quad] = []
    seen_docs: dict[str, Doc] = {}

    with httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
        for pid in TEMPORAL_PROPERTIES:
            try:
                resp = client.post(
                    WDQS_URL,
                    data={"query": _sparql_for(pid, per_property),
                          "format": "json"},
                    headers=UA)
                resp.raise_for_status()
                payload = resp.json()
            except httpx.HTTPError as exc:
                logger.warning("wdqs %s failed: %s", pid, exc)
                continue
            (RAW_DIR / f"wdqs_{pid}.json").write_text(json.dumps(payload))

            for binding in payload["results"]["bindings"]:
                if len(quads) >= cap:
                    break
                s_label = binding["sLabel"]["value"]
                o_label = binding["oLabel"]["value"]
                if s_label.startswith("Q") or o_label.startswith("Q"):
                    continue  # label service failed for this row
                start = _wd_date(binding.get("start", {}).get("value"))
                end = _wd_date(binding.get("end", {}).get("value"))
                if start is None:
                    continue
                summary = _wiki_summary(client, s_label, cache_dir)
                if summary is None:
                    continue
                text = summary.get("extract") or ""
                if len(text) < 80:
                    continue
                sent = _sentence_with(text, s_label, o_label)
                if sent is None:
                    continue
                doc_id = "wd-" + re.sub(r"[^A-Za-z0-9_.-]", "_", s_label)[:60]
                h_span = _find(text[sent[0]:sent[1]], s_label)
                t_span = _find(text[sent[0]:sent[1]], o_label)
                if h_span is None or t_span is None:
                    continue
                h_span = (sent[0] + h_span[0], sent[0] + h_span[1])
                t_span = (sent[0] + t_span[0], sent[0] + t_span[1])
                if doc_id not in seen_docs:
                    doc = _make_doc(doc_id, text, "wikidata",
                                    "wikipedia summary", date(2024, 1, 1))
                    seen_docs[doc_id] = doc
                    docs.append(doc)
                quads.append(Quad(
                    id="", world_id="wikidata", doc_id=doc_id,
                    rev_id=f"{doc_id}#r0",
                    head=Mention(entity_id=s_label,
                                 text=text[h_span[0]:h_span[1]],
                                 start=h_span[0], end=h_span[1]),
                    tail=Mention(entity_id=o_label,
                                 text=text[t_span[0]:t_span[1]],
                                 start=t_span[0], end=t_span[1]),
                    relation=pid, valid_from=start, valid_to=end,
                    time_source="kb_qualifier",
                ))
            if len(quads) >= cap:
                break

    for i, q in enumerate(quads):
        q.id = f"wd-q{i:05d}"
    logger.info("wikidata enrichment: %d docs, %d quads", len(docs), len(quads))
    return docs, quads


def write_public(docs: list[Doc], quads: list[Quad],
                  name: str = "trex") -> dict[str, int]:
    """Write split JSONLs for one public source under its own subdirectory
    (an empty pass must never clobber another source's files)."""
    if not docs and not quads:
        return {}
    out_dir = DATA_DIR / "public" / name
    counts: dict[str, int] = {}
    for split in ("train", "val", "test"):
        sdocs = [d for d in docs if split_for(d.id) == split]
        squads = [q for q in quads if split_for(q.doc_id) == split]
        docs_path = out_dir / split / "docs.jsonl"
        quads_path = out_dir / split / "quads.jsonl"
        docs_path.parent.mkdir(parents=True, exist_ok=True)
        with docs_path.open("w") as f:
            for d in sdocs:
                f.write(d.model_dump_json() + "\n")
        with quads_path.open("w") as f:
            for q in squads:
                f.write(q.model_dump_json() + "\n")
        counts[f"{split}/docs"] = len(sdocs)
        counts[f"{split}/quads"] = len(squads)
    return counts
