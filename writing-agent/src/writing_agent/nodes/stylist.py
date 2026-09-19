"""Stylist node — voice execution under anti-slop constraints.

Drafts HOT (T=0.9, top_p=0.92). Variance is forced structurally (verbalized
sampling, sentence-length spread, deep-syntax requirements, mode rotation)
rather than by temperature alone — the research's novelty/pragmaticality
paradox says raw temperature just breaks sense.
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import get_settings
from ..lint.banned_tokens import load_patterns
from ..llm import chat
from ..state import WritingState

logger = logging.getLogger(__name__)

MODES = ["dense-analytical", "conversational", "anecdote-led", "counter-argument"]

SYSTEM = """You are a stylist executing a structural blueprint. Follow the
constraints EXACTLY. Output ONLY markdown prose for the requested section —
no meta commentary, no explanations of your choices."""


def _pillar_prompt(
    pillar: dict[str, Any], mode: str, persona: str, style_notes: list[str], banned: str
) -> str:
    return f"""Write the pillar section "{pillar['heading']}".

Persona: {persona}
Style mechanics to mirror: {'; '.join(style_notes) or 'none provided'}
Structural content to cover — claim: {pillar['claim']}; evidence: {', '.join(pillar.get('evidence', []))}
Mode: {mode}

Constraints:
- First write 3 candidate opening sentences with genuinely different rhythms and angles. Choose the LEAST predictable one and continue with it. Do not show the other two.
- Vary sentence length hard: at least one sentence under 8 words and one over 25 words in this section.
- Include at least one parenthetical aside (a real digression, in parentheses).
- Include at least one sentence fragment (three words or fewer, no verb). Fragments are human.
- One rhetorical question is allowed if it earns its place.
- Ground claims in incidents, not categories: "when I ran X on the 50M-row table" beats "when working with large datasets".
- Use parentheses for asides, never em-dashes.
- Do NOT open any paragraph with Furthermore/Additionally/However/Moreover-style transitions. Open with concrete nouns, verbs, or numbers.
- Banned words (hard): {banned}
- End the section without a mini-summary. No "key takeaway", no "what this means", no restating the point — let it land.
- Start the output with '## {pillar['heading']}' followed by the prose."""


def _rotate_modes(pillars: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    modes: list[str] = []
    for p in pillars:
        m = p.get("stylistic_mode")
        if not m or m in seen:
            m = next(
                x for x in MODES if not seen or x != seen[-1]
            )
        seen.append(m)
        modes.append(m)
    return modes


async def stylist_node(state: WritingState, llm=chat) -> dict[str, Any]:
    outline = state["outline"]
    persona = state.get("persona", "")
    style_notes = outline.get("style_notes", [])
    banned = ", ".join(load_patterns()[:12])  # top of the list is enough signal
    sampling = get_settings().sampling

    async def draft(user_prompt: str) -> str:
        return await llm(
            [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=sampling.stylist_temp,
            top_p=sampling.stylist_top_p,
        )

    intro_prompt = (
        "Write the opening section (no heading; start directly with prose).\n"
        f"Persona: {persona}\n"
        f"Situation: {outline['scqa']['situation']}\n"
        f"Complication: {outline['scqa']['complication']}\n"
        f"Question: {outline['scqa']['question']}\n"
        f"Answer (deliver upfront, BLUF): {outline['scqa']['answer']}\n"
        f"Style mechanics: {'; '.join(style_notes) or 'none provided'}\n\n"
        "Constraints:\n"
        "- Same constraints as body sections: 3 candidate openers (keep the "
        "least predictable), hard sentence-length variance, one parenthetical "
        "aside, no paragraph-opening transitions, no em-dashes, no closing "
        "summary.\n"
        f"- Banned words (hard): {banned}"
    )
    logger.info("stylist: drafting intro (1 LLM call)")
    blocks = [await draft(intro_prompt)]

    pillars = outline["pillars"]
    modes = _rotate_modes(pillars)
    for i, (pillar, mode) in enumerate(zip(pillars, modes), start=1):
        logger.info(
            "stylist: drafting pillar %d/%d %r mode=%s",
            i,
            len(pillars),
            pillar.get("heading"),
            mode,
        )
        blocks.append(
            await draft(_pillar_prompt(pillar, mode, persona, style_notes, banned))
        )

    loops = outline.get("open_loops", [])
    outro_prompt = (
        "Write the closing section (no heading).\n"
        f"Persona: {persona}\n"
        f"Unresolved trade-offs to leave unresolved: {', '.join(loops) or 'state honestly what is still open'}\n\n"
        "Constraints:\n"
        "- NO tidy resolution, NO 'in conclusion', NO recap of the pillars.\n"
        "- Say what you would do differently and what you still do not "
        "know, in plain words ('I still don't know...', 'we never fixed...'). "
        "StoryScope: machine endings resolve internally; leave at least one "
        "trade-off genuinely open.\n"
        "- Short: 2-4 sentences, at least one under 8 words.\n"
        f"- Banned words (hard): {banned}"
    )
    logger.info("stylist: drafting outro")
    blocks.append(await draft(outro_prompt))

    return {"draft_blocks": blocks, "revision_count": 1}
