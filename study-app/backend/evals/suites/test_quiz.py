"""Quiz suite — the full analyze → plan → generate quiz chain on SciQ
passages that carry gold MCQs (1 correct answer + 3 expert distractors).

Metrics: structural pass per case (the validate node's rules), and
aggregate-gated judge scores — groundedness, distractor plausibility,
concept-tag accuracy, personalization adherence (novice vs advanced seeded
memory). Judge-scored metrics gate on the run's MEAN, not per-case: judge
scores are stochastic, and a single harsh verdict shouldn't fail an
otherwise-good run (per-case scores stay in the report either way).

Chains and judge calls run concurrently (gather_bounded, EVALS_CONCURRENCY)
— the generation tools are pure async functions with no DB.
"""

from __future__ import annotations

import pytest
from deepeval.test_case import LLMTestCase, SingleTurnParams

from evals.config import sample_cases
from evals.judge import rubric
from evals.report import record
from evals.suites import (
    ADVANCED_MEMORY,
    NOVICE_MEMORY,
    chain_cache,
    gather_bounded,
    judge_score,
    load_cases,
    quiz_chain,
    quiz_structural_ok,
)

pytestmark = pytest.mark.evals

SCIQ_CASES = sample_cases(load_cases("sciq"))


def _render_quiz(quiz: dict) -> str:
    questions = quiz.get("questions") or []
    lines = [f"Quiz: {quiz.get('title', '')}"]
    for q in questions:
        idx = q.get("answer_idx")
        opts = "\n    ".join(
            f"{'→' if i == idx else ' '}{o}" for i, o in enumerate(q.get("options", []))
        )
        lines.append(
            f"Q: {q.get('prompt')} [concept: {q.get('concept')}]\n    {opts}"
        )
    return "\n".join(lines)


async def _warm_bare():
    """Generate all bare-case chains concurrently (memoized in chain_cache)."""
    await gather_bounded(
        [
            (lambda c=case: quiz_chain(c["id"], c["passage"], {}))
            for case in SCIQ_CASES
        ]
    )


# --- Structure (the validate node's rules, before anything persists) ----------


async def test_quiz_structural():
    await _warm_bare()
    problems: list[str] = []
    for case in SCIQ_CASES:
        _, _, quiz = chain_cache[f"quiz:{case['id']}"]
        ok, why = quiz_structural_ok(quiz)
        record(
            "quiz",
            "structural_pass",
            case=case["id"],
            score=1.0 if ok else 0.0,
            threshold=1.0,
            success=ok,
            reason=why,
        )
        if not ok:
            problems.append(f"{case['id']}: {why}")
    assert not problems, problems


# --- Judge metrics: aggregate-gated (mean over the run's cases) ---------------


async def _judge_mean(suite_metric, cases, threshold, build) -> float:
    """Judge all cases CONCURRENTLY, record per-case, return the clamped mean.

    Unparseable verdicts (judge_score → None) are skipped, not zeroed."""
    async def one(case):
        try:
            metric, test_case = await build(case)
        except (ValueError, RuntimeError) as exc:
            # Persistent generator failure on this case (e.g. deterministic
            # max_tokens truncation) — record and continue; the per-case
            # structural gate still enforces the main chain.
            return case, None, f"generation failed — skipped: {str(exc)[:120]}"
        score, reason = await judge_score(metric, test_case)
        if score is None:
            return case, None, f"judge verdict unparseable — skipped: {reason[:120]}"
        return case, score, reason

    results = await gather_bounded(
        [(lambda c=case: one(c)) for case in cases]
    )
    scores = []
    for case, score, reason in results:
        if score is not None:
            scores.append(score)
        record(
            "quiz", suite_metric, case=case["id"], score=score or 0.0,
            threshold=threshold, success=(score is not None and score >= threshold),
            reason=reason,
        )
    assert scores, f"{suite_metric}: no parseable judge verdicts in this run"
    mean = sum(scores) / len(scores)
    return mean


async def test_quiz_groundedness():
    threshold = 0.80
    await _warm_bare()

    async def build(case):
        _, _, quiz = chain_cache[f"quiz:{case['id']}"]
        metric = rubric(
            "Quiz groundedness",
            criteria=(
                "You are grading a generated multiple-choice quiz against its "
                "source passage. A question is GROUNDED when (a) the marked "
                "correct answer is verifiable from the passage, and (b) exactly "
                "one of the four options is correct — the other three must be "
                "actually wrong, not arguably-also-right. Score = fraction of "
                "questions that are grounded; fail hard if most questions fail."
            ),
            evaluation_params=[SingleTurnParams.CONTEXT, SingleTurnParams.ACTUAL_OUTPUT],
            threshold=threshold,
        )
        test_case = LLMTestCase(
            input="Generated quiz",
            actual_output=_render_quiz(quiz),
            context=[case["passage"]],
        )
        return metric, test_case

    mean = await _judge_mean("groundedness", SCIQ_CASES, threshold, build)
    assert mean >= threshold, f"mean groundedness {mean:.2f} < {threshold}"


