"""GovReport loader + JSONL/TSV writers + MTEB-format validator.

The loader mirrors ``embeddings/cloud/seed.py:22-48``: take a proportional
slice of each split so the dev subset reflects the full distribution.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

logger = logging.getLogger(__name__)


# ----- GovReport loading -----------------------------------------------------

def load_govreport_subset(
    *,
    subset: int | None,
    split: str | None = None,
) -> list[dict[str, str]]:
    """Load GovReport from HuggingFace and return ``[{report_id, text, split}, ...]``.

    Args:
        subset: Total row cap; taken proportionally across splits. ``None`` = full dataset.
        split: If set, restrict to this split (e.g. ``"train"``).

    The ``report_id`` format is ``{split}_{row_id}`` — same as ``cloud/seed.py``.
    """
    # Import lazily so module-import-time errors don't break unrelated stages.
    from datasets import load_dataset

    ds = load_dataset("ccdv/govreport-summarization")
    splits = [s for s in ("train", "validation", "test") if s in ds]
    if split is not None:
        splits = [s for s in splits if s == split]

    out: list[dict[str, str]] = []
    for split_name in splits:
        split_ds = ds[split_name]
        if subset is not None:
            total_available = sum(len(ds[s]) for s in ds)
            if total_available == 0:
                continue
            split_count = max(1, round(len(split_ds) * subset / total_available))
            split_ds = split_ds.select(range(min(split_count, len(split_ds))))
        for row in split_ds:
            out.append(
                {
                    "report_id": f"{split_name}_{row['id']}",
                    "split": split_name,
                    "text": row["report"],
                }
            )
    return out


# ----- Generic JSONL writer --------------------------------------------------

def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    """Write ``rows`` to *path* as JSON-lines, one ``json.dumps`` per line.

    The file is opened in text mode and flushed per row so a mid-run crash
    leaves the file readable up to the last successful row. Returns row count.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")
            f.flush()
            n += 1
    return n


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Iterate JSON-lines from *path*. Skips blank lines.

    Tolerant of a trailing corrupt line (e.g. a partial write from a crash):
    a line that fails to parse as JSON is skipped with a warning.
    """
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as e:
                logger.warning("skipping corrupt line %d in %s: %s", lineno, path, e)


def read_jsonl_id_set(path: Path, key: str) -> set[str]:
    """Return the set of ``row[key]`` values across all rows in *path*.

    Returns an empty set if the file does not exist. Tolerant of partial
    trailing writes (corrupt lines are skipped). Used for resume: stage 1
    collects done report_ids, stage 2 collects done chunk_ids.
    """
    if not path.is_file():
        return set()
    out: set[str] = set()
    for row in read_jsonl(path):
        v = row.get(key)
        if v is not None:
            out.add(v)
    return out


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    """Append a single row, flushing immediately (used by failure logs)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False))
        f.write("\n")
        f.flush()


# ----- MTEB output writers ---------------------------------------------------

