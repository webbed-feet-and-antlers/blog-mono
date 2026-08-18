"""Notes suite — generate_notes on PubMed research documents that carry
human-written abstracts as gold summaries.

Metrics: markdown structure (deterministic), ROUGE-1 coverage vs the gold
abstract (deterministic), DeepEval faithfulness (claims supported by the
source, judged by the dedicated judge), and a judge key-point coverage score.
Judge-scored metrics gate on the run's mean; chains and judge calls run
concurrently (gather_bounded) — the generation tools touch no DB.
"""

from __future__ import annotations

import pytest
from deepeval.metrics.faithfulness.faithfulness import FaithfulnessMetric
from deepeval.test_case import LLMTestCase, SingleTurnParams

from evals.config import sample_cases
from evals.judge import judge, rubric
from evals.metrics import RougeMetric
from evals.report import record
from evals.suites import (
    analysis_for,
    chain_cache,
    gather_bounded,
    judge_score,
    load_cases,
)

pytestmark = pytest.mark.evals

NOTES_CASES = sample_cases(load_cases("notes_corpus"))


async def _notes_chain(case: dict):
    """analyze → plan → generate_notes; memoized per case in chain_cache."""
    from app.agent import tools

    key = f"notes:{case['id']}"
    if key not in chain_cache:
        analysis = await analysis_for(case["article"])
        plan = await tools.plan_task("notes", analysis, {}, None)
        markdown = ""
        for _ in range(3):  # empty markdown (transient) — retry before giving up
            try:
                result = await tools.generate_notes(case["article"], analysis, plan, {})
                markdown = str(result.get("markdown", ""))
                if markdown.strip():
                    break
            except ValueError:
                continue
        chain_cache[key] = (analysis, plan, {"markdown": markdown})
    return chain_cache[key]


async def _warm():
    await gather_bounded(
        [(lambda c=case: _notes_chain(c)) for case in NOTES_CASES]
    )


def _markdown(case) -> str:
    return str(chain_cache[f"notes:{case['id']}"][2].get("markdown", ""))


# --- Markdown structure + ROUGE-1 vs the gold abstract (deterministic) --------
# ROUGE is report-only: good notes restructure and simplify, so lexical
# overlap with the abstract is structurally low; the meaningful coverage gate
# is the judge's key-point score below.


async def test_notes_structure_and_rouge():
    await _warm()
    problems: list[str] = []
    for case in NOTES_CASES:
        markdown = _markdown(case)
        headings = [l for l in markdown.splitlines() if l.startswith("##")]
        ok = len(headings) >= 2 and len(markdown) >= 400
        record(
            "notes",
            "structure_pass",
            case=case["id"],
            score=1.0 if ok else 0.0,
            threshold=1.0,
            success=ok,
            reason=f"{len(headings)} headings, {len(markdown)} chars",
        )
        if not ok:
            problems.append(
                f"{case['id']}: only {len(headings)} headings / {len(markdown)} chars"
            )

        metric = RougeMetric(rouge_type="rouge1", measure="recall")
        test_case = LLMTestCase(
            input="Generated notes",
            actual_output=markdown,
            expected_output=case["summary"],
        )
        metric.measure(test_case)
        record(
            "notes",
            "rouge1_recall_vs_abstract",
            case=case["id"],
            score=metric.score or 0.0,
            threshold=None,
            success=None,
            reason=metric.reason or "",
        )
    assert not problems, problems


# --- Faithfulness (DeepEval metric on the dedicated judge) ---------------------


async def test_notes_faithfulness():
    await _warm()
    threshold = 0.75

    async def one(case):
        metric = FaithfulnessMetric(
            threshold=threshold,
            model=judge(),
            async_mode=True,
            include_reason=True,
        )
        test_case = LLMTestCase(
            input="Generated study notes",
            actual_output=_markdown(case),
            retrieval_context=[case["article"]],
        )
        await metric.a_measure(test_case, _show_indicator=False)
        score = metric.score if metric.score is not None and metric.score >= 0 else None
        return case, score, metric.reason or ""

    results = await gather_bounded([(lambda c=case: one(c)) for case in NOTES_CASES])
    scores = []
    for case, score, reason in results:
        if score is not None:
            scores.append(score)
        record(
            "notes", "faithfulness", case=case["id"],
            score=score if score is not None else 0.0,
            threshold=threshold,
            success=(score is not None and score >= threshold),
            reason=reason,
        )
    assert scores, "no parseable verdicts in this run"
    mean = sum(scores) / len(scores)
    assert mean >= threshold, f"mean notes faithfulness {mean:.2f} < {threshold}"


# --- Key-point coverage vs the gold abstract (judge) ---------------------------


async def test_notes_key_point_coverage():
    # Aggregate gate — judge scores are stochastic; observed mean 0.74 (min 0.0
    # on papers where the notes went generic, which is a real quality signal
    # worth seeing in the report even when the run passes).
    await _warm()
    threshold = 0.60

    async def one(case):
        metric = rubric(
            "Key-point coverage",
            criteria=(
                "The reference summary is what a domain expert extracted from "
                "the paper. Score what fraction of the reference's key points a "
                "reader would learn from the generated notes. Notes may "
                "reorganize and add explanatory detail — that's fine — but "
                "missing central findings, methods, or conclusions lowers the "
                "score, down to the bottom of the scale when nothing central "
                "survives."
            ),
            evaluation_params=[
                SingleTurnParams.EXPECTED_OUTPUT,
                SingleTurnParams.ACTUAL_OUTPUT,
            ],
            threshold=threshold,
        )
        test_case = LLMTestCase(
            input="Generated study notes vs expert summary",
            actual_output=_markdown(case),
            expected_output=case["summary"],
        )
        score, reason = await judge_score(metric, test_case)
        return case, score, reason

    results = await gather_bounded([(lambda c=case: one(c)) for case in NOTES_CASES])
    scores = []
    for case, score, reason in results:
        if score is not None:
            scores.append(score)
        record(
            "notes", "key_point_coverage", case=case["id"],
            score=score if score is not None else 0.0,
            threshold=threshold,
            success=(score is not None and score >= threshold),
            reason=reason,
        )
    assert scores, "no parseable judge verdicts in this run"
    mean = sum(scores) / len(scores)
    assert mean >= threshold, f"mean key-point coverage {mean:.2f} < {threshold}"
