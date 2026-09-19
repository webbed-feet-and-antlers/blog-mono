"""Architect node — SCQA/Minto blueprint, never prose.

Structural planning and prose generation live in separate model contexts
(combining them reinforces mode collapse — see the plan's research
grounding). Runs cool (T=0.2): structure wants precision, not variance.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..config import get_settings
from ..llm import chat_json
from ..state import WritingState

logger = logging.getLogger(__name__)

SYSTEM = """You are a technical-writing architect. You output ONLY structural
blueprints as JSON — never prose, never full sentences outside JSON strings.

Rules:
- Open with SCQA: situation (undisputed baseline), complication (what broke
  or got expensive), question (the dilemma), answer (the thesis, upfront).
- Body = 3-4 pillars that are MECE: mutually exclusive, collectively
  exhaustive. Each pillar carries its own evidence list (numbers, commands,
  incidents) and a stylistic_mode.
- stylistic_mode rotates across pillars, chosen from: dense-analytical,
  conversational, anecdote-led, counter-argument. Adjacent pillars must NOT
  share a mode.
- open_loops: unresolved trade-offs to leave unresolved in the post. Do not
  manufacture a tidy resolution.
- Timeline may jump (what happened last quarter explains today's choice) —
  do not force chronological linearity.
- Timeline must jump: at least one pillar opens in a different time period
  than the ones around it (the decision six months earlier, the consequence
  three weeks later, the 2019 commit that explains today). StoryScope:
  machine timelines are chronologically linear, human ones are not.
- Vary pillar scale: at least one pillar much shorter than the others —
  uniform section lengths read as machine structure.
- style_notes: if reference posts are provided, distill 3-6 concrete
  rhetorical mechanics from them (e.g. "one-sentence paragraphs after code
  blocks"), NOT vague voice adjectives.

Exact JSON shape — use these keys verbatim:
{"title": "...", "style_notes": ["..."],
 "scqa": {"situation": "...", "complication": "...", "question": "...", "answer": "..."},
 "pillars": [{"heading": "...", "claim": "...", "evidence": ["..."],
              "stylistic_mode": "dense-analytical|conversational|anecdote-led|counter-argument"}],
 "open_loops": ["..."]}"""

MODES = ["dense-analytical", "conversational", "anecdote-led", "counter-argument"]

# Models routinely drift on key names (observed: 'name' for 'heading').
# Normalize aliases instead of throwing away a 50s call over a key rename.
_SCQA_ALIASES = {
    "situation": ("situation", "context", "background"),
    "complication": ("complication", "problem", "conflict"),
    "question": ("question", "dilemma", "q"),
    "answer": ("answer", "thesis", "recommendation", "a"),
}
_PILLAR_ALIASES = {
    "heading": ("heading", "name", "title", "section"),
    "claim": ("claim", "thesis", "point", "argument", "summary"),
}


def _apply_aliases(obj: dict, aliases: dict[str, tuple[str, ...]]) -> dict:
    out = dict(obj)
    for canonical, options in aliases.items():
        if not out.get(canonical):
            for alias in options:
                if out.get(alias):
                    out[canonical] = out[alias]
                    break
    return out


def normalize_outline(outline: dict) -> dict:
    """Copy outline with scqa/pillar key aliases canonicalized. A pillar
    with a heading but no claim is workable — the heading stands in."""
    out = dict(outline)
    if isinstance(out.get("scqa"), dict):
        out["scqa"] = _apply_aliases(out["scqa"], _SCQA_ALIASES)
    pillars = out.get("pillars")
    if isinstance(pillars, list):
        normalized = []
        for p in pillars:
            if not isinstance(p, dict):
                normalized.append(p)
                continue
            np = _apply_aliases(p, _PILLAR_ALIASES)
            if not np.get("claim") and np.get("heading"):
                np["claim"] = np["heading"]
            normalized.append(np)
        out["pillars"] = normalized
    return out


def validate_outline(outline: dict[str, Any]) -> None:
    if not isinstance(outline.get("scqa"), dict) or not all(
        k in outline["scqa"]
        for k in ("situation", "complication", "question", "answer")
    ):
        raise ValueError(
            f"architect blueprint missing scqa keys: {outline.get('scqa')!r}"
        )
    pillars = outline.get("pillars")
    if not isinstance(pillars, list) or not 2 <= len(pillars) <= 5:
        raise ValueError(f"architect blueprint needs 2-5 pillars, got {pillars!r}")
    for p in pillars:
        if not p.get("heading") or not p.get("claim"):
            raise ValueError(f"pillar missing heading/claim: {p!r}")


def _load_references(paths: list[str], cap: int = 2, chars: int = 3000) -> str:
    chunks = []
    for raw in paths[:cap]:
        f = Path(raw)
        if f.exists():
            chunks.append(f.read_text()[:chars])
    return "\n\n---\n\n".join(chunks)


async def architect_node(state: WritingState, llm=chat_json) -> dict[str, Any]:
    refs = _load_references(state.get("style_reference_paths", []))
    persona = state.get("persona", "")
    prompt = (
        f"Topic: {state['topic']}\n"
        f"Audience: {state.get('audience', 'technical practitioners')}\n"
        f"Raw research:\n{state.get('raw_research', '')}\n\n"
        f"Persona summary (voice context only, do not write prose):\n{persona}\n\n"
    )
    if refs:
        prompt += f"Reference posts by the author:\n{refs}\n\n"
    prompt += (
        "Produce the blueprint JSON with keys: title, style_notes, scqa, "
        "pillars, open_loops."
    )
    outline = normalize_outline(
        await llm(
            [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=get_settings().sampling.architect_temp,
        )
    )
    try:
        validate_outline(outline)
    except ValueError as exc:
        # One repair pass beats discarding a ~50s generation over a schema
        # nit: show the model its invalid output plus the error.
        logger.warning("architect: blueprint invalid (%s) — requesting repair", exc)
        import json as _json

        outline = normalize_outline(
            await llm(
                [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": _json.dumps(outline)},
                    {
                        "role": "user",
                        "content": (
                            f"That blueprint was invalid: {exc}\n"
                            "Return the corrected JSON with the exact keys "
                            "specified in the system message. JSON only."
                        ),
                    },
                ],
                temperature=get_settings().sampling.architect_temp,
            )
        )
        validate_outline(outline)
    logger.info(
        "architect: blueprint ok — %d pillars, %d style_notes, %d open_loops",
        len(outline["pillars"]),
        len(outline.get("style_notes", [])),
        len(outline.get("open_loops", [])),
    )
    return {"outline": outline}
