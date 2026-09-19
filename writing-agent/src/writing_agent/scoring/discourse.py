"""Discourse-shape analyzer — StoryScope axes ported to technical prose.

StoryScope (arXiv:2604.03136) showed AI text is recognizable at the
discourse level, not just the stylistic one: over-explained themes (explicit
commentary 77% vs 52% in human writing), tidy single-track causal plots,
chronologically linear timelines, internally-resolved endings, and AI work
clustering in a narrow region of narrative space. This module measures the
prose analogues:

  deterministic (always available)          StoryScope dimension
  - summary/lesson markers                  over-explained themes (Style)
  - temporal jumps + retro-explanations     Time: chronological discontinuity
  - unresolved trade-off markers            Plot: ambiguous/unresolved endings
  - concrete first-person incident anchors  Agent: introduced in-action
  - concessions / messy causality          Event: disjointed causal chains
  - direct reader address                   Perspective: fourth-wall breaks
  - top-5 content-word concentration        narrative-space clustering
  - section-length variance                 Structure: streamlined vs varied

  LLM-judged (graceful skip): over_explains, linear_timeline,
  tidy_resolution, generic_abstraction, reads_like_ai.

Thresholds are starting points — calibrate against your own posts.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any

from ..config import ThresholdSettings
from ..llm import chat_json
from ..segment import is_code_block, is_heading_block, split_sentences, word_count

logger = logging.getLogger(__name__)

_TEMPORAL = re.compile(
    r"\b(?:six months|a year|years?|weeks?|months?|days?) "
    r"(?:earlier|before|ago|later)\b"
    r"|\bback in \d{4}\b|\bbefore that\b|\bat the time\b|\boriginally\b"
    r"|\bpreviously\b|\bwhen we (?:started|began|launched)\b",
    re.IGNORECASE,
)
_RETRO = re.compile(
    r"\b(?:only later|turned out|which explains|that'?s why|that is why"
    r"|in hindsight|little did)\b",
    re.IGNORECASE,
)
_CONCESSION = re.compile(
    r"\b(?:didn'?t help|did not help|maybe not|or maybe|for what it'?s worth"
    r"|not that it matters|though,? maybe|at least that)"
    r"|\bthough\b",
    re.IGNORECASE,
)
_UNRESOLVED = re.compile(
    r"\b(?:still (?:don'?t|do not) know|never (?:fixed|got|solved|shipped)"
    r"|open question|trade-?offs?|unresolved|not sure why|your mileage)\b",
    re.IGNORECASE,
)
_SUMMARY = re.compile(
    r"\b(?:the takeaway|the lesson|reali[sz]ed that|what this means"
    r"|in short|the key (?:is|takeaway)|to summari[sz]e|the point (?:is|being)"
    r"|moral of)\b",
    re.IGNORECASE,
)
_INCIDENT = re.compile(
    r"\b(?:I|we) (?:ran|shipped|tried|lost|built|broke|wrote|spent|deleted"
    r"|migrated|debugged|measured|deleted|read|asked|guessed)\b",
    re.IGNORECASE,
)
_READER = re.compile(r"\byou(?:r|rs)?\b", re.IGNORECASE)
_NUMBER = re.compile(r"\b\d[\d.,]*\b")

_STOPWORDS = {
    "the", "and", "that", "this", "with", "have", "from", "your", "they",
    "will", "would", "there", "their", "what", "about", "which", "when",
    "make", "like", "just", "into", "than", "then", "them", "these", "some",
    "more", "very", "also", "because", "been", "were", "does", "those",
    "your", "yours", "it's", "don't", "should", "could", "after", "before",
    "while", "where", "here", "over", "under", "again", "each", "other",
    "most", "only", "such", "same", "both", "just", "even", "much", "many",
}


def _prose_indexes(blocks: list[str]) -> list[int]:
    return [
        i
        for i, b in enumerate(blocks)
        if not is_code_block(b) and not is_heading_block(b)
    ]


def shape_metrics(blocks: list[str]) -> dict[str, Any]:
    """Deterministic StoryScope-style shape metrics over a block list."""
    prose = [blocks[i] for i in _prose_indexes(blocks)]
    text = "\n\n".join(prose)
    words = word_count(text)

    def per_1k(n: int) -> float:
        return n / words * 1000 if words else 0.0

    summary_hits: dict[int, list[str]] = {}
    for i in _prose_indexes(blocks):
        found = sorted({m.lower() for m in _SUMMARY.findall(blocks[i])})
        if found:
            summary_hits[i] = found

    fragments = 0
    questions = 0
    for block in prose:
        for sent in split_sentences(block):
            if 0 < len(sent.split()) <= 3:
                fragments += 1
            if sent.endswith("?"):
                questions += 1

    content = [
        w for w in re.findall(r"[a-z']+", text.lower())
        if len(w) > 3 and w not in _STOPWORDS
    ]
    top5_share = 0.0
    if content:
        counts = Counter(content)
        top5 = sum(c for _, c in counts.most_common(5))
        top5_share = top5 / len(content)

    section_lengths = [word_count(b) for b in prose]
    mean_len = sum(section_lengths) / len(section_lengths) if section_lengths else 0.0
    section_len_sd = (
        (sum((x - mean_len) ** 2 for x in section_lengths) / len(section_lengths)) ** 0.5
        if section_lengths
        else 0.0
    )

    return {
        "words": words,
        "temporal_jumps_per_1k": per_1k(len(_TEMPORAL.findall(text))),
        "retro_explanations_per_1k": per_1k(len(_RETRO.findall(text))),
        "concessions_per_1k": per_1k(len(_CONCESSION.findall(text))),
        "unresolved_markers": len(_UNRESOLVED.findall(text)),
        "incident_anchors_per_1k": per_1k(len(_INCIDENT.findall(text))),
        "reader_address_per_1k": per_1k(len(_READER.findall(text))),
        "numbers_per_1k": per_1k(len(_NUMBER.findall(text))),
        "fragments_per_1k": per_1k(fragments),
        "rhetorical_questions": questions,
        "summary_hit_blocks": summary_hits,
        "top5_content_share": top5_share,
        "section_count": len(prose),
        "section_length_sd": section_len_sd,
    }


JUDGE_SYSTEM = """You are a discourse analyst trained on StoryScope findings
(arXiv:2604.03136): machine-written text over-explains its themes, keeps
tidy single-track causal structure, resolves everything internally, stays
chronologically linear, and avoids concrete idiosyncratic incident. You
judge TECHNICAL BLOG PROSE on those axes. Score each axis 0.0 (fully human)
to 1.0 (fully machine). Be stingy above 0.7 — that is the flag threshold.
Respond ONLY with JSON:
{"over_explains": 0.0, "linear_timeline": 0.0, "tidy_resolution": 0.0,
 "generic_abstraction": 0.0, "reads_like_ai": 0.0, "notes": "..."}"""


async def judge_axes(text: str, llm=chat_json) -> dict[str, Any]:
    result = await llm(
        [
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": text[:8000]},
        ],
        temperature=0.1,
    )
    axes = {
        k: float(result.get(k, 0.0))
        for k in (
            "over_explains",
            "linear_timeline",
            "tidy_resolution",
            "generic_abstraction",
            "reads_like_ai",
        )
    }
    axes["notes"] = str(result.get("notes", ""))[:300]
    return axes


async def analyze_discourse(
    blocks: list[str],
    t: ThresholdSettings,
    *,
    judge=judge_axes,
) -> dict[str, Any]:
    """Full shape report: deterministic metrics + judge, with failures
    routed to actionable blocks (first prose block for doc-level issues,
    last prose block for resolution issues)."""
    metrics = shape_metrics(blocks)
    failures: list[dict[str, Any]] = []
    prose = _prose_indexes(blocks)

    for block_index, markers in metrics["summary_hit_blocks"].items():
        failures.append(
            {
                "block_index": block_index,
                "reason": (
                    f"over-explains (StoryScope): summary/lesson marker"
                    f" {', '.join(markers)} — cut it, let the point land"
                    f" without commentary"
                ),
            }
        )

    temporal = (
        metrics["temporal_jumps_per_1k"] + metrics["retro_explanations_per_1k"]
    )
    if metrics["words"] >= 400:
        if prose and temporal < t.temporal_markers_min_per_1k:
            failures.append(
                {
                    "block_index": prose[0],
                    "reason": (
                        f"linear timeline ({temporal:.1f} temporal/retro "
                        f"markers per 1k words, min {t.temporal_markers_min_per_1k}) "
                        f"— StoryScope: human narrative time is discontinuous; "
                        f"open with a jump or a retro-explanation"
                    ),
                }
            )
        if prose and metrics["unresolved_markers"] < t.unresolved_min:
            failures.append(
                {
                    "block_index": prose[-1],
                    "reason": (
                        "tidy resolution — nothing is left unresolved; "
                        "StoryScope: machine endings resolve internally, "
                        "humans leave trade-offs open"
                    ),
                }
            )
    if metrics["top5_content_share"] > t.top5_share_max:
        failures.append(
            {
                "block_index": prose[0] if prose else None,
                "reason": (
                    f"lexical concentration: top-5 content words are "
                    f"{metrics['top5_content_share']:.0%} of content "
                    f"(max {t.top5_share_max:.0%}) — repetitive framing "
                    f"vocabulary, the textual fingerprint of AI clustering"
                ),
            }
        )

    judge_out: dict[str, Any] | None = None
    try:
        judge_out = await judge("\n\n".join(b for i, b in enumerate(blocks) if i in set(prose)))
        for axis, value in judge_out.items():
            if axis == "notes":
                continue
            if value >= t.judge_flag_at:
                failures.append(
                    {
                        "block_index": prose[0] if prose else None,
                        "reason": (
                            f"judge: {axis} = {value:.2f} — "
                            f"{judge_out.get('notes', '')[:120]}"
                        ),
                    }
                )
    except Exception as exc:
        logger.warning("discourse judge skipped: %r", exc)
        judge_out = None

    flagged = sorted({f["block_index"] for f in failures if f["block_index"] is not None})
    return {
        "metrics": metrics,
        "judge": judge_out,
        "failures": failures,
        "flagged_blocks": flagged,
        "passed": not failures,
    }
