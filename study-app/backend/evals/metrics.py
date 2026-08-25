"""Deterministic metrics — no LLM involved.

Plain helpers (concept_f1, auc, brier, log_loss) are used directly by the
replay suites; the DeepEval BaseMetric subclasses wrap the two reference-
comparison metrics so they flow through the same assert_test/report plumbing
as the judge metrics.
"""

from __future__ import annotations

import math
from collections import Counter

from deepeval.metrics import BaseMetric
from deepeval.test_case import LLMTestCase
from rapidfuzz import fuzz

# ---------------------------------------------------------------------------
# Concept matching (analysis suite)
# ---------------------------------------------------------------------------


def _concept_f1_half(predicted: list[str], gold: list[str], cutoff: int) -> float:
    """Greedy fuzzy match of `predicted` concepts against `gold` (token-set
    ratio >= cutoff counts as a match). Returns the half of F1 for this side.
    """
    gold_pool = list(gold)
    matched = 0
    # Highest-similarity-first greedy assignment.
    pairs = []
    for p in predicted:
        for gi, g in enumerate(gold_pool):
            score = fuzz.token_set_ratio(p.lower(), g.lower())
            if score >= cutoff:
                pairs.append((score, p, gi))
    pairs.sort(reverse=True)
    used_gold: set[int] = set()
    matched_preds: set[str] = set()
    for score, p, gi in pairs:
        if gi not in used_gold and p not in matched_preds:
            used_gold.add(gi)
            matched_preds.add(p)
            matched += 1
    precision = matched / len(predicted) if predicted else 0.0
    recall = matched / len(gold) if gold else 0.0
    return precision, recall


def concept_f1(
    predicted: list[str], gold: list[str], cutoff: int = 85
) -> tuple[float, float, float]:
    """Fuzzy (precision, recall, F1) between predicted and gold concept lists."""
    if not predicted and not gold:
        return (1.0, 1.0, 1.0)
    if not predicted or not gold:
        return (0.0, 0.0, 0.0)
    precision, recall = _concept_f1_half(predicted, gold, cutoff)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return (round(precision, 4), round(recall, 4), round(f1, 4))


class ConceptF1Metric(BaseMetric):
    """DeepEval wrapper: actual_output / expected_output are '||'-joined
    concept lists; score = fuzzy F1."""

    def __init__(self, threshold: float = 0.4, cutoff: int = 85):
        self.threshold = threshold
        self._cutoff = cutoff

    def measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        predicted = [c.strip() for c in (test_case.actual_output or "").split("||")]
        gold = [c.strip() for c in (test_case.expected_output or "").split("||")]
        _, _, f1 = concept_f1(predicted, gold, self._cutoff)
        self.score = f1
        self.success = f1 >= self.threshold
        self.reason = f"fuzzy F1={f1:.3f} (predicted {len(predicted)}, gold {len(gold)})"
        return self.score

    async def a_measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        return self.measure(test_case)

    def is_successful(self) -> bool | None:
        return self.success


# ---------------------------------------------------------------------------
# ROUGE coverage (notes suite)
# ---------------------------------------------------------------------------


class RougeMetric(BaseMetric):
    """ROUGE of actual_output vs expected_output (reference summary).

    `measure` picks which side: "recall" = what fraction of the reference
    the output covers (the right direction for notes-vs-abstract, where the
    notes are far longer than the gold text); "fmeasure" = the symmetric F1.
    """

    def __init__(self, rouge_type: str = "rouge1", threshold: float = 0.2,
                 measure: str = "recall"):
        self.threshold = threshold
        self._rouge_type = rouge_type
        self._measure = measure

    def measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        from rouge_score import rouge_scorer

        scorer = rouge_scorer.RougeScorer([self._rouge_type], use_stemmer=True)
        scores = scorer.score(
            test_case.expected_output or "", test_case.actual_output or ""
        )
        self.score = getattr(scores[self._rouge_type], self._measure)
        self.success = self.score >= self.threshold
        self.reason = f"{self._rouge_type} {self._measure}={self.score:.3f}"
        return self.score

    async def a_measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        return self.measure(test_case)

    def is_successful(self) -> bool | None:
        return self.success


# ---------------------------------------------------------------------------
# Calibration helpers (FSRS replay suite)
# ---------------------------------------------------------------------------


def auc(scores: list[float], outcomes: list[int]) -> float:
    """Rank-based AUC (Mann-Whitney) with tie handling. 0.5 = chance."""
    pairs = sorted(zip(scores, outcomes), key=lambda x: x[0])
    # Rank-average with ties.
    ranks: list[float] = []
    i = 0
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        avg_rank = (i + j) / 2 + 1  # 1-based average rank of the tie block
        ranks.extend([avg_rank] * (j - i + 1))
        i = j + 1
    n_pos = sum(outcomes)
    n_neg = len(outcomes) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5
    rank_sum_pos = sum(r for (_, o), r in zip(pairs, ranks) if o == 1)
    return (rank_sum_pos - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def brier(scores: list[float], outcomes: list[int]) -> float:
    """Mean squared error of predicted probabilities."""
    return sum((p - o) ** 2 for p, o in zip(scores, outcomes)) / len(outcomes)


def log_loss(scores: list[float], outcomes: list[int], eps: float = 1e-6) -> float:
    """Mean cross-entropy, clipped for stability."""
    total = 0.0
    for p, o in zip(scores, outcomes):
        p = min(max(p, eps), 1 - eps)
        total += -(o * math.log(p) + (1 - o) * math.log(1 - p))
    return total / len(outcomes)


def majority_accuracy(outcomes: list[int]) -> float:
    """Accuracy of the constant majority-class predictor — the floor any
    calibrated model must beat."""
    counts = Counter(outcomes)
    return counts.most_common(1)[0][1] / len(outcomes)
