"""Generation tools — the feature-specific LLM calls.

These are invoked by the `generate` node. Keeping them as discrete functions
(rather than one giant prompt) lets each feature evolve independently while
sharing the surrounding agent loop (analysis, planning, memory, validation).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from ..llm import chat_json

# How much of a long document we feed into a single generation call. We slice
# around this many characters; the plan node decides which slices matter.
MAX_DOC_CHARS = 12000


def _truncate(text: str, limit: int = MAX_DOC_CHARS) -> str:
    if len(text) <= limit:
        return text
    # Keep the start and the end so we don't lose conclusions.
    head = text[: int(limit * 0.7)]
    tail = text[-int(limit * 0.3) :]
    return f"{head}\n\n[…truncated…]\n\n{tail}"


def _concept_hint(analysis: dict[str, Any]) -> str:
    concepts = analysis.get("concepts") or []
    if not concepts:
        return ""
    preview = ", ".join(str(c) for c in concepts[:15])
    return f"\nKey concepts identified in the document: {preview}."


def _memory_hint(memory: dict[str, Any], task_type: str) -> str:
    """Surface relevant prior learnings (the shared-backbone payoff)."""
    hints: list[str] = []

    # --- Learner profile (the highest-level personalization context) ---
    profile = memory.get("learner_profile") or {}
    if profile and profile.get("learner_level", "unknown") != "unknown":
        parts = [f"{profile['learner_level']} learner"]
        avg = (profile.get("stats") or {}).get("avg_score")
        if avg is not None:
            parts.append(f"avg {int(avg * 100)}%")
        diff = profile.get("preferred_difficulty")
        if diff:
            parts.append(f"prefers {diff} difficulty")
        fmts = profile.get("preferred_formats") or {}
        if task_type == "quiz" and fmts.get("quiz_length"):
            parts.append(f"{fmts['quiz_length']}-question quizzes")
        if task_type == "flashcards" and fmts.get("card_style"):
            parts.append(f"{fmts['card_style']}-style cards")
        if task_type == "notes" and fmts.get("notes_depth"):
            parts.append(f"{fmts['notes_depth']} notes")
        goal = profile.get("study_goal")
        if goal and goal != "unknown":
            parts.append(f"studying for {goal.replace('_', ' ')}")
        hints.append(
            "Learner profile: "
            + ", ".join(parts)
            + ". Calibrate difficulty and format accordingly."
        )

    # Prefer the rich per-concept mastery signal when available — it captures
    # both quiz and flashcard outcomes and lets the agent weight precisely.
    mastery = memory.get("concept_mastery") or []
    if mastery and task_type in ("quiz", "flashcards"):
        lines = []
        due_count = 0
        for m in mastery[:10]:
            pct = m.get("mastery_pct")
            if pct is None:
                label = "new (untested)"
            elif pct < 0.4:
                label = "VERY WEAK"
            elif pct < 0.7:
                label = "weak"
            else:
                label = "strong"
            correct = m.get("correct", 0)
            seen = m.get("seen", 0)
            seen_str = f"{correct}/{seen} correct" if seen else "untested"
            # FSRS due marker — concepts scientifically scheduled for review.
            due_marker = ""
            if m.get("due"):
                due_marker = " ⚡ DUE"
                due_count += 1
            lines.append(f"  - {m['concept']}: {seen_str} [{label}]{due_marker}")
        instruction = (
            "The learner's per-concept mastery (weight questions/cards toward "
            "weak/very-weak concepts; only briefly review strong ones"
        )
        if due_count:
            instruction += (
                f". {due_count} concept(s) marked ⚡ DUE are scientifically "
                "scheduled for spaced-repetition review — prioritize them "
                "to prevent forgetting"
            )
        instruction += "):\n" + "\n".join(lines)
        hints.append(instruction)
    elif memory.get("weak_topics") and task_type in ("quiz", "flashcards"):
        # Fallback: flat weak-topics list (no mastery detail).
        weak = memory["weak_topics"]
        hints.append(
            "The learner has struggled with these topics in past quizzes — "
            f"weight questions/cards toward them: {', '.join(weak[:8])}."
        )

    style = memory.get("notes_style")
    if style and task_type == "notes":
        hints.append(f"Preferred notes style: {style}.")
    attempts = memory.get("quiz_attempts")
    if attempts and task_type == "quiz":
        hints.append(
            f"The learner has taken {attempts} quiz attempt(s) on this document; "
            "increase difficulty slightly."
        )
    return ("\n" + "\n".join(hints)) if hints else ""


# --- Analysis (shared across features) ---


async def analyze_document(document_text: str) -> dict[str, Any]:
    """Extract topic, concepts, structure, difficulty from a document.

    Cached per-document in agent memory so it only runs once.
    """
    messages = [
        {
            "role": "system",
            "content": (
                "You analyze study documents to help an AI study agent. "
                "Return ONLY JSON with keys: "
                "topic (string), document_type (string, e.g. 'textbook chapter', "
                "'lecture slides', 'research paper'), "
                "difficulty (one of: easy, medium, hard), "
                "summary (2-3 sentence string), "
                "concepts (array of short strings, the key learnable concepts), "
                "sections (array of {title, summary} covering the document's structure)."
            ),
        },
        {
            "role": "user",
            "content": f"Analyze this document:\n\n{_truncate(document_text)}",
        },
    ]
    return await chat_json(messages, temperature=0.1, max_tokens=1500)


# --- Planning ---


async def plan_task(
    task_type: str,
    analysis: dict[str, Any],
    memory: dict[str, Any],
    instructions: str | None,
) -> dict[str, Any]:
    """Decide how to generate content for the requested task type."""
    task_descriptions = {
        "notes": (
            "Plan a structured study notes document. Decide on the section "
            "headings (aligned with the source), depth, and which concepts to "
            "emphasize. Return JSON: {sections: [{title, concepts: [..]}], "
            "depth: 'concise'|'standard'|'detailed'}."
        ),
        "quiz": (
            "Plan a multiple-choice quiz. Decide the number of questions "
            "(8-12), which concepts to cover, and difficulty distribution. "
            "Return JSON: {question_count: int, concepts_to_cover: [..], "
            "difficulty_mix: {easy: int, medium: int, hard: int}}."
        ),
        "flashcards": (
            "Plan a flashcard deck. Decide the number of cards (15-30), "
            "which concepts to turn into cards, and whether to favor "
            "definition-style or application-style cards. "
            "Return JSON: {card_count: int, concepts_to_cover: [..], "
            "card_style: 'definition'|'application'|'mixed'}."
        ),
    }
    messages = [
        {
            "role": "system",
            "content": (
                "You plan how an AI study agent should generate study material "
                "from a document, informed by what the agent already knows about "
                f"the learner. {task_descriptions[task_type]}\n\n"
                "IMPORTANT: Use the learner_profile in the memory to calibrate your plan. "
                "Match the preferred_difficulty for the difficulty mix. If preferred_formats "
                "specify a quiz_length, card_style, or notes_depth, use those as defaults. "
                "For beginner-level learners, favor simpler wording and more foundational "
                "concepts; for advanced learners, include harder application questions.\n\n"
                "If concept_mastery entries have due=true, prioritize those concepts in the "
                "plan. These are due for spaced-repetition review — the learner is at risk "
                "of forgetting them."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Document analysis:\n{json.dumps(analysis, indent=2)}\n\n"
                f"Memory about the learner:\n{json.dumps(memory)}\n\n"
                f"Learner instructions: {instructions or '(none)'}\n\n"
                f"Produce a plan for generating {task_type}."
            ),
        },
    ]
    return await chat_json(messages, temperature=0.2, max_tokens=800)


# --- Generation tools (one per feature) ---


async def generate_notes(
    document_text: str,
    analysis: dict[str, Any],
    plan: dict[str, Any],
    memory: dict[str, Any],
) -> dict[str, Any]:
    concept_hint = _concept_hint(analysis)
    style_hint = _memory_hint(memory, "notes")
    messages = [
        {
            "role": "system",
            "content": (
                "You write excellent study notes as Markdown. Be clear, "
                "accurate to the source, and pedagogically structured. Use "
                "headings (##), bullet points, and **bold** for key terms. "
                "Do NOT invent facts not in the source. Return ONLY JSON: "
                '{"markdown": "<the full notes as a markdown string>"}.'
            ),
        },
        {
            "role": "user",
            "content": (
                f"Source document:\n{_truncate(document_text)}\n\n"
                f"Plan:\n{json.dumps(plan, indent=2)}{concept_hint}{style_hint}\n\n"
                "Write the study notes."
            ),
        },
    ]
    result = await chat_json(messages, temperature=0.4, max_tokens=3000)
    if "markdown" not in result or not result["markdown"].strip():
        raise ValueError("generate_notes returned empty markdown")
    return result


async def generate_quiz(
    document_text: str,
    analysis: dict[str, Any],
    plan: dict[str, Any],
    memory: dict[str, Any],
) -> dict[str, Any]:
    concept_hint = _concept_hint(analysis)
    weakness_hint = _memory_hint(memory, "quiz")
    target_count = int(plan.get("question_count", 8))
    messages = [
        {
            "role": "system",
            "content": (
                "You write multiple-choice quiz questions that test real "
                "understanding (not trivia). Each question has 4 options, "
                "exactly one correct answer, and a concise explanation of why "
                "the answer is correct. Distractors must be plausible. "
                "Every question MUST include a 'concept' field naming the "
                "single concept it tests (from the plan's concepts_to_cover). "
                "Return ONLY JSON: "
                '{"title": string, "questions": ['
                '{"id": "q1", "prompt": string, "options": [4 strings], '
                '"answer_idx": 0-3, "explanation": string, '
                '"concept": string}]}. '
                f"Generate exactly {target_count} questions."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Source document:\n{_truncate(document_text)}\n\n"
                f"Plan:\n{json.dumps(plan, indent=2)}{concept_hint}{weakness_hint}\n\n"
                "Write the quiz."
            ),
        },
    ]
    result = await chat_json(messages, temperature=0.4, max_tokens=3500)
    _normalize_quiz_ids(result)
    return result


async def generate_flashcards(
    document_text: str,
    analysis: dict[str, Any],
    plan: dict[str, Any],
    memory: dict[str, Any],
) -> dict[str, Any]:
    concept_hint = _concept_hint(analysis)
    weakness_hint = _memory_hint(memory, "flashcards")
    messages = [
        {
            "role": "system",
            "content": (
                "You create flashcards for active recall. Each card has a "
                "concise front (prompt) and a clear back (answer). Keep cards "
                "atomic — one idea per card. Be accurate to the source. "
                "Every card MUST include a 'concept' field naming the single "
                "concept it covers (from the plan's concepts_to_cover). "
                "Return ONLY JSON: "
                '{"title": string, "cards": ['
                '{"id": "c1", "front": string, "back": string, '
                '"concept": string}]}.'
            ),
        },
        {
            "role": "user",
            "content": (
                f"Source document:\n{_truncate(document_text)}\n\n"
                f"Plan:\n{json.dumps(plan, indent=2)}{concept_hint}{weakness_hint}\n\n"
                "Create the flashcards."
            ),
        },
    ]
    result = await chat_json(messages, temperature=0.4, max_tokens=3000)
    _normalize_flashcard_ids(result)
    return result


# --- Normalization helpers ---


def _normalize_quiz_ids(result: dict[str, Any]) -> None:
    qs = result.get("questions") or []
    for i, q in enumerate(qs, start=1):
        if not q.get("id"):
            q["id"] = f"q{i}"
        else:
            q["id"] = str(q["id"])
        q["answer_idx"] = int(q.get("answer_idx", 0))
        q["options"] = list(q.get("options", []))
        # Ensure every question carries a concept tag for mastery tracking.
        q.setdefault("concept", "")
        if not isinstance(q["concept"], str):
            q["concept"] = str(q["concept"])


def _normalize_flashcard_ids(result: dict[str, Any]) -> None:
    cards = result.get("cards") or []
    for i, c in enumerate(cards, start=1):
        if not c.get("id"):
            c["id"] = f"c{i}"
        else:
            c["id"] = str(c["id"])
        # Ensure every card carries a concept tag for mastery tracking.
        c.setdefault("concept", "")
        if not isinstance(c["concept"], str):
            c["concept"] = str(c["concept"])


def new_content_id() -> str:
    return uuid.uuid4().hex[:12]