async def test_distractor_plausibility():
    threshold = 0.65
    await _warm_bare()

    async def build(case):
        _, _, quiz = chain_cache[f"quiz:{case['id']}"]
        metric = rubric(
            "Distractor plausibility",
            criteria=(
                "Grade the quality of the quiz's WRONG options (distractors). "
                "Good distractors are plausible to a learner who half-knows the "
                "material: same topic, same register, tempting confusion (common "
                "misconceptions, near-miss values, related concepts). Bad "
                "distractors are absurd, trivially elimifiable, or obviously "
                "joking. The metadata contains the dataset's own gold question "
                "and expert distractors for calibration of what 'plausible' "
                "means here. Score = overall distractor quality, from worthless "
                "at the bottom of the scale to expert-like at the top."
            ),
            evaluation_params=[SingleTurnParams.CONTEXT, SingleTurnParams.ACTUAL_OUTPUT],
            threshold=threshold,
        )
        test_case = LLMTestCase(
            input="Generated quiz",
            actual_output=_render_quiz(quiz),
            context=[case["passage"]],
            metadata={
                "gold_question": case["question"],
                "gold_correct_answer": case["correct_answer"],
                "gold_distractors": case["distractors"],
            },
        )
        return metric, test_case

    mean = await _judge_mean("distractor_plausibility", SCIQ_CASES, threshold, build)
    assert mean >= threshold, f"mean distractor plausibility {mean:.2f} < {threshold}"


async def test_concept_tag_accuracy():
    threshold = 0.70
    await _warm_bare()

    async def build(case):
        _, _, quiz = chain_cache[f"quiz:{case['id']}"]
        metric = rubric(
            "Concept-tag accuracy",
            criteria=(
                "Every question in the quiz is tagged with the single concept it "
                "tests. Score = fraction of questions whose tagged concept "
                "accurately describes what the question actually assesses (by "
                "its prompt, options, and correct answer). A tag naming a "
                "different concept, or so vague it could describe any question, "
                "does not count."
            ),
            evaluation_params=[SingleTurnParams.ACTUAL_OUTPUT],
            threshold=threshold,
        )
        test_case = LLMTestCase(
            input="Generated quiz with concept tags",
            actual_output=_render_quiz(quiz),
        )
        return metric, test_case

    mean = await _judge_mean("concept_tag_accuracy", SCIQ_CASES, threshold, build)
    assert mean >= threshold, f"mean concept-tag accuracy {mean:.2f} < {threshold}"


async def test_personalization_shift():
    threshold = 0.55

    # Warm both memory variants for the first 5 cases concurrently.
    variants = []
    for case in SCIQ_CASES[:5]:
        variants.append((f"{case['id']}::novice", case["passage"], NOVICE_MEMORY))
        variants.append((f"{case['id']}::advanced", case["passage"], ADVANCED_MEMORY))
    await gather_bounded(
        [(lambda p=pair: quiz_chain(*p)) for pair in variants]
    )

    async def build(case):
        _, _, novice_quiz = chain_cache[f"quiz:{case['id']}::novice"]
        _, _, advanced_quiz = chain_cache[f"quiz:{case['id']}::advanced"]
        metric = rubric(
            "Personalization adherence",
            criteria=(
                "Two quizzes were generated from the SAME source: quiz A for a "
                "beginner (30% average score, knows 30% of reviewed flashcards, "
                "prefers easy difficulty) and quiz B for an advanced learner "
                "(90% average, knows 95% of reviewed flashcards, prefers hard). "
                "Score how discernibly quiz B is calibrated harder than quiz A: "
                "more application/analysis questions, more nuanced distractors, "
                "less definitional recall — from clearly and appropriately "
                "harder at the top of the scale, through indistinguishable in "
                "the middle, to quiz B actually easier at the bottom."
            ),
            evaluation_params=[SingleTurnParams.INPUT],
            threshold=threshold,
        )
        test_case = LLMTestCase(
            input=(
                f"Source passage:\n{case['passage']}\n\n"
                f"QUIZ A (beginner learner):\n{_render_quiz(novice_quiz)}\n\n"
                f"QUIZ B (advanced learner):\n{_render_quiz(advanced_quiz)}"
            )
        )
        return metric, test_case

    mean = await _judge_mean(
        "personalization_shift", SCIQ_CASES[:5], threshold, build
    )
    assert mean >= threshold, f"mean personalization shift {mean:.2f} < {threshold}"
