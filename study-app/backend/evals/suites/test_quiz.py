"""Quiz suite — the full analyze → plan → generate quiz chain on SciQ
passages that carry gold MCQs (1 correct answer + 3 expert distractors).

Metrics: structural pass per case (the validate node's rules), and
aggregate-gated judge scores — groundedness, distractor plausibility,
concept-tag accuracy, personalization adherence (novice vs advanced seeded
memory). Judge-scored metrics gate on the run's MEAN, not per-case: judge
scores are stochastic, and a single harsh verdict shouldn't fail an
otherwise-good run (per-case scores stay in the report either way).
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
    case_ids,
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


# --- Structure (the validate node's rules, before anything persists) ----------


@pytest.mark.parametrize("case", SCIQ_CASES, ids=case_ids)
async def test_quiz_structural(case):
    _, _, quiz = await quiz_chain(case["id"], case["passage"], {})
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
    assert ok, why


# --- Judge metrics: aggregate-gated (mean over the run's cases) ---------------


async def _judge_mean(suite_metric, cases, threshold, build) -> float:
    """Run a judge metric over cases, record per-case, return the clamped mean.

    Unparseable verdicts (judge_score → None) are skipped, not zeroed."""
    scores = []
    for case in cases:
        metric, test_case = await build(case)
        score, reason = await judge_score(metric, test_case)
        if score is None:
            record(
                "quiz", suite_metric, case=case["id"], score=0.0,
                threshold=threshold, success=False,
                reason=f"judge verdict unparseable — skipped: {reason[:120]}",
            )
            continue
        scores.append(score)
        record(
            "quiz", suite_metric, case=case["id"], score=score,
            threshold=threshold, success=score >= threshold,
            reason=reason,
        )
    assert scores, f"{suite_metric}: no parseable judge verdicts in this run"
    mean = sum(scores) / len(scores)
    return mean


async def test_quiz_groundedness():
    threshold = 0.80

    async def build(case):
        _, _, quiz = await quiz_chain(case["id"], case["passage"], {})
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

    async def build(case):
        _, _, quiz = await quiz_chain(case["id"], case["passage"], {})
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

    async def build(case):
        _, _, quiz = await quiz_chain(case["id"], case["passage"], {})
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

    async def build(case):
        _, _, novice_quiz = await quiz_chain(
            f"{case['id']}::novice", case["passage"], NOVICE_MEMORY
        )
        _, _, advanced_quiz = await quiz_chain(
            f"{case['id']}::advanced", case["passage"], ADVANCED_MEMORY
        )
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
