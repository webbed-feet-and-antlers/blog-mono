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


# Deliberately AI-shaped control texts for the narrative-space graph and
# rarity plot — each a different corporate register, same machine shape:
# linear, tidy, over-explaining, repetitive lexicon. Multiple controls give
# the "AI" group a distribution, the way StoryScope plots five model
# authors against human writers.
AI_CONTROLS = [
    """## Why our platform wins

Our platform delivers value across the organization through seamless
integration. The platform provides a robust foundation for growth. Teams
can leverage the platform to unlock new potential. The platform ensures
seamless integration for every user across the industry. Furthermore, the
roadmap continues to build on the platform foundation each quarter. The
takeaway is that the platform is the key to long-term platform value.
""",
    """## Unlocking data-driven transformation

In today's fast-paced digital landscape, organizations must navigate the
complexities of digital transformation. A robust data strategy is crucial
to note for modern enterprises seeking to unlock insights. Moreover,
leveraging cutting-edge analytics empowers teams to delve into their data
tapestry. In conclusion, a holistic data strategy is a testament to
organizational readiness.
""",
    """## The future of intelligent security

Security in the modern era demands a comprehensive approach. Our solution
delivers seamless protection across the entire threat landscape. The
solution provides robust controls, and the solution ensures compliance at
every layer. Additionally, the solution empowers security teams to stay
ahead of threats. The key takeaway is that intelligent security requires
an intelligent solution built for intelligent enterprises.
""",
    """## Scaling engineering teams effectively

Engineering leadership requires intentional investment in culture. Our
framework provides the foundation for sustainable engineering growth.
Teams that leverage the framework deliver value more consistently, and the
framework ensures alignment across the organization. In short, the
framework is the key to scaling engineering teams the right way.
""",
]

# Back-compat alias (single control used by earlier versions).
AI_CONTROL_TEXT = AI_CONTROLS[0]


def trigram_novelty(target_blocks: list[str], corpus_blocks: list[str]) -> float:
    """Genie-style novelty: the share of the target's distinct prose 3-grams
    absent from the reference corpus (your posts + AI controls + other
    drafts). Human posts keep saying things nobody in the corpus said;
    aligned prose recombines known phrasing. 1.0 = fully novel."""
    def grams(blocks: list[str]) -> set[str]:
        text = " ".join(
            b for b in blocks if not is_code_block(b) and not is_heading_block(b)
        )
        words = re.findall(r"[a-z0-9']+", text.lower())
        return {" ".join(words[i : i + 3]) for i in range(len(words) - 2)}

    tgt = grams(target_blocks)
    if not tgt:
        return 1.0
    corpus = grams(corpus_blocks)
    return len(tgt - corpus) / len(tgt)


def human_score(axes: dict[str, float]) -> float:
    """Composite human-shape score: mean of the normalized axes (1 = fully
    human-shaped). The rarity plot ranks documents on this."""
    vals = list(axes.values())
    return sum(vals) / len(vals) if vals else 0.0


def rarity_percentiles(scores: list[float]) -> list[float]:
    """Rank-based percentile of each score within the pooled list (StoryScope
    Figure 5's 'rarity percentile vs. train+val', with the pooled corpus
    standing in for the reference population). 1.0 = more human-shaped than
    everything else in the pool. Ties share the lower rank."""
    n = len(scores)
    if n < 2:
        return [1.0] * n
    ranked = sorted(scores)
    return [ranked.index(s) / (n - 1) for s in scores]


