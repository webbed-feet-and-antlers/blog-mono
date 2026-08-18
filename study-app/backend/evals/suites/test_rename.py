"""Rename suite — suggest_filename over synthetic machine-noise filenames
(hex hashes, camera defaults, recorder timestamps) attached to real SciQ
passages.

Metrics: heuristic gate recall (deterministic — every synthetic bad name must
trip filename_needs_rename), rule compliance (≤80 chars, actually changed, no
residual gibberish), and a judge descriptiveness score gated on the run mean.
Suggestions and judge calls run concurrently (gather_bounded).
"""

from __future__ import annotations

import re

import pytest
from deepeval.test_case import LLMTestCase, SingleTurnParams

from evals.config import sample_cases
from evals.judge import rubric
from evals.report import record
from evals.suites import gather_bounded, judge_score, load_cases

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

_HEXISH = re.compile(
    r"[0-9a-f]{8,}|img[_-]?\d+|recording|untitled|scan_|document", re.I
)


async def _suggest_all() -> list[tuple[dict, str, str]]:
    """(case, bad_name, new_name) for every case, suggested concurrently."""
    from app.agent import tools

    async def one(case):
        bad_name = BAD_NAMES[len(case["id"]) % len(BAD_NAMES)]
        new_name = (await tools.suggest_filename(bad_name, case["passage"])) or ""
        return case, bad_name, new_name

    return await gather_bounded([(lambda c=case: one(c)) for case in SCIQ_CASES])


async def test_rename_rules():
    """Deterministic per-case gates: heuristic flag + suggestion rules."""
    from app.agent import tools

    results = await _suggest_all()
    problems: list[str] = []
    for case, bad_name, new_name in results:
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
        if not gated:
            problems.append(f"{case['id']}: gate missed noise name {bad_name!r}")

        # 2. The LLM suggestion must obey the deterministic rules.
        rules_ok = bool(new_name) and len(new_name) <= 80 and not _HEXISH.search(new_name)
        unchanged = (new_name or "").lower() == bad_name.rsplit(".", 1)[0].lower()
        record(
            "rename",
            "rule_pass",
            case=case["id"],
            score=1.0 if rules_ok else 0.0,
            threshold=1.0,
            success=rules_ok,
            reason=f"{bad_name!r} → {new_name!r}",
        )
        if not rules_ok:
            problems.append(f"{case['id']}: {bad_name!r} → {new_name!r} violates rules")
        if unchanged:
            problems.append(f"{case['id']}: {bad_name!r} → unchanged")
    assert not problems, problems


async def test_rename_descriptiveness():
    """Aggregate gate: the descriptiveness judge is harsh and occasionally
    numerically erratic per-case; the run's MEAN is the stable signal."""
    results = await _suggest_all()

    async def one(item):
        case, bad_name, new_name = item
        metric = rubric(
            "Name descriptiveness",
            criteria=(
                "Grade the proposed filename as a study-material organizer "
                "would: does it identify what this document is about — topic, "
                "material type — specifically enough to pick the file out of a "
                "folder of fifty? Generic names ('lecture notes', 'study "
                "material') score low; names naming the actual content score "
                "high."
            ),
            evaluation_params=[SingleTurnParams.CONTEXT, SingleTurnParams.ACTUAL_OUTPUT],
            threshold=0.45,
        )
        test_case = LLMTestCase(
            input="Proposed filename",
            actual_output=new_name,
            context=[case["passage"][:1500]],
        )
        score, reason = await judge_score(metric, test_case)
        return (
            case,
            score,
            (
                f"{bad_name} → {new_name}: {reason}"
                if score is not None
                else f"{bad_name} → {new_name}: judge verdict unparseable — skipped"
            ),
        )

    judged = await gather_bounded([(lambda i=item: one(item)) for item in results])
    scores = []
    for case, score, reason in judged:
        if score is not None:
            scores.append(score)
        record(
            "rename",
            "descriptiveness",
            case=case["id"],
            score=score if score is not None else 0.0,
            threshold=0.45,
            success=(score is not None and score >= 0.45),
            reason=reason,
        )
    assert scores, "no parseable judge verdicts in this run"
    mean = sum(scores) / len(scores)
    assert mean >= 0.45, f"mean name descriptiveness {mean:.2f} < 0.45"
