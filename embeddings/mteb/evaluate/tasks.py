"""Per-task evaluation functions for the 7 GovReport MTEB datasets.

Each function:

    1. Reads its dataset directory under ``--datasets-dir`` (default
       ``mteb/datasets``).
    2. Calls the appropriate ``validate_*_dir`` from ``scripts.dataset_io`` to
       fail fast on malformed data.
    3. Encodes the needed texts via the supplied :class:`Encoder`.
    4. Computes the metric(s) via :mod:`evaluate.metrics`.
    5. Returns a list of :class:`TaskResult` (one or two per dataset).

Every task is wrapped so a missing dataset dir is skipped with a warning
(returns an empty list) instead of crashing the whole run.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import numpy as np

# tasks.py lives under mteb/evaluate/. ``scripts`` is a sibling package under
# mteb/, so we require mteb/ on sys.path (the __main__ entrypoint ensures
# this; for direct imports, callers must arrange it themselves).
import sys
_MTEB_DIR = Path(__file__).resolve().parent.parent
if str(_MTEB_DIR) not in sys.path:
    sys.path.insert(0, str(_MTEB_DIR))

from scripts.common import DATASETS_DIR as _DEFAULT_DATASETS_DIR  # noqa: E402
from scripts.dataset_io import (  # noqa: E402
    read_jsonl,
    validate_clustering_dir,
    validate_cross_report_dir,
    validate_mteb_dir,
    validate_pair_classification_dir,
    validate_reranking_dir,
    validate_sts_dir,
)

from .encoders import Encoder
from .metrics import (
    accuracy_at_threshold,
    cosine_matrix,
    map_at_k,
    ndcg_at_k,
    recall_at_k,
    roc_auc,
    spearman,
    v_measure_cluster,
)

logger = logging.getLogger(__name__)


# ----- Result dataclass -----------------------------------------------------


@dataclass
class TaskResult:
    task: str
    metric: str
    score: float
    n_examples: int
    runtime_seconds: float

    def to_dict(self) -> dict:
        return asdict(self)


# ----- Task names + dataset dir layout --------------------------------------

# Task short name → dataset subdirectory under datasets/.
TASK_DIRS: dict[str, str] = {
    "retrieval": "govreport_retrieval",
    "sts": "govreport_sts",
    "summary_sts": "govreport_summary_sts",
    "clustering": "govreport_clustering",
    "reranking": "govreport_reranking",
    "cross_report": "govreport_cross_report",
    "pair_classification": "govreport_pair_classification",
}

ALL_TASKS: tuple[str, ...] = (
    "retrieval",
    "sts",
    "summary_sts",
    "clustering",
    "reranking",
    "cross_report",
    "pair_classification",
)


# ----- Helpers --------------------------------------------------------------


def _read_qrels(qrels_path: Path) -> dict[str, dict[str, float]]:
    """Parse qrels TSV (with header) into ``{qid: {cid: score}}``."""
    qrels: dict[str, dict[str, float]] = {}
    with qrels_path.open("r", encoding="utf-8") as f:
        f.readline()  # header
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            qid, cid, score = parts
            qrels.setdefault(qid, {})[cid] = float(score)
    return qrels


def _safe_eval(
    task_name: str,
    fn: Callable[[], list[TaskResult]],
) -> list[TaskResult]:
    """Run fn(); on missing dataset, log a warning and return []."""
    try:
        return fn()
    except FileNotFoundError as e:
        logger.warning("Skipping %s — dataset missing: %s", task_name, e.filename)
        return []
    except ValueError as e:
        # Validator failed (malformed dataset).
        logger.warning("Skipping %s — validation failed: %s", task_name, e)
        return []


# ----- Retrieval-style: standard + cross-report -----------------------------


def _eval_retrieval_like(
    task_name: str,
    dataset_dir: Path,
    encoder: Encoder,
    *,
    k_ndcg: int = 10,
    k_map: int = 10,
    k_recall: int = 5,
) -> list[TaskResult]:
    """Shared retrieval path for ``retrieval`` and ``cross_report`` tasks."""
    validate_fn = (
        validate_cross_report_dir if task_name == "cross_report" else validate_mteb_dir
    )
    stats = validate_fn(dataset_dir)

    corpus_rows = list(read_jsonl(dataset_dir / "corpus.jsonl"))
    query_rows = list(read_jsonl(dataset_dir / "queries.jsonl"))
    qrels = _read_qrels(dataset_dir / "qrels" / "test.tsv")

    corpus_id_by_idx = [r["_id"] for r in corpus_rows]
    corpus_idx_by_id = {cid: i for i, cid in enumerate(corpus_id_by_idx)}

    # Encode corpus + queries.
    t0 = time.perf_counter()
    corpus_emb = encoder.encode([r["text"] for r in corpus_rows], kind="document")
    query_emb = encoder.encode([r["text"] for r in query_rows], kind="query")
    encode_secs = time.perf_counter() - t0

    # Score every query against every corpus row.
    sim = cosine_matrix(query_emb, corpus_emb)  # (Nq, Nc)

    ndcg_scores: list[float] = []
    map_inputs: list[list[float]] = []
    recall_inputs: list[list[float]] = []

    for qi, q in enumerate(query_rows):
        qid = q["_id"]
        gold = qrels.get(qid, {})
        if not gold:
            continue
        row = sim[qi]
        order = np.argsort(-row)  # descending
        # Ranked gold relevance per candidate (gold score if relevant else 0).
        ranked_rel = [gold.get(corpus_id_by_idx[i], 0.0) for i in order]
        ndcg_scores.append(ndcg_at_k(ranked_rel, k=k_ndcg))
        map_inputs.append(ranked_rel)
        recall_inputs.append(ranked_rel)

    n_examples = len(query_rows)
    total_secs = encode_secs

    results = [
        TaskResult(
            task=f"govreport_{task_name}",
            metric=f"ndcg@{k_ndcg}",
            score=float(np.mean(ndcg_scores)) if ndcg_scores else 0.0,
            n_examples=n_examples,
            runtime_seconds=total_secs,
        ),
        TaskResult(
            task=f"govreport_{task_name}",
            metric=f"map@{k_map}",
            score=map_at_k(map_inputs, k=k_map) if map_inputs else 0.0,
            n_examples=n_examples,
            runtime_seconds=total_secs,
        ),
    ]
    if task_name == "cross_report":
        results.append(
            TaskResult(
                task=f"govreport_{task_name}",
                metric=f"recall@{k_recall}",
                score=recall_at_k(recall_inputs, k=k_recall) if recall_inputs else 0.0,
                n_examples=n_examples,
                runtime_seconds=total_secs,
            )
        )
    _ = corpus_idx_by_id, stats  # silence unused warnings
    return results


def eval_retrieval(datasets_dir: Path, encoder: Encoder) -> list[TaskResult]:
    dd = datasets_dir / TASK_DIRS["retrieval"]

    def _run() -> list[TaskResult]:
        return _eval_retrieval_like("retrieval", dd, encoder)

    return _safe_eval("retrieval", _run)


def eval_cross_report(datasets_dir: Path, encoder: Encoder) -> list[TaskResult]:
    dd = datasets_dir / TASK_DIRS["cross_report"]

    def _run() -> list[TaskResult]:
        return _eval_retrieval_like("cross_report", dd, encoder)

    return _safe_eval("cross_report", _run)


# ----- STS / Summary STS ----------------------------------------------------


def _eval_sts_like(task_name: str, dataset_dir: Path, encoder: Encoder) -> list[TaskResult]:
    validate_sts_dir(dataset_dir)
    rows = list(read_jsonl(dataset_dir / "test.jsonl"))
    s1 = [r["sent1"] for r in rows]
    s2 = [r["sent2"] for r in rows]
    gold = [float(r["score"]) for r in rows]

    t0 = time.perf_counter()
    emb1 = encoder.encode(s1, kind="text")
    emb2 = encoder.encode(s2, kind="text")
    encode_secs = time.perf_counter() - t0

    # Cosine similarity per pair.
    # Normalize rows then dot product row-wise.
    n1 = emb1 / np.clip(np.linalg.norm(emb1, axis=1, keepdims=True), 1e-12, None)
    n2 = emb2 / np.clip(np.linalg.norm(emb2, axis=1, keepdims=True), 1e-12, None)
    pred = np.sum(n1 * n2, axis=1)

    return [
        TaskResult(
            task=f"govreport_{task_name}",
            metric="spearman",
            score=spearman(pred.tolist(), gold),
            n_examples=len(rows),
            runtime_seconds=encode_secs,
        )
    ]


def eval_sts(datasets_dir: Path, encoder: Encoder) -> list[TaskResult]:
    dd = datasets_dir / TASK_DIRS["sts"]

    def _run() -> list[TaskResult]:
        return _eval_sts_like("sts", dd, encoder)

    return _safe_eval("sts", _run)


def eval_summary_sts(datasets_dir: Path, encoder: Encoder) -> list[TaskResult]:
    dd = datasets_dir / TASK_DIRS["summary_sts"]

    def _run() -> list[TaskResult]:
        return _eval_sts_like("summary_sts", dd, encoder)

    return _safe_eval("summary_sts", _run)


# ----- Clustering -----------------------------------------------------------


def eval_clustering(datasets_dir: Path, encoder: Encoder) -> list[TaskResult]:
    dd = datasets_dir / TASK_DIRS["clustering"]

    def _run() -> list[TaskResult]:
        stats = validate_clustering_dir(dd)
        rows = list(read_jsonl(dd / "test.jsonl"))
        texts = [r["text"] for r in rows]
        labels = [r["label"] for r in rows]

        # Fixed k=15 matches the 15-topic vocab used during dataset gen.
        n_unique = len(set(labels))
        k = max(2, min(15, n_unique))

        t0 = time.perf_counter()
        emb = encoder.encode(texts, kind="text")
        encode_secs = time.perf_counter() - t0

        vm = v_measure_cluster(emb, labels, n_clusters=k)
        _ = stats
        return [
            TaskResult(
                task="govreport_clustering",
                metric="v_measure",
                score=vm,
                n_examples=len(rows),
                runtime_seconds=encode_secs,
            )
        ]

    return _safe_eval("clustering", _run)


# ----- Reranking ------------------------------------------------------------


def eval_reranking(datasets_dir: Path, encoder: Encoder) -> list[TaskResult]:
    dd = datasets_dir / TASK_DIRS["reranking"]

    def _run() -> list[TaskResult]:
        validate_reranking_dir(dd)
        rows = list(read_jsonl(dd / "test.jsonl"))

        # Flatten all candidates across queries for one batched encode pass,
        # then split back. We encode queries separately.
        t0 = time.perf_counter()
        queries = [r["query"] for r in rows]
        # Candidate list per query = positives + negatives.
        cand_lists = [r["positive"] + r["negative"] for r in rows]
        flat_cands: list[str] = []
        sizes: list[int] = []
        for cl in cand_lists:
            flat_cands.extend(cl)
            sizes.append(len(cl))

        q_emb = encoder.encode(queries, kind="query")
        c_emb = encoder.encode(flat_cands, kind="document")
        encode_secs = time.perf_counter() - t0

        # For each query, cosine-sim vs its candidates, then build a ranked
        # gold-relevance list. Gold = 1 for positives, 0 for negatives.
        offsets = np.concatenate([[0], np.cumsum(sizes)])
        map_inputs: list[list[float]] = []
        for qi, cl in enumerate(cand_lists):
            start, end = int(offsets[qi]), int(offsets[qi + 1])
            cvecs = c_emb[start:end]
            qvec = q_emb[qi : qi + 1]
            sim = cosine_matrix(qvec, cvecs)[0]
            order = np.argsort(-sim)
            # Gold relevance in ranked order: 1.0 for positives, 0 for negatives.
            n_pos = len(rows[qi]["positive"])
            gold = [1.0] * n_pos + [0.0] * (len(cl) - n_pos)
            ranked_rel = [gold[i] for i in order]
            map_inputs.append(ranked_rel)

        k = 10
        return [
            TaskResult(
                task="govreport_reranking",
                metric=f"map@{k}",
                score=map_at_k(map_inputs, k=k) if map_inputs else 0.0,
                n_examples=len(rows),
                runtime_seconds=encode_secs,
            )
        ]

    return _safe_eval("reranking", _run)


# ----- Pair classification --------------------------------------------------


def eval_pair_classification(datasets_dir: Path, encoder: Encoder) -> list[TaskResult]:
    dd = datasets_dir / TASK_DIRS["pair_classification"]

    def _run() -> list[TaskResult]:
        validate_pair_classification_dir(dd)
        rows = list(read_jsonl(dd / "test.jsonl"))
        s1 = [r["sent1"] for r in rows]
        s2 = [r["sent2"] for r in rows]
        # ``labels`` is a 1-element list per MTEB convention.
        y = [int(r["labels"][0]) for r in rows]

        t0 = time.perf_counter()
        emb1 = encoder.encode(s1, kind="text")
        emb2 = encoder.encode(s2, kind="text")
        encode_secs = time.perf_counter() - t0

        n1 = emb1 / np.clip(np.linalg.norm(emb1, axis=1, keepdims=True), 1e-12, None)
        n2 = emb2 / np.clip(np.linalg.norm(emb2, axis=1, keepdims=True), 1e-12, None)
        sims = np.sum(n1 * n2, axis=1)

        return [
            TaskResult(
                task="govreport_pair_classification",
                metric="roc_auc",
                score=roc_auc(y, sims.tolist()),
                n_examples=len(rows),
                runtime_seconds=encode_secs,
            ),
            TaskResult(
                task="govreport_pair_classification",
                metric="accuracy@0.5",
                score=accuracy_at_threshold(y, sims.tolist(), threshold=0.5),
                n_examples=len(rows),
                runtime_seconds=encode_secs,
            ),
        ]

    return _safe_eval("pair_classification", _run)


# ----- Dispatch table -------------------------------------------------------


TASK_FUNCS: dict[str, Callable[[Path, Encoder], list[TaskResult]]] = {
    "retrieval": eval_retrieval,
    "sts": eval_sts,
    "summary_sts": eval_summary_sts,
    "clustering": eval_clustering,
    "reranking": eval_reranking,
    "cross_report": eval_cross_report,
    "pair_classification": eval_pair_classification,
}