def authorship_distance(
    current: dict[str, float],
    humans: list[dict[str, float]],
    controls: list[dict[str, float]],
) -> dict[str, Any] | None:
    """Nearest-centroid authorship check over the shape-axis space: how much
    closer does this document sit to YOUR writing than to machine-shaped
    text? ratio < 1 means human-side. None when there are no human
    references (honest refusal rather than a one-sided guess)."""
    if not humans or not controls or not current:
        return None
    names = list(current.keys())

    def centroid(vectors: list[dict[str, float]]) -> dict[str, float]:
        return {n: sum(v[n] for v in vectors) / len(vectors) for n in names}

    def dist(a: dict[str, float], b: dict[str, float]) -> float:
        return sum((a[n] - b[n]) ** 2 for n in names) ** 0.5

    human_centroid = centroid(humans)
    ai_centroid = centroid(controls)
    human_dist = dist(current, human_centroid)
    ai_dist = dist(current, ai_centroid)
    ratio = human_dist / ai_dist if ai_dist else float("inf")
    return {
        "human_dist": round(human_dist, 3),
        "ai_dist": round(ai_dist, 3),
        "ratio": round(ratio, 3),
        "verdict": "human-side" if ratio < 1.0 else "machine-side",
    }

# Radar axes: 1.0 = human-shaped. Caps are saturation points, not gates —
# they just keep the polygon inside the chart. Calibrate against your own
# posts and adjust in config if the shape looks wrong.
_AXIS_CAPS = {
    "temporal": 2.0,      # temporal+retro markers per 1k words
    "unresolved": 3.0,    # marker count
    "incidents": 3.0,     # first-person incident anchors per 1k
    "reader": 5.0,        # second-person address per 1k
    "fragments": 3.0,     # sentence fragments per 1k
    "sections": 40.0,     # section-length SD in words
    "lexicon": 0.30,      # top-5 content share (inverted)
    "summaries": 3,       # summary-marker blocks (inverted)
}


def shape_axes(metrics: dict[str, Any]) -> dict[str, float]:
    """Normalized 0-1 human-shape axes for the radar/PCA graph."""

    def sat(v: float, cap: float) -> float:
        return max(0.0, min(1.0, v / cap))

    return {
        "Temporal texture": sat(
            metrics["temporal_jumps_per_1k"] + metrics["retro_explanations_per_1k"],
            _AXIS_CAPS["temporal"],
        ),
        "Unresolved ends": sat(metrics["unresolved_markers"], _AXIS_CAPS["unresolved"]),
        "Incident anchors": sat(
            metrics["incident_anchors_per_1k"], _AXIS_CAPS["incidents"]
        ),
        "Reader address": sat(
            metrics["reader_address_per_1k"], _AXIS_CAPS["reader"]
        ),
        "Fragments": sat(metrics["fragments_per_1k"], _AXIS_CAPS["fragments"]),
        "Section variance": sat(
            metrics["section_length_sd"], _AXIS_CAPS["sections"]
        ),
        "Lexical diversity": 1.0
        - sat(metrics["top5_content_share"], _AXIS_CAPS["lexicon"]),
        "Summary-free": 1.0
        - sat(len(metrics["summary_hit_blocks"]), _AXIS_CAPS["summaries"]),
    }


def project_narrative_space(vectors: list[dict[str, float]]) -> list[tuple[float, float]]:
    """PCA (2 components) over standardized shape-axis vectors — the
    StoryScope-style narrative-space scatter. Returns (x, y) per document."""
    import numpy as np

    if not vectors:
        return []
    names = list(vectors[0].keys())
    data = np.array([[v[n] for n in names] for v in vectors], dtype=float)
    if len(vectors) < 3:
        # Too few docs for meaningful principal components — spread on the
        # first two raw axes instead so the chart still renders.
        return [(float(row[0]), float(row[1])) for row in data]
    std = data.std(axis=0)
    std[std == 0] = 1.0
    standardized = (data - data.mean(axis=0)) / std
    cov = np.cov(standardized, rowvar=False)
    eigenvalues, eigenvectors = np.linalg.eigh(cov)
    top = eigenvectors[:, np.argsort(eigenvalues)[::-1][:2]]
    projected = standardized @ top
    return [(float(x), float(y)) for x, y in projected]


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
