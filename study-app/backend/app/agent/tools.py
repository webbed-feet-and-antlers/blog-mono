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


def _score_trend(
    score_history: list[dict] | None, min_entries: int = 4, threshold: float = 0.1
) -> str | None:
    """Derive a calibration phrase from the stored quiz score history.

    Compares first-half vs second-half averages; ±10 points counts as a
    trend, anything quieter is noise and returns None. Deterministic —
    the trend signal score_history always implied but never surfaced.
    """
    if not isinstance(score_history, list):
        return None
    scores = [
        s.get("score")
        for s in score_history
        if isinstance(s, dict) and s.get("score") is not None
    ]
    if len(scores) < min_entries:
        return None
    mid = len(scores) // 2
    first = sum(scores[:mid]) / mid
    second = sum(scores[mid:]) / (len(scores) - mid)
    delta = second - first
    first_pct, second_pct = round(first * 100), round(second * 100)
    if delta >= threshold:
        return f"scores trending up ({first_pct}%→{second_pct}%) — can push difficulty"
    if delta <= -threshold:
        return f"scores trending down ({first_pct}%→{second_pct}%) — consolidate before advancing"
    return None


def _memory_hint(memory: dict[str, Any], task_type: str) -> str:
    """Surface relevant prior learnings (the shared-backbone payoff)."""
    hints: list[str] = []

    # --- Behavioral understanding (reflection + deterministic patterns) ---
    # The narrative layer reads FIRST — it frames everything below.
    insights = memory.get("learner_insights") or {}
    if insights.get("summary"):
        parts = [f"Learner insights (from observed behavior): {insights['summary']}"]
        traits = insights.get("traits") or []
        if traits:
            parts.append(f"Notable: {'; '.join(str(t) for t in traits[:5])}.")
        habits = insights.get("habits")
        if habits:
            parts.append(str(habits))
        hints.append(" ".join(parts))

    patterns = memory.get("study_patterns") or {}
    pattern_bits = []
    hour = patterns.get("best_study_hour_utc")
    if hour is not None:
        pattern_bits.append(f"most active around {int(hour):02d}:00 UTC")
    sessions = patterns.get("sessions") or {}
    completed = int(sessions.get("completed", 0))
    abandoned = int(sessions.get("abandoned", 0))
    if completed + abandoned >= 2:
        pattern_bits.append(f"{completed}/{completed + abandoned} study sessions completed")
    avg_quiz = patterns.get("avg_quiz_duration_secs")
    if avg_quiz:
        pattern_bits.append(f"avg quiz takes ~{int(avg_quiz)}s")
    fatigue = memory.get("fatigue")
    if fatigue == "fatigued":
        pattern_bits.append("currently deep into a long study session — favor shorter, gentler material")
    elif fatigue == "focused":
        pattern_bits.append("mid-session — keep material focused")
    if pattern_bits:
        hints.append("Study patterns: " + ", ".join(pattern_bits) + ".")

    # --- Learner profile (the highest-level personalization context) ---
    profile = memory.get("learner_profile") or {}
    if profile and profile.get("learner_level", "unknown") != "unknown":
        stats = profile.get("stats") or {}
        parts = [f"{profile['learner_level']} learner"]
        avg = stats.get("avg_score")
        if avg is not None:
            parts.append(f"avg {int(avg * 100)}%")
        trend = _score_trend(stats.get("score_history"))
        if trend:
            parts.append(trend)
        known = stats.get("flashcard_known_ratio")
        if known is not None and task_type in ("quiz", "flashcards"):
            known_str = f"knows ~{int(known * 100)}% of reviewed flashcards"
            if task_type == "flashcards" and known >= 0.8:
                known_str += " — favor application-style over definition cards"
            parts.append(known_str)
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
        # Build a lookup so we can show prerequisite mastery inline.
        mastery_by_name = {m["concept"]: m for m in mastery}
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
            # Behavioral difficulty: slow average answers signal hard recall
            # even when the tally looks fine.
            latency = m.get("latency") or {}
            slow_marker = ""
            avg_secs = latency.get("avg_secs")
            if avg_secs and float(avg_secs) >= 12.0:
                slow_marker = f" · slow recall (~{int(float(avg_secs))}s avg)"
            line = f"  - {m['concept']}: {seen_str} [{label}]{due_marker}{slow_marker}"

            # Knowledge graph: show prerequisite mastery so the agent can
            # sequence material along the dependency chain.
            prereqs = m.get("prerequisites") or []
            if prereqs:
                prereq_parts = []
                for prereq in prereqs[:4]:
                    pdata = mastery_by_name.get(prereq, {})
                    ppct = pdata.get("mastery_pct")
                    if ppct is None:
                        prereq_parts.append(f"{prereq} (untested)")
                    elif ppct < 0.4:
                        prereq_parts.append(f"{prereq} ({int(ppct*100)}% — very weak)")
                    elif ppct < 0.7:
                        prereq_parts.append(f"{prereq} ({int(ppct*100)}% — weak)")
                    else:
                        prereq_parts.append(f"{prereq} ({int(ppct*100)}% — strong)")
                line += f"\n    ↑ requires: {', '.join(prereq_parts)}"
            lines.append(line)

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
        instruction += (
            ". For concepts with prerequisites (↑ requires), test the "
            "prerequisite first if it's weak/untested — the learner needs "
            "the foundation before the advanced concept"
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
    """Extract topic, concepts, structure, difficulty, and concept relationships
    from a document. Cached per-document in agent memory so it only runs once.
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
                "sections (array of {title, summary} covering the document's structure), "
                "concept_relationships (array of {source, target, type} where source "
                "and target are concept names from the concepts array, and type is "
                "'prerequisite' (source requires understanding target first), "
                "'related' (source and target are connected but neither requires the other), "
                "or 'part_of' (source is a subtopic of target)). "
                "Identify prerequisite relationships carefully — they are the most "
                "important for sequencing study material."
            ),
        },
        {
            "role": "user",
            "content": f"Analyze this document:\n\n{_truncate(document_text)}",
        },
    ]
    return await chat_json(messages, temperature=0.1, max_tokens=2500)


# --- Filename auto-naming ---------------------------------------------------


def filename_needs_rename(filename: str) -> bool:
    """Heuristic gate: does this filename look like machine-generated noise?

    Catches the common cases: random hex/uuid prefixes from LMS downloads,
    camera/recorder defaults (IMG_1234, recording-<ts>), generic names
    (document, untitled, scan), and pure numbers. Cheap — no LLM call needed
    for names that pass.
    """
    import re

    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    stem = stem.strip()
    lower = stem.lower()

    if not lower:
        return True
    bad_patterns = [
        r"[0-9a-f]{10,}",                                # hex/uuid chunks
        r"^\d+$",                                        # pure numbers
        r"^(img|dsc|vid|pict?|screen[\s_-]?shot)[\s_-]?\d*$",  # camera/screen
        r"^(untitled|document|doc|file|scan|new|download|upload)[\s_-]?\d*$",
        r"^(recording|audio|voice[\s_-]?memo|memo|lecture|notes?|slides?|pages?|"
        r"week|session)[\s_-]?\d*$",                     # generic study names
        r"^\d{4}[-_]\d{2}[-_]\d{2}",                     # date-prefixed
        r"copy[\s_-]?\d*$",                              # duplicate suffixes
    ]
    return any(re.search(p, lower) for p in bad_patterns)


async def suggest_filename(current_filename: str, text: str) -> str | None:
    """Ask the LLM for a clean, descriptive name for a document.

    Returns the new stem (no extension), or None if the current name should
    be kept. Only called when the heuristic flags the filename as noise —
    good names never cost an LLM call.
    """
    excerpt = text[:2000]
    messages = [
        {
            "role": "system",
            "content": (
                "You rename study documents. Given the current filename and an "
                "excerpt of the document's content, return ONLY JSON: "
                '{"new_name": string | null}. '
                "new_name is a concise descriptive title (3-8 words, Title Case, "
                "no file extension, no quotes) that identifies BOTH what the "
                "document covers AND what kind of material it is — the topic "
                "plus the material type (lecture, lecture notes, chapter "
                "summary, problem set, lab report, seminar slides, textbook "
                "chapter, review sheet, …) as evident from the content — "
                "e.g. 'Photosynthesis Lecture Notes', 'Cell Biology Chapter "
                "4 Summary', 'MIT Science Writing Seminar 20 Slides'. "
                "A topic alone is not enough: 'Glycolysis' is a bad name, "
                "'Glycolysis Lecture Notes' is a good one — and when the "
                "content supports it, name the distinguishing subtopic "
                "too ('Ecology — Nutrient Cycles Lecture Notes' beats "
                "'Ecology Lecture Notes' in a folder of ecology files). "
                "Return null if the current filename (ignoring its extension) "
                "is already clear and descriptive."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Current filename: {current_filename}\n\n"
                f"Document excerpt:\n{excerpt}"
            ),
        },
    ]
    result = await chat_json(messages, temperature=0.2, max_tokens=200)
    name = (result.get("new_name") or "").strip()
    if not name or len(name) > 80:
        return None
    # Strip a file extension if the model added one anyway.
    if "." in name:
        name = name.rsplit(".", 1)[0].strip()
    return name or None


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
                "of forgetting them.\n\n"
                "CONCEPT GRAPH: concept_mastery entries may show prerequisites (↑ requires). "
                "Sequence your material along the prerequisite chain: test/cover foundational "
                "concepts before advanced ones that depend on them. If a prerequisite has low "
                "mastery, include it in the plan — the learner needs the foundation first."
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
                "Ground the notes in THIS document: explicitly cover the "
                "work's own aim, methods, and key findings — the specific "
                "study, mechanism, or argument the source advances — not "
                "just background on the field. "
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
                "the answer is correct. Distractors are the hard part — "
                "build each one from the SOURCE MATERIAL itself: a related "
                "concept from the passage, a near-miss value (a number off "
                "by a factor or unit, a swapped label, an adjacent step in "
                "a process), or a common misconception a learner who "
                "half-knows the concept would hold. Every distractor must "
                "be same-topic, same-register, and genuinely tempting — "
                "absurd, jokey, or trivially eliminable options are "
                "failures. Do NOT reuse another question's correct answer "
                "as a distractor. "
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
                "IMPORTANT: For each concept, generate 2 card variants with "
                "DIFFERENT phrasings — test the same concept from different "
                "angles so the student can't memorize the card wording instead "
                "of learning the underlying concept. Store variants as an array. "
                "Return ONLY JSON: "
                '{"title": string, "cards": ['
                '{"id": "c1", "concept": string, '
                '"variants": [{"front": string, "back": string}, '
                '{"front": string, "back": string}]}]}.'
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

        # Handle card variants (new format) vs flat front/back (old format).
        if "variants" not in c:
            # Old format: flat front/back → wrap in a single variant.
            c["variants"] = [{"front": c.get("front", ""), "back": c.get("back", "")}]
        else:
            # Ensure variants is a list of {front, back} dicts.
            if not isinstance(c["variants"], list):
                c["variants"] = [{"front": c.get("front", ""), "back": c.get("back", "")}]
            else:
                for v in c["variants"]:
                    if not isinstance(v, dict):
                        continue
                    v.setdefault("front", "")
                    v.setdefault("back", "")

        # Also keep flat front/back (first variant) for backward compat.
        if c["variants"]:
            c["front"] = c["variants"][0].get("front", "")
            c["back"] = c["variants"][0].get("back", "")


def new_content_id() -> str:
    return uuid.uuid4().hex[:12]
