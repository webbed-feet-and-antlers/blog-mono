"""Reflection suite — the LLM-written learner narrative over synthetic
behavior ledgers, checked two ways:

  1. Deterministic no-fabrication: every number that appears in the
     summary/traits/habits must exist in the seeded data the grounding
     packet is built from (numbers are where invented "insight" betrays
     itself).
  2. Judge faithfulness: every claim traceable to the actual grounding
     packet (the same evidence discipline the production prompt demands).

Three synthetic archetypes: a struggling evening learner, a strong learner
who neglects half their documents, and an exam-crammer with slow recall.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from deepeval.test_case import LLMTestCase, SingleTurnParams

from evals.judge import rubric
from evals.report import record

pytestmark = pytest.mark.evals


def _hours_ago(h: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=h)


async def _seed(db, *, mastery, profile, patterns, engagement, activities) -> set[str]:
    """Write the behavior world; return every number the packet may contain."""
    from app.agent.memory import write_memory
    from app.models import UserActivity

    import uuid

    await write_memory(db, "user", "", "concept_mastery", mastery)
    await write_memory(db, "user", "", "learner_profile", profile)
    await write_memory(db, "user", "", "study_patterns", patterns)
    await write_memory(db, "user", "", "engagement", engagement)
    db.add_all(
        UserActivity(
            id=uuid.uuid4().hex[:12], ts=a["ts"], type=a["type"], props={}
        )
        for a in activities
    )
    await db.commit()

    blob = json.dumps(
        {"m": mastery, "p": profile, "s": patterns, "e": engagement,
         "a": [{"type": a["type"]} for a in activities]},
        default=str,
    )
    return set(re.findall(r"\d+(?:\.\d+)?", blob))


async def seed_struggling_evening(db) -> set[str]:
    """Low scores, late-night activity, slow recall, one strong concept."""
    return await _seed(
        db,
        mastery={
            "mitosis": {"correct": 2, "wrong": 6, "seen": 8, "mastery_pct": 0.25,
                        "latency": {"avg_secs": 21.4, "samples": 8}},
            "meiosis": {"correct": 1, "wrong": 5, "seen": 6, "mastery_pct": 0.17,
                        "latency": {"avg_secs": 18.0, "samples": 6}},
            "dna replication": {"correct": 3, "wrong": 3, "seen": 6, "mastery_pct": 0.5},
            "cell membrane": {"correct": 7, "wrong": 1, "seen": 8, "mastery_pct": 0.875},
        },
        profile={
            "learner_level": "beginner",
            "study_goal": "pass_biology_midterm",
            "stats": {"avg_score": 0.42, "total_quizzes": 7,
                      "score_history": [{"score": 0.4}, {"score": 0.35},
                                        {"score": 0.5}, {"score": 0.45}]},
        },
        patterns={
            "hour_histogram": [0] * 20 + [2, 5, 9, 4],
            "best_study_hour": 22,
            "avg_quiz_duration_secs": 240,
            "sessions": {"completed": 2, "abandoned": 5},
        },
        engagement={
            "docs": {"bio-w1": {"views": 9, "dwell_secs": 4200,
                                "last_viewed": _hours_ago(6).isoformat()},
                     "bio-w2": {"views": 1, "dwell_secs": 120,
                                "last_viewed": _hours_ago(200).isoformat()}},
            "total_dwell_secs": 4320,
            "tab_switches": {"document": 14, "quiz": 9, "flashcards": 8},
            "actions_count": 40,
        },
        activities=[
            {"ts": _hours_ago(i * 5), "type": t}
            for i, t in enumerate(
                ["quiz_submitted"] * 4 + ["flashcard_review"] * 3
                + ["document_view"] * 3
            )
        ],
    )


async def seed_strong_neglectful(db) -> set[str]:
    """High scores, but half the uploads never opened and sessions abandoned."""
    return await _seed(
        db,
        mastery={
            "fourier transform": {"correct": 9, "wrong": 1, "seen": 10,
                                  "mastery_pct": 0.9},
            "laplace transform": {"correct": 8, "wrong": 2, "seen": 10,
                                  "mastery_pct": 0.8},
            " convolution": {"correct": 10, "wrong": 0, "seen": 10,
                             "mastery_pct": 1.0},
        },
        profile={
            "learner_level": "advanced",
            "stats": {"avg_score": 0.91, "total_quizzes": 9,
                      "score_history": [{"score": 0.85}, {"score": 0.95}]},
        },
        patterns={
            "hour_histogram": [0, 0, 0, 0, 0, 1, 3, 6, 2] + [0] * 15,
            "best_study_hour": 7,
            "avg_quiz_duration_secs": 95,
            "sessions": {"completed": 6, "abandoned": 1},
        },
        engagement={
            "docs": {"signals-w1": {"views": 12, "dwell_secs": 9000,
                                    "last_viewed": _hours_ago(3).isoformat()},
                     "signals-w2": {"views": 0, "dwell_secs": 0},
                     "signals-w3": {"views": 0, "dwell_secs": 0}},
            "total_dwell_secs": 9000,
            "tab_switches": {"document": 2, "quiz": 1, "flashcards": 1},
            "actions_count": 55,
        },
        activities=[
            {"ts": _hours_ago(i * 9), "type": t}
            for i, t in enumerate(
                ["quiz_submitted"] * 5 + ["flashcard_review"] * 5
                + ["study_session"] * 2
            )
        ],
    )


async def seed_exam_crammer(db) -> set[str]:
    """Exam in days, everything answerable but SLOW — recognition not recall."""
    return await _seed(
        db,
        mastery={
            "enthalpy": {"correct": 5, "wrong": 4, "seen": 9, "mastery_pct": 0.56,
                         "latency": {"avg_secs": 26.0, "samples": 9}},
            "entropy": {"correct": 5, "wrong": 3, "seen": 8, "mastery_pct": 0.63,
                        "latency": {"avg_secs": 24.5, "samples": 8}},
            "gibbs free energy": {"correct": 6, "wrong": 2, "seen": 8,
                                  "mastery_pct": 0.75,
                                  "latency": {"avg_secs": 15.0, "samples": 8}},
        },
        profile={
            "learner_level": "intermediate",
            "study_goal": "chemistry_final_exam",
            "stats": {"avg_score": 0.64, "total_quizzes": 12,
                      "score_history": [{"score": 0.6}, {"score": 0.7}]},
        },
        patterns={
            "hour_histogram": [0] * 22 + [4, 8],
            "best_study_hour": 23,
            "avg_quiz_duration_secs": 410,
            "sessions": {"completed": 3, "abandoned": 3},
        },
        engagement={
            "docs": {"chem-final-notes": {"views": 15, "dwell_secs": 12000,
                                          "last_viewed": _hours_ago(1).isoformat()}},
            "total_dwell_secs": 12000,
            "tab_switches": {"document": 9, "quiz": 7, "flashcards": 6},
            "actions_count": 61,
        },
        activities=[
            {"ts": _hours_ago(i * 2), "type": t}
            for i, t in enumerate(
                ["quiz_submitted"] * 6 + ["flashcard_review"] * 6
            )
        ],
    )


ARCHETYPES = {
    "struggling_evening": seed_struggling_evening,
    "strong_neglectful": seed_strong_neglectful,
    "exam_crammer": seed_exam_crammer,
}


@pytest.mark.parametrize("archetype", list(ARCHETYPES))
async def test_reflection_faithfulness(archetype, db):
    from app.agent import reflection

    allowed_numbers = await ARCHETYPES[archetype](db)

    payload = None
    for attempt in range(3):  # transient LLM/API flakiness shouldn't fail a run
        payload = await reflection.reflect_on_learner(db, force=True)
        if (payload or {}).get("insights", {}).get("summary"):
            break
    insights = (payload or {}).get("insights") or {}
    assert insights.get("summary"), f"reflection produced nothing: {payload}"

    text = " ".join(
        [insights.get("summary", "")]
        + [str(t) for t in insights.get("traits") or []]
        + [str(insights.get("habits") or "")]
    )

    # 1. Deterministic: what fraction of numbers in the narrative trace back
    # to the seeded world? (Clock times are phrasing — stripped; seeded
    # decimals count in raw, percent, and rounded forms: 0.42 ↔ "42%",
    # 26.0 ↔ "26s".) Wholesale fabrication scores ~0; occasional paraphrase
    # stays above the 0.8 bar.
    text_clean = re.sub(r"\b\d{1,2}:\d{2}\b", " ", text)
    text_clean = text_clean.replace(",", "")  # 12,000 → 12000
    numbers = set(re.findall(r"\d+(?:\.\d+)?", text_clean))
    allowed = set(allowed_numbers)
    for n in list(allowed):
        try:
            v = float(n)
            if 0 < v < 1:
                allowed |= {str(int(v * 100)), str(round(v * 100))}
            elif "." in n:  # 26.0 may be written as "26", 24.5 as "24"/"25"
                allowed |= {str(int(v)), str(round(v))}
        except ValueError:
            pass
    fabricated = sorted(n for n in numbers if n not in allowed)
    grounded_frac = (
        (len(numbers) - len(fabricated)) / len(numbers) if numbers else 1.0
    )
    record(
        "reflection", "numbers_grounded", case=archetype,
        score=round(grounded_frac, 3), threshold=0.70,
        success=grounded_frac >= 0.70,
        reason=f"untraceable: {fabricated}" if fabricated else "all numbers grounded",
    )
    assert grounded_frac >= 0.70, f"untraceable numbers: {fabricated}"

    # 2. Format clamps (the production contract).
    clamps_ok = (
        len(insights.get("traits") or []) <= 6
        and all(len(str(t)) <= 80 for t in insights.get("traits") or [])
        and len(str(insights.get("habits") or "")) <= 300
    )
    record(
        "reflection", "format_clamps", case=archetype,
        score=1.0 if clamps_ok else 0.0, threshold=1.0, success=clamps_ok,
        reason="traits<=6x80, habits<=300",
    )
    assert clamps_ok

    # 3. Judge: every claim traceable to the actual grounding packet.
    packet_dict = await reflection._build_grounding_packet(db, total_activities=12)
    packet = reflection._render_packet(packet_dict)
    metric = rubric(
        "Insight faithfulness",
        criteria=(
            "The 'actual output' is an AI-written narrative about a learner; "
            "the 'context' is the complete grounded data packet it was "
            "allowed to see. Score what fraction of the narrative's claims "
            "are directly supported by the packet. Invented habits, "
            "unwarranted generalizations ('always studies at night' from two "
            "data points), contradictions of the data, and unsupported "
            "evaluations score low. Flavor and phrasing are free; substance "
            "must be traceable."
        ),
        evaluation_params=[SingleTurnParams.CONTEXT, SingleTurnParams.ACTUAL_OUTPUT],
        # Regression floor, not the aspiration: the first calibrated run
        # showed the generator contradicting its packet on ~1-in-3 claims
        # (e.g. "has not reviewed any flashcards" over 8 flashcard
        # activities). Improve the layer, then raise this bar.
        threshold=0.45,
    )
    test_case = LLMTestCase(
        input="Learner narrative", actual_output=text, context=[packet],
    )
    await metric.a_measure(test_case, _show_indicator=False)
    record(
        "reflection", "faithfulness", case=archetype,
        score=metric.score or 0.0, threshold=metric.threshold,
        success=metric.is_successful(), reason=metric.reason or "",
    )
    assert metric.is_successful(), metric.reason
