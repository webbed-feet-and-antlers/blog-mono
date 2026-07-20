"""Metric helpers used by the embedding evaluator.

All functions are pure (no I/O) and operate on numpy arrays / python lists so
they can be smoke-tested in isolation without touching the dataset layer.

Coverage:
    - ``cosine_matrix`` — pairwise cosine similarity between two embedding sets.
    - ``spearman`` — Spearman rank correlation (scipy).
    - ``ndcg_at_k`` — normalized DCG @ k for one ranking.
    - ``map_at_k`` — mean average precision @ k across queries.
    - ``v_measure`` — V-measure (sklearn) after KMeans clustering.
    - ``roc_auc`` — ROC-AUC (sklearn).
    - ``accuracy_at_threshold`` — fraction correct under a score threshold.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np


# ----- Similarity -----------------------------------------------------------


def cosine_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Return ``(N, M)`` cosine-similarity matrix between rows of *a* and *b*.

    Rows with zero norm get a zero-similarity column/row (defensive; should not
    happen with real embeddings but avoids NaN propagation).
    """
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    if a.ndim != 2 or b.ndim != 2:
        raise ValueError(f"cosine_matrix expects 2D arrays, got {a.shape} and {b.shape}")
    if a.shape[1] != b.shape[1]:
        raise ValueError(f"dim mismatch: a={a.shape[1]} b={b.shape[1]}")

    a_norm = a / np.clip(np.linalg.norm(a, axis=1, keepdims=True), 1e-12, None)
    b_norm = b / np.clip(np.linalg.norm(b, axis=1, keepdims=True), 1e-12, None)
    return a_norm @ b_norm.T


# ----- Correlation ----------------------------------------------------------


def spearman(pred: Sequence[float], gold: Sequence[float]) -> float:
    """Spearman rank correlation between predicted and gold scores."""
    from scipy.stats import spearmanr  # lazy import: scipy is an extra dep

    pred_arr = np.asarray(pred, dtype=np.float64)
    gold_arr = np.asarray(gold, dtype=np.float64)
    if pred_arr.shape != gold_arr.shape or pred_arr.ndim != 1:
        raise ValueError("spearman expects equal-length 1D sequences")
    if len(pred_arr) < 2:
        raise ValueError("spearman needs ≥ 2 samples")
    rho, _p = spearmanr(pred_arr, gold_arr)
    rho_f = float(rho)
    if np.isnan(rho_f):
        # Constant input — correlation is undefined; report 0.
        return 0.0
    return rho_f


# ----- Ranking metrics ------------------------------------------------------


def ndcg_at_k(ranked_relevances: Sequence[float], k: int = 10) -> float:
    """NDCG@k for a single list of relevance scores, ranked by the model.

    *ranked_relevances* is the gold relevance of each candidate **in the order
    the model ranked them** (most similar first). Relevance can be binary or
    graded.
    """
    if k <= 0:
        raise ValueError("k must be positive")
    rel = np.asarray(ranked_relevances, dtype=np.float64)
    if rel.size == 0:
        return 0.0
    top = rel[:k]

    # DCG: sum_i rel_i / log2(i + 2)  (i is 0-indexed; +2 because rank 1 -> log2(2))
    discounts = 1.0 / np.log2(np.arange(top.size, dtype=np.float64) + 2.0)
    dcg = float(np.sum(top * discounts))

    # Ideal DCG: sort all relevances descending, take top-k.
    ideal = np.sort(rel)[::-1][:k]
    ideal_discounts = 1.0 / np.log2(np.arange(ideal.size, dtype=np.float64) + 2.0)
    idcg = float(np.sum(ideal * ideal_discounts))

    if idcg <= 0.0:
        return 0.0
    return dcg / idcg


def average_precision_at_k(ranked_relevances: Sequence[float], k: int = 10) -> float:
    """Average precision @ k for one ranked list."""
    if k <= 0:
        raise ValueError("k must be positive")
    rel = np.asarray(ranked_relevances, dtype=np.float64)
    if rel.size == 0:
        return 0.0
    top = rel[:k]
    n_rel = float(np.sum(rel > 0.0))
    if n_rel == 0.0:
        return 0.0

    hits = 0.0
    ap = 0.0
    for i, r in enumerate(top):
        if r > 0.0:
            hits += 1.0
            ap += hits / (i + 1.0)
    return ap / min(n_rel, float(k))


def map_at_k(ranked_relevances_per_query: Sequence[Sequence[float]], k: int = 10) -> float:
    """Mean average precision @ k across many queries."""
    if not ranked_relevances_per_query:
        raise ValueError("map_at_k needs ≥ 1 query")
    aps = [average_precision_at_k(r, k=k) for r in ranked_relevances_per_query]
    return float(np.mean(aps))


def recall_at_k(ranked_relevances_per_query: Sequence[Sequence[float]], k: int = 5) -> float:
    """Mean Recall@k across queries. ``Recall@k = (any hit in top-k?) / n_rel``.

    Capped at 1.0 (so a query with multiple golds still contributes at most 1).
    """
    if not ranked_relevances_per_query:
        raise ValueError("recall_at_k needs ≥ 1 query")
    recalls: list[float] = []
    for rel in ranked_relevances_per_query:
        arr = np.asarray(rel, dtype=np.float64)
        n_rel = float(np.sum(arr > 0.0))
        if n_rel == 0.0:
            continue
        top = arr[:k]
        hit = float(np.sum(top > 0.0))
        # Cap at 1.0: "did we find at least one relevant item in top-k".
        recalls.append(min(1.0, hit / min(n_rel, float(k))) if hit > 0 else 0.0)
    if not recalls:
        return 0.0
    return float(np.mean(recalls))


# ----- Clustering -----------------------------------------------------------


def v_measure_cluster(
    embeddings: np.ndarray,
    gold_labels: Sequence[str],
    *,
    n_clusters: int,
    random_state: int = 42,
) -> float:
    """Run KMeans on *embeddings*, return V-measure vs *gold_labels*."""
    from sklearn.cluster import KMeans  # lazy
    from sklearn.metrics import v_measure_score

    X = np.asarray(embeddings, dtype=np.float64)
    if X.ndim != 2:
        raise ValueError("embeddings must be 2D")
    if len(gold_labels) != X.shape[0]:
        raise ValueError("gold_labels length must match embeddings rows")

    km = KMeans(n_clusters=n_clusters, n_init=10, random_state=random_state)
    pred = km.fit_predict(X)
    return float(v_measure_score(gold_labels, pred))


# ----- Pair classification --------------------------------------------------


def roc_auc(y_true: Sequence[int], y_score: Sequence[float]) -> float:
    """ROC-AUC score. Returns 0.0 if undefined (single-class input)."""
    from sklearn.metrics import roc_auc_score

    yt = np.asarray(y_true, dtype=np.int64)
    ys = np.asarray(y_score, dtype=np.float64)
    if yt.size < 2 or len(set(yt.tolist())) < 2:
        return 0.0
    return float(roc_auc_score(yt, ys))


def accuracy_at_threshold(
    y_true: Sequence[int], y_score: Sequence[float], *, threshold: float = 0.5
) -> float:
    """Classification accuracy when ``score >= threshold`` is the positive class."""
    yt = np.asarray(y_true, dtype=np.int64)
    ys = np.asarray(y_score, dtype=np.float64)
    if yt.size == 0:
        return 0.0
    preds = (ys >= threshold).astype(np.int64)
    return float(np.mean(preds == yt))
