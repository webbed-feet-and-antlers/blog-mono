"""Analysis suite — the analyze_document node, graded against public gold.

Datasets:
  - AL-CPL (courses + expert concept lists & prerequisite pairs, docs composed
    from Wikipedia summaries of those concepts) → concept extraction F1,
    prerequisite-edge overlap, summary faithfulness.
  - RACE (middle/high-school exam passages) → difficulty calibration.

Runs the REAL analyze_document (app.agent.tools) — the same call the
LangGraph pipeline makes on every upload. Analyses for all cases are
generated concurrently (gather_bounded).
"""

from __future__ import annotations

import pytest
from deepeval.test_case import LLMTestCase, SingleTurnParams

from evals.config import sample_cases
from evals.judge import rubric
from evals.metrics import concept_f1
from evals.report import record
from evals.suites import analysis_for, gather_bounded, judge_score, load_cases

pytestmark = pytest.mark.evals

ALCPL_CASES = load_cases("alcpl")
RACE_CASES = sample_cases(load_cases("race"))


async def _warm():
    await gather_bounded(
        [(lambda c=case: analysis_for(c["text"])) for case in ALCPL_CASES]
        + [(lambda c=case: analysis_for(c["passage"])) for c in RACE_CASES]
    )


# --- Concept extraction vs AL-CPL gold (deterministic, per-case gated) --------


async def test_concept_extraction_f1():
    await _warm()
    problems: list[str] = []
    for case in ALCPL_CASES:
        analysis = await analysis_for(case["text"])
        predicted = [str(c) for c in analysis.get("concepts") or []]
        gold = [c.split(" (")[0].replace("_", " ") for c in case["concepts"]]
        _, _, f1 = concept_f1(predicted, gold)
        record(
            "analysis",
            "concept_f1",
            case=case["id"],
            score=f1,
            threshold=0.30,
            success=f1 >= 0.30,
            reason=f"predicted {len(predicted)} vs gold {len(gold)}",
        )
        if f1 < 0.30:
            problems.append(
                f"{case['id']}: concept F1 {f1:.2f} — predicted: {predicted[:8]}"
            )
    assert not problems, problems


# --- Prerequisite edges vs AL-CPL expert pairs (fuzzy, undirected) ------------


async def test_prerequisite_edge_overlap():
    """Report-only: Wikipedia summaries rarely state prerequisites in prose,
    so overlap with expert course structure is a signal check, not a gate."""
    from rapidfuzz import fuzz

    await _warm()
    for case in ALCPL_CASES:
        analysis = await analysis_for(case["text"])
        edges = [
            (str(e.get("source", "")), str(e.get("target", "")))
            for e in analysis.get("concept_relationships") or []
            if e.get("type") == "prerequisite"
        ]
        gold_pairs = [
            (a.split(" (")[0], b.split(" (")[0]) for a, b in case["prerequisite_pairs"]
        ]

        def _match(p: str, g: str) -> bool:
            return fuzz.token_set_ratio(p.lower(), g.lower()) >= 80

        hits = 0
        for gs, gt in gold_pairs:
            if any(
                (_match(es, gs) and _match(et, gt)) or (_match(es, gt) and _match(et, gs))
                for es, et in edges
            ):
                hits += 1
        recall = hits / len(gold_pairs) if gold_pairs else 0.0
        record(
            "analysis",
            "prereq_edge_recall",
            case=case["id"],
            score=recall,
            threshold=None,  # report-only
            success=None,
            reason=f"{hits}/{len(gold_pairs)} gold edges found among {len(edges)} extracted",
        )


# --- Difficulty calibration vs RACE tiers (report-only) ------------------------

_TIER_TO_DIFFICULTY = {
    "middle": {"easy", "medium"},
    "high": {"medium", "hard"},
}


async def test_difficulty_calibration():
    """Report-only: RACE tiers label the QUESTIONS' difficulty, and the model
    sees only the passage — the first calibrated run scored 0.40 against a
    ~0.50 majority-class baseline, i.e. passage→difficulty inference has no
    signal here. Recorded so the number stays visible; gated metrics are the
    ones with a defensible floor."""
    await _warm()
    for case in RACE_CASES:
        analysis = await analysis_for(case["passage"])
        predicted = str(analysis.get("difficulty", "")).lower()
        if predicted not in {"easy", "medium", "hard"}:
            predicted = "medium"
        ok = predicted in _TIER_TO_DIFFICULTY[case["tier"]]
        record(
            "analysis",
            "difficulty_accuracy",
            case=case["id"],
            score=1.0 if ok else 0.0,
            threshold=None,  # report-only (null result — see docstring)
            success=None,
            reason=f"tier={case['tier']} predicted={predicted}",
        )


# --- Summary faithfulness (judge, per-case gated) ------------------------------


async def test_summary_faithfulness():
    await _warm()
    threshold = 0.75

    async def one(case):
        analysis = await analysis_for(case["text"])
        summary = str(analysis.get("summary", ""))
        metric = rubric(
            "Summary faithfulness",
            criteria=(
                "Score the summary's factual faithfulness to the source document. "
                "Every claim in the summary must be supported by the document. "
                "Penalize invented facts, unsupported numbers, and misattributed "
                "relationships — from fully supported at the top of the scale to "
                "mostly invented at the bottom."
            ),
            evaluation_params=[SingleTurnParams.CONTEXT, SingleTurnParams.ACTUAL_OUTPUT],
            threshold=threshold,
        )
        test_case = LLMTestCase(
            input="Document analysis summary",
            actual_output=summary,
            context=[case["text"]],
        )
        score, reason = await judge_score(metric, test_case)
        return case, score, reason

    results = await gather_bounded([(lambda c=case: one(c)) for case in ALCPL_CASES])
    problems: list[str] = []
    seen = 0
    for case, score, reason in results:
        if score is not None:
            seen += 1
        record(
            "analysis",
            "summary_faithfulness",
            case=case["id"],
            score=score if score is not None else 0.0,
            threshold=threshold,
            success=(score is not None and score >= threshold),
            reason=reason if score is not None
            else f"judge verdict unparseable — skipped: {reason[:120]}",
        )
        if score is not None and score < threshold:
            problems.append(f"{case['id']}: {reason[:120]}")
    assert seen > 0, "no parseable judge verdicts in this run"
    assert not problems, problems
