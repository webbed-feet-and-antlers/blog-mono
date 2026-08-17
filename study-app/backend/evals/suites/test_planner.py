"""Planner suite — the real `generate_study_plan` over synthetic modules
seeded from SciQ passages: some concepts weak, some due, an exam 10 days
out. Deterministic invariants on the emitted items, plus a judge score for
whether each item's rationale actually cites the grounding data (the
production prompt demands evidence, so we check it).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from deepeval.test_case import LLMTestCase, SingleTurnParams

from app.agent import fsrs_scheduler
from app.agent.memory import write_memory
from app.db import SessionLocal
from app.models import Document, Module

from evals.config import DATA_DIR, sample_cases
from evals.judge import rubric
from evals.report import record
from evals.suites import case_ids, load_cases

pytestmark = pytest.mark.evals

SCIQ_CASES = sample_cases(load_cases("sciq"))[:5]  # planner calls are slow

NOW = datetime.now(timezone.utc)


def _due_state(days_ago: float = 6.0) -> dict:
    state = fsrs_scheduler.schedule_review(None, 3)
    state["last_review"] = (NOW - timedelta(days=days_ago)).isoformat()
    state["due"] = (NOW - timedelta(days=1)).isoformat()
    return state


def _future_state() -> dict:
    state = fsrs_scheduler.schedule_review(None, 3)
    state["due"] = (NOW + timedelta(days=20)).isoformat()
    return state


async def _seed_module(case: dict, index: int) -> tuple[str, dict]:
    """Module + 3 docs (SciQ passages) + mastery: 2 weak-due, 1 strong, 1 new."""
    module_id = f"plan-mod-{index}"
    doc_ids = []
    async with SessionLocal() as s:
        module = Module(
            id=module_id,
            title=f"BIO20{index} — {case['correct_answer'][:18]}",
            exam_date=date.today() + timedelta(days=10),
        )
        s.add(module)
        for j in range(3):
            doc_id = f"plan-{index}-doc{j}"
            s.add(Document(
                id=doc_id,
                filename=f"lecture-{index}-{j}.txt",
                mime="text/plain",
                file_path="/tmp/x.txt",
                text=case["passage"][:2000],
                kind="text",
                module_id=module_id,
            ))
            await write_memory(s, "doc", doc_id, "analysis", {
                "topic": f"{case['correct_answer']} part {j}",
                "concepts": [f"{case['correct_answer']} concept {j}a",
                             f"{case['correct_answer']} concept {j}b"],
            })
            doc_ids.append(doc_id)
        mastery = {
            f"{case['correct_answer']} concept 0a": {
                "correct": 1, "wrong": 4, "seen": 5, "mastery_pct": 0.2,
                "documents": [doc_ids[0]], "fsrs": _due_state(7)},
            f"{case['correct_answer']} concept 0b": {
                "correct": 2, "wrong": 3, "seen": 5, "mastery_pct": 0.4,
                "documents": [doc_ids[0]], "fsrs": _due_state(6)},
            f"{case['correct_answer']} concept 1a": {
                "correct": 5, "wrong": 0, "seen": 5, "mastery_pct": 1.0,
                "documents": [doc_ids[1]], "fsrs": _future_state()},
            # concept 1b / 2a / 2b: untested (new material, no entry)
        }
        await write_memory(s, "user", "", "concept_mastery", mastery)
        await write_memory(s, "user", "", "learner_profile", {
            "learner_level": "intermediate",
            "stats": {"avg_score": 0.65, "score_history":
                      [{"score": 0.6}, {"score": 0.7}]},
        })
        await s.commit()
    return module_id, {"doc_ids": doc_ids, "module_id": module_id}


@pytest.mark.parametrize(
    ("index", "case"),
    list(enumerate(SCIQ_CASES)),
    ids=[c["id"] for c in SCIQ_CASES],
)
async def test_plan_invariants_and_rationales(index, case):
    from app.agent.planner import ITEM_TYPES, generate_study_plan

    module_id, world = await _seed_module(case, index)

    plan = None
    for attempt in range(2):  # transient LLM failures shouldn't kill the case
        async with SessionLocal() as s:
            try:
                plan = await generate_study_plan(s, module_id)
                break
            except Exception:
                if attempt == 1:
                    raise
    assert plan is not None, "planner returned no plan"
    items = plan.items or []
    assert items, "plan has no items"

    # --- Deterministic invariants -----------------------------------------
    problems: list[str] = []

    if not all(i.get("type") in ITEM_TYPES for i in items):
        problems.append("invalid item type present")

    per_day: dict[int, int] = {}
    for it in items:
        day = int(it.get("day_offset", 0))
        per_day[day] = per_day.get(day, 0) + int(it.get("estimate_mins", 0))
        if day < 0 or day > 14:
            problems.append(f"day_offset {day} outside horizon")
    max_daily = max(per_day.values()) if per_day else 0
    if max_daily > 60:  # prompt promises ≤45; validator doesn't enforce — margin
        problems.append(f"daily load {max_daily}min exceeds budget")

    # Weak/due concepts should be engaged early: some review item within
    # the first two days when due concepts exist.
    early = [i for i in items if int(i.get("day_offset", 0)) <= 2
             and str(i.get("type", "")).startswith("review")]
    due_scheduled_early = bool(early)

    invariants_ok = not problems
    record(
        "planner", "invariants_pass", case=case["id"],
        score=1.0 if invariants_ok else 0.0, threshold=1.0,
        success=invariants_ok, reason="; ".join(problems) or "all invariants hold",
    )
    assert invariants_ok, problems

    record(
        "planner", "weak_engaged_early", case=case["id"],
        score=1.0 if due_scheduled_early else 0.0, threshold=0.6,
        success=due_scheduled_early,
        reason=f"{len(early)} review items in first 2 days",
    )
    record(
        "planner", "max_daily_minutes", case=case["id"],
        score=max_daily, threshold=None, success=None,
        reason="heaviest day's estimate_mins total",
    )

    # --- Judge: does each rationale cite the grounding? --------------------
    rendered = "\n".join(
        f"- day {it.get('day_offset')}: [{it.get('type')}] {it.get('title')} "
        f"— {it.get('rationale')} (~{it.get('estimate_mins')} min)"
        for it in items
    )
    grounding_summary = (
        f"Module: BIO20{index}. Exam in 10 days. Documents: 3 lectures on "
        f"{case['correct_answer']}. Weakest concepts (20%/40% mastery, FSRS-due): "
        f"'{case['correct_answer']} concept 0a', "
        f"'{case['correct_answer']} concept 0b'. Strong: concept 1a (100%). "
        "Learner: intermediate, avg 65%."
    )
    metric = rubric(
        "Rationale evidence",
        criteria=(
            "A study plan's items each carry a rationale. Given the module's "
            "grounding facts, score what fraction of rationales cite real "
            "evidence (weak concepts by name, due-for-review status, the "
            "exam timing, unread documents) rather than generic filler "
            "('this will help you succeed') — from every rationale naming "
            "its evidence at the top of the scale to pure filler at the "
            "bottom."
        ),
        evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT],
        threshold=0.60,
    )
    test_case = LLMTestCase(
        input=f"Grounding facts:\n{grounding_summary}",
        actual_output=rendered,
    )
    from evals.suites import judge_score

    score, reason = await judge_score(metric, test_case)
    record(
        "planner", "rationale_cites_evidence", case=case["id"],
        score=score if score is not None else 0.0, threshold=metric.threshold,
        success=(score is not None and score >= metric.threshold), reason=reason,
    )
    if score is None:
        pytest.skip("judge verdict unparseable after retry")
    assert score >= metric.threshold, reason