def write_qrels_tsv(path: Path, qrels: Iterable[tuple[str, str, float]]) -> int:
    """Write MTEB-format qrels. Header: ``query-id\\tcorpus-id\\tscore``.

    Args:
        qrels: iterable of (query_id, corpus_id, score). Score is typically 1.0.
    Returns row count (excluding header).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        f.write("query-id\tcorpus-id\tscore\n")
        f.flush()
        for qid, cid, score in qrels:
            f.write(f"{qid}\t{cid}\t{score}\n")
            f.flush()
            n += 1
    return n


# ----- MTEB format validation -----------------------------------------------

@dataclass
class DatasetStats:
    corpus_size: int
    queries_size: int
    qrels_size: int

    def __str__(self) -> str:
        return (
            f"DatasetStats(corpus={self.corpus_size}, "
            f"queries={self.queries_size}, qrels={self.qrels_size})"
        )


def validate_mteb_dir(dataset_dir: Path) -> DatasetStats:
    """Validate an MTEB-format directory under *dataset_dir*.

    Expected layout::

        corpus.jsonl   → {"_id": "...", "title": "...", "text": "..."}
        queries.jsonl  → {"_id": "...", "text": "..."}
        qrels/test.tsv → query-id\\tcorpus-id\\tscore  (with header)

    Raises ``ValueError`` on any structural problem.
    """
    dataset_dir = Path(dataset_dir)
    corpus_p = dataset_dir / "corpus.jsonl"
    queries_p = dataset_dir / "queries.jsonl"
    qrels_p = dataset_dir / "qrels" / "test.tsv"

    for p in (corpus_p, queries_p, qrels_p):
        if not p.is_file():
            raise ValueError(f"Missing required file: {p}")
        if p.stat().st_size == 0:
            raise ValueError(f"Empty file: {p}")

    # Collect corpus ids (no duplicates).
    corpus_ids: set[str] = set()
    corpus_count = 0
    for row in read_jsonl(corpus_p):
        cid = row.get("_id")
        if not cid:
            raise ValueError(f"corpus row missing _id: {row}")
        if cid in corpus_ids:
            raise ValueError(f"Duplicate corpus _id: {cid}")
        corpus_ids.add(cid)
        corpus_count += 1
    if corpus_count == 0:
        raise ValueError("corpus.jsonl has no rows")

    # Collect query ids (no duplicates).
    query_ids: set[str] = set()
    query_count = 0
    for row in read_jsonl(queries_p):
        qid = row.get("_id")
        if not qid:
            raise ValueError(f"queries row missing _id: {row}")
        if qid in query_ids:
            raise ValueError(f"Duplicate query _id: {qid}")
        query_ids.add(qid)
        query_count += 1
    if query_count == 0:
        raise ValueError("queries.jsonl has no rows")

    # Validate qrels: header + each row's ids must exist; ≥1 per query.
    with qrels_p.open("r", encoding="utf-8") as f:
        header = f.readline().rstrip("\n")
        expected_header = "query-id\tcorpus-id\tscore"
        if header != expected_header:
            raise ValueError(
                f"qrels header mismatch: got {header!r}, expected {expected_header!r}"
            )
        qrels_count = 0
        qrels_per_query: dict[str, int] = {}
        seen_pairs: set[tuple[str, str]] = set()
        for lineno, line in enumerate(f, start=2):
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) != 3:
                raise ValueError(
                    f"qrels/{qrels_p.name}:{lineno} expected 3 fields, got {len(parts)}"
                )
            qid, cid, score = parts
            if qid not in query_ids:
                raise ValueError(f"qrels references unknown query id: {qid}")
            if cid not in corpus_ids:
                raise ValueError(f"qrels references unknown corpus id: {cid}")
            try:
                float(score)
            except ValueError:
                raise ValueError(
                    f"qrels/{qrels_p.name}:{lineno} score is not numeric: {score!r}"
                )
            if (qid, cid) in seen_pairs:
                raise ValueError(f"Duplicate qrel pair: {qid} -> {cid}")
            seen_pairs.add((qid, cid))
            qrels_per_query[qid] = qrels_per_query.get(qid, 0) + 1
            qrels_count += 1

    if qrels_count == 0:
        raise ValueError("qrels/test.tsv has no rows (after header)")

    missing = query_ids - set(qrels_per_query.keys())
    if missing:
        raise ValueError(
            f"{len(missing)} query/queries have no qrels (first few: "
            f"{sorted(missing)[:5]})"
        )

    return DatasetStats(
        corpus_size=corpus_count,
        queries_size=query_count,
        qrels_size=qrels_count,
    )


# ----- Title disambiguation --------------------------------------------------

def disambiguate_titles(chunks: list[dict[str, Any]]) -> None:
    """Append `` (#2)``, ``(#3)`` ... to duplicate titles within *chunks* in place.

    Operates on a list of dicts each containing a ``"title`` field. The first
    occurrence keeps its title; subsequent duplicates get suffixed.
    """
    seen: dict[str, int] = {}
    for c in chunks:
        base = c.get("title") or ""
        n = seen.get(base, 0) + 1
        seen[base] = n
        if n > 1:
            c["title"] = f"{base} (#{n})"


# ----- Long-report sectioning (paragraph split, inlined) ---------------------

_PARA_SPLIT_RE = re.compile(r"\n\s*\n")


def split_paragraphs(text: str) -> list[str]:
    """Inline paragraph splitter (matches modules/chunking.py:14-16)."""
    return [p.strip() for p in _PARA_SPLIT_RE.split(text.strip()) if p.strip()]
