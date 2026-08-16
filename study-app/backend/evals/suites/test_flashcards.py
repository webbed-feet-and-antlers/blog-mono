"""Flashcards suite — the analyze → plan → generate flashcards chain on SciQ
passages. No public flashcard benchmark exists, so this suite pairs
deterministic structure/distinctness checks with a Matuschak-style judge
rubric (atomic, source-grounded, no shallow variations) plus the
personalization axis (application-style when the learner knows ~everything).
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
    clamp01,
    deck_structural_ok,
    flashcard_chain,
    load_cases,
)

pytestmark = pytest.mark.evals

SCIQ_CASES = sample_cases(load_cases("sciq"))


def _render_deck(deck: dict) -> str:
    lines = [f"Deck: {deck.get('title', '')}"]
    for c in deck.get("cards") or []:
        lines.append(f"[{c.get('concept')}]")
        for v in c.get("variants") or []:
            lines.append(f"  front: {v.get('front')}")
            lines.append(f"  back:  {v.get('back')}")
    return "\n".join(lines)


def _variant_distinctness(deck: dict) -> float:
    """Fraction of cards whose variant fronts are not near-duplicates
    (token-set overlap < 0.8)."""
    def _tokens(s: str) -> set[str]:
        return set(str(s).lower().split())

    cards = deck.get("cards") or []
    if not cards:
        return 0.0
    distinct = 0
    for c in cards:
        variants = c.get("variants") or []
        if len(variants) < 2:
            continue
        a, b = _tokens(variants[0].get("front", "")), _tokens(variants[-1].get("front", ""))
        overlap = len(a & b) / max(1, len(a | b))
        if overlap < 0.8:
            distinct += 1
    return distinct / len(cards)


# --- Structure: ≥3 cards, 2 variants each, non-empty (the validate node) ------


@pytest.mark.parametrize("case", SCIQ_CASES, ids=case_ids)
async def test_deck_structural(case):
    _, _, deck = await flashcard_chain(case["id"], case["passage"], {})
    ok, why = deck_structural_ok(deck)
    record(
        "flashcards",
        "structural_pass",
        case=case["id"],
        score=1.0 if ok else 0.0,
        threshold=1.0,
        success=ok,
        reason=why,
    )
    assert ok, why


# --- Variant distinctness (deterministic) --------------------------------------


@pytest.mark.parametrize("case", SCIQ_CASES, ids=case_ids)
async def test_variant_distinctness(case):
    _, _, deck = await flashcard_chain(case["id"], case["passage"], {})
    cards = deck.get("cards") or []
    if not cards:
        # Empty deck already fails the structural gate; don't cascade.
        record(
            "flashcards", "variant_distinctness", case=case["id"],
            score=0.0, threshold=0.80, success=False,
            reason="empty deck — generation failed",
        )
        pytest.skip("empty deck — generation failed")
    score = _variant_distinctness(deck)
    record(
        "flashcards",
        "variant_distinctness",
        case=case["id"],
        score=score,
        threshold=0.80,
        success=score >= 0.80,
        reason="fraction of cards with non-duplicate variant phrasings",
    )
    assert score >= 0.80, f"only {score:.0%} of cards have distinct variants"


# --- Judge rubric: atomic, grounded, not shallow (aggregate-gated) -------------


async def test_deck_quality_rubric():
    threshold = 0.60  # regression floor; observed mean 0.86 (min 0.40)
    scores = []
    for case in SCIQ_CASES:
        _, _, deck = await flashcard_chain(case["id"], case["passage"], {})
        if not deck.get("cards"):
            record(
                "flashcards", "quality_rubric", case=case["id"],
                score=0.0, threshold=threshold, success=False,
                reason="empty deck — generation failed",
            )
            continue
        metric = rubric(
            "Flashcard quality",
            criteria=(
                "Grade this flashcard deck as a spaced-repetition practitioner "
                "would. A good deck: (1) ATOMIC — each front asks exactly one "
                "thing, not a compound question; (2) GROUNDED — every answer is "
                "supported by the source passage, nothing invented; (3) NOT "
                "TRIVIAL — cards test understanding that transfers, not "
                "word-order or filler recognition; (4) VARIANT QUALITY — the two "
                "variants of a card test the same knowledge from genuinely "
                "different angles rather than rewording the same cloze. Score "
                "0.0-1.0 overall."
            ),
            evaluation_params=[SingleTurnParams.CONTEXT, SingleTurnParams.ACTUAL_OUTPUT],
            threshold=threshold,
        )
        test_case = LLMTestCase(
            input="Generated flashcard deck",
            actual_output=_render_deck(deck),
            context=[case["passage"]],
        )
        await metric.a_measure(test_case, _show_indicator=False)
        score = clamp01(metric.score)
        scores.append(score)
        record(
            "flashcards",
            "quality_rubric",
            case=case["id"],
            score=score,
            threshold=threshold,
            success=score >= threshold,
            reason=metric.reason or "",
        )
    assert scores, "no decks generated at all"
    mean = sum(scores) / len(scores)
    assert mean >= threshold, f"mean deck quality {mean:.2f} < {threshold}"


# --- Personalization: known ~everything ⇒ application-style cards (aggregate) --


async def test_application_style_shift():
    threshold = 0.50  # regression floor; observed mean 0.82 (min 0.10)
    scores = []
    for case in SCIQ_CASES[:5]:
        _, _, novice_deck = await flashcard_chain(
            f"{case['id']}::novice", case["passage"], NOVICE_MEMORY
        )
        _, _, advanced_deck = await flashcard_chain(
            f"{case['id']}::advanced", case["passage"], ADVANCED_MEMORY
        )
        metric = rubric(
            "Application-style shift",
            criteria=(
                "Two decks from the SAME source: deck A for a beginner (knows "
                "30% of reviewed cards), deck B for a learner who knows 95% of "
                "reviewed flashcards (the system instructed deck B to favor "
                "application-style over definition cards). Score how well deck B "
                "avoids re-testing definitions the learner already knows and "
                "instead asks the knowledge to be applied, compared, or used in "
                "a new situation. 1.0 = clearly more application-oriented; 0.5 "
                "= indistinguishable; 0.0 = deck B is more definitional."
            ),
            evaluation_params=[SingleTurnParams.INPUT],
            threshold=threshold,
        )
        test_case = LLMTestCase(
            input=(
                f"Source passage:\n{case['passage']}\n\n"
                f"DECK A (beginner):\n{_render_deck(novice_deck)}\n\n"
                f"DECK B (knows 95% of cards):\n{_render_deck(advanced_deck)}"
            )
        )
        await metric.a_measure(test_case, _show_indicator=False)
        score = clamp01(metric.score)
        scores.append(score)
        record(
            "flashcards",
            "application_style_shift",
            case=case["id"],
            score=score,
            threshold=threshold,
            success=score >= threshold,
            reason=metric.reason or "",
        )
    assert scores, "no decks generated at all"
    mean = sum(scores) / len(scores)
    assert mean >= threshold, f"mean application-style shift {mean:.2f} < {threshold}"
