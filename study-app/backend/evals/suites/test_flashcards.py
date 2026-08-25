"""Flashcards suite — the analyze → plan → generate flashcards chain on SciQ
passages. No public flashcard benchmark exists, so this suite pairs
deterministic structure/distinctness checks with a Matuschak-style judge
rubric (atomic, source-grounded, no shallow variations) plus the
personalization axis (application-style when the learner knows ~everything).

Judge-scored metrics gate on the run's mean; chains and judge calls run
concurrently (gather_bounded) — the generation tools touch no DB.
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
    flashcard_chain,
    gather_bounded,
    judge_score,
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


async def _warm_bare():
    await gather_bounded(
        [
            (lambda c=case: flashcard_chain(c["id"], c["passage"], {}))
            for case in SCIQ_CASES
        ]
    )


# --- Structure + distinctness (deterministic; the validate node's rules) ------


async def test_deck_structural_and_distinctness():
    from evals.suites import deck_structural_ok

    await _warm_bare()
    problems: list[str] = []
    for case in SCIQ_CASES:
        _, _, deck = chain_cache[f"flashcards:{case['id']}"]
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
        if not ok:
            problems.append(f"{case['id']}: {why}")

        cards = deck.get("cards") or []
        if not cards:
            # Empty deck already fails the structural gate; don't cascade.
            record(
                "flashcards", "variant_distinctness", case=case["id"],
                score=0.0, threshold=0.80, success=False,
                reason="empty deck — generation failed",
            )
            continue
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
        if score < 0.80:
            problems.append(
                f"{case['id']}: only {score:.0%} of cards have distinct variants"
            )
    assert not problems, problems


# --- Judge rubric: atomic, grounded, not shallow (aggregate-gated) -------------


async def test_deck_quality_rubric():
    threshold = 0.60  # regression floor; observed mean 0.86 (min 0.40)
    await _warm_bare()

    async def one(case):
        _, _, deck = chain_cache[f"flashcards:{case['id']}"]
        if not deck.get("cards"):
            return case, None, "empty deck — generation failed"
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
                "different angles rather than rewording the same cloze."
            ),
            evaluation_params=[SingleTurnParams.CONTEXT, SingleTurnParams.ACTUAL_OUTPUT],
            threshold=threshold,
        )
        test_case = LLMTestCase(
            input="Generated flashcard deck",
            actual_output=_render_deck(deck),
            context=[case["passage"]],
        )
        score, reason = await judge_score(metric, test_case)
        return case, score, reason

    results = await gather_bounded([(lambda c=case: one(c)) for case in SCIQ_CASES])
    scores = []
    for case, score, reason in results:
        if score is not None:
            scores.append(score)
        record(
            "flashcards", "quality_rubric", case=case["id"],
            score=score if score is not None else 0.0,
            threshold=threshold,
            success=(score is not None and score >= threshold),
            reason=reason,
        )
    assert scores, "no decks generated at all"
    mean = sum(scores) / len(scores)
    assert mean >= threshold, f"mean deck quality {mean:.2f} < {threshold}"


# --- Personalization: known ~everything ⇒ application-style cards (aggregate) --


async def test_application_style_shift():
    threshold = 0.50  # regression floor; observed mean 0.82 (min 0.10)

    variants = []
    for case in SCIQ_CASES[:5]:
        variants.append((f"{case['id']}::novice", case["passage"], NOVICE_MEMORY))
        variants.append((f"{case['id']}::advanced", case["passage"], ADVANCED_MEMORY))
    await gather_bounded([(lambda p=pair: flashcard_chain(*p)) for pair in variants])

    async def one(case):
        _, _, novice_deck = chain_cache[f"flashcards:{case['id']}::novice"]
        _, _, advanced_deck = chain_cache[f"flashcards:{case['id']}::advanced"]
        metric = rubric(
            "Application-style shift",
            criteria=(
                "Two decks from the SAME source: deck A for a beginner (knows "
                "30% of reviewed cards), deck B for a learner who knows 95% of "
                "reviewed flashcards (the system instructed deck B to favor "
                "application-style over definition cards). Score how well deck B "
                "avoids re-testing definitions the learner already knows and "
                "instead asks the knowledge to be applied, compared, or used in "
                "a new situation — from clearly more application-oriented at "
                "the top of the scale, through indistinguishable in the middle, "
                "to deck B more definitional at the bottom."
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
        score, reason = await judge_score(metric, test_case)
        return case, score, reason

    results = await gather_bounded(
        [(lambda c=case: one(c)) for case in SCIQ_CASES[:5]]
    )
    scores = []
    for case, score, reason in results:
        if score is not None:
            scores.append(score)
        record(
            "flashcards", "application_style_shift", case=case["id"],
            score=score if score is not None else 0.0,
            threshold=threshold,
            success=(score is not None and score >= threshold),
            reason=reason,
        )
    assert scores, "no decks generated at all"
    mean = sum(scores) / len(scores)
    assert mean >= threshold, f"mean application-style shift {mean:.2f} < {threshold}"
