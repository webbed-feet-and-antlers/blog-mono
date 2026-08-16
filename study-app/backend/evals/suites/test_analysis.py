"""Analysis suite — the analyze_document node, graded against public gold.

Datasets:
  - AL-CPL (courses + expert concept lists & prerequisite pairs, docs composed
    from Wikipedia summaries of those concepts) → concept extraction F1,
    prerequisite-edge overlap, summary faithfulness.
  - RACE (middle/high-school exam passages) → difficulty calibration.

Runs the REAL analyze_document (app.agent.tools) — the same call the
LangGraph pipeline makes on every upload.
"""

from __future__ import annotations

import json

import pytest
from deepeval.test_case import LLMTestCase, SingleTurnParams

from evals.config import sample_cases
from evals.judge import rubric
from evals.metrics import concept_f1
from evals.report import record
from evals.suites import case_ids, load_cases

pytestmark = pytest.mark.evals

ALCPL_CASES = load_cases("alcpl")
RACE_CASES = load_cases("race")


async def _analysis(case_id: str, text: str) -> dict:
    from app.agent import tools

    from evals.suites import chain_cache

    if f"analysis:{case_id}" not in chain_cache:
        chain_cache[f"analysis:{case_id}"] = await tools.analyze_document(text)
    return chain_cache[f"analysis:{case_id}"]


# --- Concept extraction vs AL-CPL gold ----------------------------------------


@pytest.mark.parametrize("case", ALCPL_CASES, ids=case_ids)
async def test_concept_extraction_f1(case):
    analysis = await _analysis(case["id"], case["text"])
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
    assert f1 >= 0.30, f"concept F1 {f1:.2f} — predicted: {predicted[:8]}"


# --- Prerequisite edges vs AL-CPL expert pairs (fuzzy, undirected) ------------


@pytest.mark.parametrize("case", ALCPL_CASES, ids=case_ids)
async def test_prerequisite_edge_overlap(case):
    """Report-only: Wikipedia summaries rarely state prerequisites in prose,
    so overlap with expert course structure is a signal check, not a gate."""
    from rapidfuzz import fuzz

    analysis = await _analysis(case["id"], case["text"])
    edges = [
        (str(e.get("source", "")), str(e.get("target", "")))
        for e in analysis.get("concept_relationships") or []
        if e.get("type") == "prerequisite"
    ]
    gold_pairs = [(a.split(" (")[0], b.split(" (")[0]) for a, b in case["prerequisite_pairs"]]

    def _match(p: str, g: str) -> bool:
        return fuzz.token_set_ratio(p.lower(), g.lower()) >= 80

    hits = 0
    for gs, gt in gold_pairs:
        if any((_match(es, gs) and _match(et, gt)) or (_match(es, gt) and _match(et, gs))
               for es, et in edges):
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


# --- Difficulty calibration vs RACE tiers -------------------------------------

_TIER_TO_DIFFICULTY = {
    "middle": {"easy", "medium"},
    "high": {"medium", "hard"},
}


@pytest.mark.parametrize("case", sample_cases(RACE_CASES), ids=case_ids)
async def test_difficulty_calibration(case):
    """Report-only: RACE tiers label the QUESTIONS' difficulty, and the model
    sees only the passage — the first calibrated run scored 0.40 against a
    ~0.50 majority-class baseline, i.e. passage→difficulty inference has no
    signal here. Recorded so the number stays visible; gated metrics are the
    ones with a defensible floor."""
    analysis = await _analysis(f"race-{case['id']}", case["passage"])
    predicted = str(analysis.get("difficulty", "")).lower()
    if predicted not in {"easy", "medium", "hard"}:
        predicted = "medium"
    ok = predicted in _TIER_TO_DIFFICULTY[case["tier"]]
    record(
        "analysis",
        "difficulty_accuracy",
        case=case["id"],
        score=1.0 if ok else 0.0,
        threshold=None,
        success=None,
        reason=f"tier={case['tier']} predicted={predicted}",
    )


# --- Summary faithfulness (judge) ----------------------------------------------


@pytest.mark.parametrize("case", ALCPL_CASES, ids=case_ids)
async def test_summary_faithfulness(case):
    analysis = await _analysis(case["id"], case["text"])
    summary = str(analysis.get("summary", ""))
    metric = rubric(
        "Summary faithfulness",
        criteria=(
            "Score the summary's factual faithfulness to the source document. "
            "Every claim in the summary must be supported by the document. "
            "Penalize invented facts, unsupported numbers, and misattributed "
            "relationships. 1.0 = fully supported, 0.0 = mostly invented."
        ),
        evaluation_params=[SingleTurnParams.CONTEXT, SingleTurnParams.ACTUAL_OUTPUT],
        threshold=0.75,
    )
    test_case = LLMTestCase(
        input="Document analysis summary",
        actual_output=summary,
        context=[case["text"]],
    )
    await metric.a_measure(test_case, _show_indicator=False)
    record(
        "analysis",
        "summary_faithfulness",
        case=case["id"],
        score=metric.score or 0.0,
        threshold=metric.threshold,
        success=metric.is_successful(),
        reason=metric.reason or "",
    )
    assert metric.is_successful(), metric.reason
