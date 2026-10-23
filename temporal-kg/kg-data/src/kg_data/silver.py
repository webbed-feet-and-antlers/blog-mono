"""Stage 5b — silver labels over REAL text.

The complement of the synthetic corpus: synthetic gold teaches what to
believe (true temporal bounds, supersession, conflicts); this stage teaches
how real text talks. Raw rows already on disk from stage 0 (Ubuntu IRC,
Enron, Stack Exchange) are wrapped as docs and quad-labeled by LLM
extraction with self-consistency voting, then adjudicated with the audit
stage's assertion rules. There is no gold timeline behind these labels —
they are SILVER: LLM opinion filtered by mechanical checks. Training bulk
for the generic phase, never eval material.

Time discipline: Enron rows carry real send timestamps → transaction time
is real and time_source="doc_date" when the text states no explicit date;
Ubuntu/SO rows have no dates → a fixed corpus-window date, time_source
"none" unless the text itself yields a date. Valid bounds only appear when
explicitly derivable from the text.

Relations are slugified ("runs on database" → runs_on_database): the
kg-pipeline bridge drops whitespace relations as untrainable templates, and
snake_case keeps the silver family distinct from Wikidata P-ids and
COMPANY: predicates.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections import Counter
from datetime import date
from pathlib import Path

from . import audit, extract as extract_mod, grounding, llm
from .adapters.trex import write_public
from .config import DATA_DIR, settings
from .schema import Author, Doc, Quad, Revision
from .audit import AuditResult, AUDIT_SYSTEM, _same_sentence

logger = logging.getLogger(__name__)

SILVER_SOURCES: dict[str, dict] = {
    "enron": {
        "channel": "email", "doc_type": "email",
        "author": "enron corpus", "has_dates": True,
        "fallback_date": date(2001, 8, 1), "max_chars": 4000,
    },
    "stackexchange": {
        "channel": "confluence", "doc_type": "stack overflow post",
        "author": "stack exchange corpus", "has_dates": False,
        "fallback_date": date(2023, 3, 1), "max_chars": 6000,
    },
    "ubuntu": {
        "channel": "slack", "doc_type": "irc dialogue",
        "author": "ubuntu irc corpus", "has_dates": False,
        "fallback_date": date(2018, 4, 1), "max_chars": 3000,
    },
}

MIN_CHARS = 80  # below this a real text is chit-chat with no assertable facts


def slug_relation(rel: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", rel.strip().lower()).strip("_")
    return s or "related_to"


def _row_date(row: dict) -> date | None:
    raw = row.get("date")
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _clean(text: str, max_chars: int) -> str:
    text = text.replace("__eou__", "\n").replace("__eot__", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > max_chars:
        cut = text[:max_chars]
        text = cut[: cut.rfind("\n") if "\n" in cut else len(cut)].rstrip()
    return text


def load_rows(source: str, *, grounding_dir: Path | None = None) -> list[dict]:
    path = (grounding_dir or grounding.GROUNDING_DIR) / "raw" / f"{source}.jsonl"
    if not path.exists():
        raise SystemExit(
            f"no raw rows for {source} at {path} — run "
            f"`python -m kg_data grounding` first")
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_docs(source: str, rows: list[dict], *, cap: int = 200) -> list[Doc]:
    """Wrap real texts as revision-wrapped docs (the kg-pipeline bridge
    requires revisions[]; flat docs silently yield nothing downstream)."""
    spec = SILVER_SOURCES[source]
    fields = grounding.CORPORA[source]["fields"]
    docs: list[Doc] = []
    seen_texts: set[str] = set()
    for row in rows:
        if len(docs) >= cap:
            break
        shaped = grounding._extract_texts([row], fields)
        text = _clean(shaped[0], spec["max_chars"]) if shaped else ""
        if len(text) < MIN_CHARS:
            continue
        key = text[:200].lower()
        if key in seen_texts:  # Enron has near-identical bodies en masse
            continue
        seen_texts.add(key)
        sim_time = _row_date(row) if spec["has_dates"] else None
        sim_time = sim_time or spec["fallback_date"]
        doc_id = f"sil-{source}-{len(docs):04d}"
        sender = row.get("from") if isinstance(row.get("from"), str) else ""
        docs.append(Doc(
            id=doc_id, world_id=f"silver_{source}", channel=spec["channel"],
            doc_type=spec["doc_type"],
            title=str(row.get("subject") or "").strip(),
            author=Author(name=sender or spec["author"],
                          role="real corpus" if sender else ""),
            created_at=sim_time,
            revisions=[Revision(rev_id=f"{doc_id}#r0", sim_time=sim_time,
                                text=text)],
        ))
    return docs


def _vote_key(q: Quad) -> tuple[str, str, str]:
    # extract_doc anchors surfaces verbatim, so the same mention found by two
    # samples yields identical text; only relation phrasing varies
    return (q.head.text.lower(), slug_relation(q.relation), q.tail.text.lower())


async def label_doc(doc: Doc, *, votes: int = 3,
                    semaphore: asyncio.Semaphore | None = None) -> list[Quad]:
    """Self-consistency extraction: k samples at temperature, keep quads a
    strict majority of successful samples agrees on (dates merged by vote)."""
    samples: list[list[Quad]] = []
    for _ in range(votes):
        try:
            samples.append(await extract_mod.extract_doc(
                None, doc, semaphore=semaphore, temperature=0.5))
        except Exception as exc:  # per-sample isolation: one bad call ≠ no doc
            logger.warning("silver sample failed for %s: %s",
                           doc.id, str(exc)[:120])
            samples.append([])
    n_ok = sum(1 for s in samples if s)
    # strict majority of the successful samples, floored at 2 — except when
    # only one sample survived (throttle reality), where it stands alone
    need = min(max(2, math.ceil((n_ok + 1) / 2)), max(n_ok, 1)) \
        if votes > 1 else 1

    by_key: dict[tuple[str, str, str], list[Quad]] = {}
    for s in samples:
        for q in s:
            by_key.setdefault(_vote_key(q), []).append(q)

    kept: list[Quad] = []
    for instances in by_key.values():
        if len(instances) < need:
            continue
        rep = instances[0].model_copy()
        rep.relation = slug_relation(rep.relation)
        # dates: majority among agreeing samples (None counts as a vote)
        vf = Counter(q.valid_from for q in instances)
        vt = Counter(q.valid_to for q in instances)
        rep.valid_from = vf.most_common(1)[0][0]
        rep.valid_to = vt.most_common(1)[0][0]
        spec = SILVER_SOURCES.get(doc.world_id.removeprefix("silver_"), {})
        rep.time_source = ("explicit_in_text" if rep.valid_from
                           else "doc_date" if spec.get("has_dates")
                           else "none")
        kept.append(rep)
    return kept


async def adjudicate_doc(doc: Doc, quads: list[Quad], *,
                         semaphore: asyncio.Semaphore | None = None
                         ) -> tuple[list[Quad], dict]:
    """Audit-stage assertion rules applied to the extractor's own quads:
    asserted → keep (hedged flag), denied + same-sentence co-occurrence →
    keep (auditor drop precision is too low to trust), denied with no
    co-occurrence → drop, omitted → keep (fail-safe). Failure → keep all."""
    stats = {"dropped": 0, "denied_comention": 0, "hedged": 0,
             "audit_failures": 0}
    if not quads:
        return [], stats
    rev = doc.latest_revision
    lines = [f"- f{i}: {q.head.text} {q.relation.replace('_', ' ')} "
             f"{q.tail.text}" for i, q in enumerate(quads)]
    user = (f"Document date: {rev.sim_time.isoformat()} · channel: "
            f"{doc.channel}\n\nText:\n{rev.text}\n\nCandidate facts:\n"
            + "\n".join(lines))
    try:
        if semaphore:
            async with semaphore:
                result = await llm.structured(
                    AuditResult, system=AUDIT_SYSTEM, user=user,
                    temperature=0.0, max_tokens=900)
        else:
            result = await llm.structured(
                AuditResult, system=AUDIT_SYSTEM, user=user,
                temperature=0.0, max_tokens=900)
    except Exception as exc:
        logger.warning("silver adjudication failed for %s: %s",
                       doc.id, str(exc)[:120])
        stats["audit_failures"] = 1
        return quads, stats

    valid = {f"f{i}" for i in range(len(quads))}
    asserted: dict[str, bool] = {}
    for sel in result.assertions:
        if sel.fact_id in valid:
            asserted[sel.fact_id] = asserted.get(sel.fact_id, False) \
                or sel.hedged
    denied = {sel.fact_id for sel in result.not_asserted
              if sel.fact_id in valid and sel.fact_id not in asserted}

    kept: list[Quad] = []
    for i, q in enumerate(quads):
        fid = f"f{i}"
        if fid in asserted:
            if asserted[fid]:
                q.hedged = True
                stats["hedged"] += 1
            kept.append(q)
        elif fid in denied:
            if _same_sentence(rev.text, [q.head.text], [q.tail.text]):
                stats["denied_comention"] += 1
                kept.append(q)
            else:
                stats["dropped"] += 1
        else:
            kept.append(q)  # uncertain → fail safe
    return kept, stats


async def label_corpus(source: str, docs: list[Doc], *, votes: int = 3,
                       use_llm: bool = True) -> tuple[list[Quad], dict]:
    sem = asyncio.Semaphore(settings.concurrency)
    stats: dict = {"docs": len(docs), "docs_with_quads": 0,
                   "voted_quads": 0}

    async def one(doc: Doc) -> list[Quad]:
        voted = await label_doc(doc, votes=votes, semaphore=sem)
        stats["voted_quads"] += len(voted)
        if voted:
            stats["docs_with_quads"] += 1
        if not use_llm:
            return voted
        kept, astats = await adjudicate_doc(doc, voted, semaphore=sem)
        for k, v in astats.items():
            stats[k] = stats.get(k, 0) + v
        return kept

    results = await asyncio.gather(*(one(d) for d in docs),
                                   return_exceptions=True)
    quads: list[Quad] = []
    for doc, outcome in zip(docs, results):
        if isinstance(outcome, BaseException):
            logger.warning("silver doc %s failed: %s",
                           doc.id, str(outcome)[:120])
            stats["doc_failures"] = stats.get("doc_failures", 0) + 1
            continue
        quads.extend(outcome)
    for i, q in enumerate(quads):
        q.id = f"silq-{source}-{i:05d}"
    return quads, stats


def export_silver(source: str, docs: list[Doc], quads: list[Quad], *,
                  holdout: int = 25) -> dict:
    """Train/val/test via the trex splitter; the last `holdout` docs (in
    corpus order) go to a holdout/ dir no consumer reads — the human-audited
    real-text benchmark slice."""
    train_docs = docs[:-holdout] if holdout else docs
    train_ids = {d.id for d in train_docs}
    out = write_public(train_docs,
                       [q for q in quads if q.doc_id in train_ids],
                       name=f"silver_{source}") or {}
    if holdout and len(docs) > holdout:
        h_docs = docs[-holdout:]
        h_dir = DATA_DIR / "public" / f"silver_{source}" / "holdout"
        h_dir.mkdir(parents=True, exist_ok=True)
        h_ids = {d.id for d in h_docs}
        h_quads = [q for q in quads if q.doc_id in h_ids]
        with (h_dir / "docs.jsonl").open("w") as f:
            for d in h_docs:
                f.write(d.model_dump_json() + "\n")
        with (h_dir / "quads.jsonl").open("w") as f:
            for q in h_quads:
                f.write(q.model_dump_json() + "\n")
        out["holdout/docs"] = len(h_docs)
        out["holdout/quads"] = len(h_quads)
    return out


async def run_silver(source: str, *, docs_cap: int = 200, votes: int = 3,
                     use_llm: bool = True, holdout: int = 25) -> dict:
    rows = load_rows(source)
    docs = build_docs(source, rows, cap=docs_cap + holdout)
    if len(docs) <= holdout:
        raise SystemExit(f"only {len(docs)} usable docs for {source} — "
                         f"holdout of {holdout} leaves nothing to train on")
    quads, stats = await label_corpus(source, docs, votes=votes,
                                      use_llm=use_llm)
    counts = export_silver(source, docs, quads, holdout=holdout)
    stats["export"] = counts
    return stats
