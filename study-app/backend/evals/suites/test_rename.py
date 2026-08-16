"""Rename suite — suggest_filename over synthetic machine-noise filenames
(hex hashes, camera defaults, recorder timestamps) attached to real SciQ
passages.

Metrics: heuristic gate recall (deterministic — every synthetic bad name must
trip filename_needs_rename), rule compliance (≤80 chars, actually changed, no
residual gibberish), and a judge descriptiveness score.
"""

from __future__ import annotations

import re

import pytest
from deepeval.test_case import LLMTestCase, SingleTurnParams

from evals.config import sample_cases
from evals.judge import rubric
from evals.report import record
from evals.suites import case_ids, load_cases

pytestmark = pytest.mark.evals

SCIQ_CASES = sample_cases(load_cases("sciq"))

# Deterministic rotation of realistic machine-generated noise names.
BAD_NAMES = [
    "a3f9c2e1b8d7.pdf",
    "IMG_2042.jpg",
    "recording-1728349921.wav",
    "document (3).pdf",
    "untitled 2.pdf",
    "week 4.pdf",
    "scan_0021.pdf",
    "1689023401_lecture.pdf",
]

_HEXISH = re.compile(r"[0-9a-f]{8,}|img[_-]?\d+|recording|untitled|scan_|document", re.I)


@pytest.mark.parametrize("case", SCIQ_CASES, ids=case_ids)
async def test_rename(case):
    from app.agent import tools

    bad_name = BAD_NAMES[len(case["id"]) % len(BAD_NAMES)]

    # 1. The heuristic gate must flag the noise name (no LLM call wasted).
    gated = tools.filename_needs_rename(bad_name)
    record(
        "rename",
        "gate_recall",
        case=case["id"],
        score=1.0 if gated else 0.0,
        threshold=1.0,
        success=gated,
        reason=f"{bad_name} flagged={gated}",
    )
    assert gated, f"gate missed noise name: {bad_name}"

    # 2. The LLM suggestion must obey the deterministic rules.
    new_name = await tools.suggest_filename(bad_name, case["passage"])
    rules_ok = bool(new_name) and len(new_name) <= 80 and not _HEXISH.search(new_name)
    record(
        "rename",
        "rule_pass",
        case=case["id"],
        score=1.0 if rules_ok else 0.0,
        threshold=1.0,
        success=rules_ok,
        reason=f"{bad_name!r} → {new_name!r}",
    )
    assert rules_ok, f"{bad_name!r} → {new_name!r} violates rules"
    assert (new_name or "").lower() != bad_name.rsplit(".", 1)[0].lower(), "name unchanged"


async def _descriptiveness(bad_name: str, new_name: str, case: dict) -> float:
    from evals.suites import clamp01

    metric = rubric(
        "Name descriptiveness",
        criteria=(
            "Grade the proposed filename as a study-material organizer "
            "would: does it identify what this document is about — topic, "
            "material type — specifically enough to pick the file out of a "
            "folder of fifty? Generic names ('lecture notes', 'study "
            "material') score low; names naming the actual content score "
            "high. Score 0.0-1.0."
        ),
        evaluation_params=[SingleTurnParams.CONTEXT, SingleTurnParams.ACTUAL_OUTPUT],
        threshold=0.45,
    )
    test_case = LLMTestCase(
        input="Proposed filename",
        actual_output=new_name,
        context=[case["passage"][:1500]],
    )
    await metric.a_measure(test_case, _show_indicator=False)
    score = clamp01(metric.score)
    record(
        "rename",
        "descriptiveness",
        case=case["id"],
        score=score,
        threshold=metric.threshold,
        success=score >= metric.threshold,
        reason=f"{bad_name} → {new_name}: {metric.reason or ''}",
    )
    return score


async def test_rename_descriptiveness():
    """Aggregate gate: the descriptiveness judge is harsh and occasionally
    numerically erratic per-case; the run's MEAN is the stable signal."""
    from app.agent import tools

    scores = []
    for case in SCIQ_CASES:
        bad_name = BAD_NAMES[len(case["id"]) % len(BAD_NAMES)]
        new_name = await tools.suggest_filename(bad_name, case["passage"])
        scores.append(await _descriptiveness(bad_name, new_name or "", case))
    assert scores, "no rename suggestions generated"
    mean = sum(scores) / len(scores)
    assert mean >= 0.45, f"mean name descriptiveness {mean:.2f} < 0.45"
