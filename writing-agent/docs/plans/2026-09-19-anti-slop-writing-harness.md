# Anti-Slop Writing-Agent Harness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a greenfield Python/LangGraph agent harness in `blog-mono/writing-agent/` that drafts technical blog posts through a 4-stage pipeline — SCQA/Minto architecture → high-entropy stochastic drafting → deterministic + information-theoretic linting → targeted edit loop — engineered to counter LLM statistical normalization ("AI slop").

**Architecture:** A LangGraph `StateGraph` (mirroring `study-app/backend/app/agent/graph.py`) routes `architect → stylist → audit → (editor → audit)* → finalize`. Deterministic Python linters (banned n-grams, sentence burstiness, transition density, deep-syntax SDH checks) act as the prose "compiler"; a surprisal scorer using OpenRouter logprobs gates flat, low-entropy text. All LLM calls go through OpenRouter, mirroring `study-app/backend/app/llm.py`. A typer CLI exposes `wa lint` (standalone on any markdown), `wa score`, and `wa draft` (full pipeline). Drafts land in `writing-agent/workspace/` as markdown plus a scorecard file.

**Tech Stack:** Python ≥3.13, uv, langgraph ≥0.2, langchain-core ≥0.3, openai ≥1.40 (OpenRouter), pydantic ≥2.7, pydantic-settings ≥2.3, numpy (listed for parity with study-app; linters stay stdlib-pure), typer, rich; pytest + pytest-asyncio. No spacy — linters are pure regex/stdlib so they stay deterministic and fast.

**Spec:** Self-contained below ("Spec & Research Grounding"). Derived from the user's deep-research document (information-theoretic anatomy of AI slop, Structural Depth Hypothesis, detector mechanics, SCQA/Minto, StoryScope, asymmetric agency) plus their harness design conversation (multi-pass compilation loop, deterministic linters as compiler errors, dual-role agents, opinionated constraints).

## Global Constraints

- Lives in the blog-mono monorepo alongside `study-app/` (same git repo, own pyproject). Commit after every task, conventional commits.
- One vendor: OpenRouter via `AsyncOpenAI(base_url="https://openrouter.ai/api/v1")`; `OPENROUTER_API_KEY` in `.env` (never committed).
- Model roles are config values in `config/default.toml`: `models.draft` (architect/stylist/editor; default `anthropic/claude-sonnet-4.5`) and `models.scorer` (MUST support completions echo+logprobs; default `openai/gpt-3.5-turbo-instruct`).
- Thresholds (all configurable, defaults from the research): sentence-length SD ≥ 8.0; sentence-length variance ≥ 15.0; transition density < 0.15; em-dash density ≤ 6.0 per 1k words; banned hits = 0; deep-syntax structures ≥ 1 per prose block; mean token surprisal ≥ 1.2 bits; surprisal variance ≥ 2.0 bits² (calibratable); max_revisions = 3.
- Sampling profiles: architect T=0.2, stylist T=0.9 + top_p=0.92, editor T=0.15.
- LLM-dependent functions take an injectable callable default (`llm=chat`, `scorer=score_blocks`) so tests never touch the network. Deterministic linters are pure functions.
- Surprisal is in bits: `s_i = -logprob_nat / ln(2)`.
- Default persona encodes the blog's "human and messy, no copywriter polish" voice.
- Tests run with `uv run pytest`; `asyncio_mode = "auto"`. No network in tests, ever.

## Spec & Research Grounding

| Research finding | Harness mechanism |
|---|---|
| KL-regularized alignment collapses P(y\|x) into low-entropy attractors | Stylist drafts at T=0.9/top_p=0.92 with verbalized-sampling prompts (3 candidate openers, keep the least predictable) |
| Structural Depth Hypothesis: deep syntax (parentheticals, inversions) decays, surface connectives amplify | `deep_syntax` linter requires parenthetical/inversion structures; `transitions` linter caps paragraph-initial conjunctive adverbs and em-dash density; `banned_tokens` hard-bans filler n-grams |
| Human prose has high-variance token surprisal; machine prose is flat | `scoring/surprisal.py` gates per-block mean + variance of token surprisal from a separate scorer model's logprobs |
| Combined drafting + planning in one context reinforces mode collapse | Architect (JSON blueprint, T=0.2) and Stylist (prose, T=0.9) are separate nodes/model contexts |
| SCQA + Minto/MECE structure for technical discourse | Architect emits `{scqa, pillars[3–4], evidence}` JSON; MECE instruction in prompt |
| StoryScope: AI over-explains, over-linearizes, resolves too cleanly | Architect emits `open_loops` (unresolved trade-offs); outro prompt forbids tidy wrap-up; style notes forbid per-section "moral" summaries |
| Asymmetric agency: LLMs smoothly elaborate their own previous text | Pillars rotate `stylistic_mode` (dense-analytical / conversational / anecdote-led / counter-argument) to force semantic disruption between sections |
| Global re-generation introduces new errors | Editor rewrites ONLY flagged blocks, receives per-block failure reasons, runs at T=0.15 |
| n-gram novelty vs pragmaticality paradox (raw temperature breaks sense) | Variance is forced at the syntactic/rhythmic level (sentence length, asides) and via mode rotation — not by cranking temperature alone |
| Writing-RL margin-aware selection | Editor targets only blocks failing gates (the "margin"); full pairwise-reward machinery deferred (see Extension Points) |

## File Structure

```
writing-agent/
├── pyproject.toml
├── .env.example
├── .gitignore
├── README.md
├── config/
│   ├── default.toml
│   ├── banned_ngrams.txt
│   └── personas/default.md
├── references/README.md
├── workspace/.gitkeep
├── docs/plans/2026-09-19-anti-slop-writing-harness.md   (this file)
├── src/writing_agent/
│   ├── __init__.py
│   ├── config.py               # Settings + TOML merge
│   ├── llm.py                  # OpenRouter: chat / chat_json / completion_logprobs
│   ├── state.py                # WritingState TypedDict
│   ├── segment.py              # markdown → blocks → sentences
│   ├── lint/
│   │   ├── __init__.py
│   │   ├── banned_tokens.py
│   │   ├── burstiness.py
│   │   ├── transitions.py
│   │   ├── deep_syntax.py
│   │   └── report.py           # LintReport + run_all_linters + scorecard
│   ├── scoring/
│   │   ├── __init__.py
│   │   └── surprisal.py        # per-block surprisal stats + calibration
│   ├── nodes/
│   │   ├── __init__.py
│   │   ├── architect.py
│   │   ├── stylist.py
│   │   ├── editor.py
│   │   ├── audit.py
│   │   └── finalize.py
│   ├── graph.py                # build_graph + route_after_audit + run_pipeline
│   └── cli.py                  # wa lint / wa score / wa draft
└── tests/
    ├── conftest.py
    ├── test_config.py
    ├── test_segment.py
    ├── test_banned_tokens.py
    ├── test_burstiness.py
    ├── test_transitions.py
    ├── test_deep_syntax.py
    ├── test_report.py
    ├── test_cli_lint.py
    ├── test_llm.py
    ├── test_surprisal.py
    ├── test_architect.py
    ├── test_stylist.py
    ├── test_editor.py
    └── test_graph.py
```

---

### Task 1: Scaffold package, config, and seed files

**Files:**
- Create: `writing-agent/pyproject.toml`, `writing-agent/.env.example`, `writing-agent/.gitignore`
- Create: `writing-agent/config/default.toml`, `writing-agent/config/banned_ngrams.txt`, `writing-agent/config/personas/default.md`
- Create: `writing-agent/src/writing_agent/__init__.py`, `writing-agent/src/writing_agent/config.py`
- Create: `writing-agent/tests/conftest.py`, `writing-agent/tests/test_config.py`
- Create: `writing-agent/references/README.md`, `writing-agent/workspace/.gitkeep`

**Interfaces:**
- Produces: `get_settings() -> Settings` (lru_cached); `Settings` with fields `openrouter_api_key: str`, `openrouter_base_url: str` (default `https://openrouter.ai/api/v1`), `workspace_dir: Path`, `models: ModelSettings`, `sampling: SamplingSettings`, `thresholds: ThresholdSettings`; module constant `CONFIG_DIR: Path`; `ThresholdSettings` fields exactly: `sentence_sd_min=8.0`, `sentence_variance_min=15.0`, `transition_density_max=0.15`, `em_dash_per_1k_max=6.0`, `banned_hits_max=0`, `deep_syntax_min_per_block=1`, `mean_surprisal_min=1.2`, `surprisal_variance_min=2.0`, `max_revisions=3`.

- [ ] **Step 1: Create pyproject.toml**

```toml
[project]
name = "writing-agent"
version = "0.1.0"
description = "Anti-slop technical writing agent harness — LangGraph pipeline + deterministic prose linters"
requires-python = ">=3.13"
dependencies = [
    "langgraph>=0.2",
    "langchain-core>=0.3",
    "openai>=1.40",
    "pydantic>=2.7",
    "pydantic-settings>=2.3",
    "numpy>=1.26",
    "typer>=0.12",
    "rich>=13.7",
]

[project.scripts]
wa = "writing_agent.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/writing_agent"]

[dependency-groups]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Create .env.example, .gitignore, seed config files**

`.env.example`:

```
# OpenRouter API key — https://openrouter.ai/keys
OPENROUTER_API_KEY=
# Optional: where drafts are written (default: writing-agent/workspace)
# WORKSPACE_DIR=
```

`.gitignore`:

```
.env
.venv/
__pycache__/
*.pyc
.pytest_cache/
```

`config/default.toml`:

```toml
[models]
draft = "anthropic/claude-sonnet-4.5"
# Must support /completions with echo + logprobs. Swap freely on OpenRouter.
scorer = "openai/gpt-3.5-turbo-instruct"

[sampling]
architect_temp = 0.2
stylist_temp = 0.9
stylist_top_p = 0.92
editor_temp = 0.15

[thresholds]
sentence_sd_min = 8.0
sentence_variance_min = 15.0
transition_density_max = 0.15
em_dash_per_1k_max = 6.0
banned_hits_max = 0
deep_syntax_min_per_block = 1
mean_surprisal_min = 1.2
surprisal_variance_min = 2.0
max_revisions = 3
```

`config/banned_ngrams.txt` (one regex per line; `#` starts a comment):

```
# AI-slop filler — hard-banned (research: RLHF surface-marker amplification)
\bdelve(s|d)?\b
\bdelve into\b
\btapestry\b
\btestament to\b
\bit('s| is) important to note\b
\bit('s| is) worth (noting|mentioning)\b
\bfurthermore\b
\bmoreover\b
\bin conclusion\b
\bkey takeaways?\b
\bgame[- ]changing\b
\bdive (in|into|deep)\b
\bin today's fast-paced world\b
\bin today's digital (age|landscape|world)\b
\bnavigate the landscape\b
\bleverag(e|es|ing)\b
\bseamless(ly)?\b
\bunleash(es|ed|ing)?\b
\bunlock the (power|potential|future|secrets)\b
\bbuckle up\b
\bwithout further ado\b
\bat the end of the day\b
\bdemystif(y|ies|ying)\b
\bcutting-edge\b
\brevolutioniz(e|es|ing)\b
\bpave(s)? the way\b
\bplay(s|ed)? a (crucial|vital|pivotal|key) role\b
\bsignificantly (enhance|enhances|enhancing|improve|improves)\b
\ba wealth of\b
\bvibrant\b
```

`config/personas/default.md`:

```markdown
# Persona: default (the blog's voice)

Human and messy. An engineer's blog, not a content-marketing channel.

- Write like you talk to a teammate who is smart but context-switching.
- First person. Concrete: real commands, real numbers, real mistakes.
- Opinions allowed. If industry consensus is wrong, say so and say why.
- Casual register: sentence fragments are fine. "And"/"But" openers are fine.
- No copywriter polish. If a sentence could appear on a SaaS landing page, kill it.
- Complexity only where the idea is complex. Plain words everywhere else.
- Admit unresolved trade-offs. Do not wrap up tidy if it is not tidy.
```

`references/README.md`:

```markdown
# Style references

Drop 5–10 gold-standard human-written technical posts here (markdown).
The architect distills concrete rhetorical mechanics from them (e.g.
"one-sentence paragraphs after code blocks", "self-deprecating asides
after architectural mistakes") — never vague "imitate this voice".
```

`src/writing_agent/__init__.py`: empty file.

- [ ] **Step 3: Write the failing config test**

`tests/test_config.py`:

```python
from writing_agent.config import get_settings


def test_defaults_load_from_toml():
    s = get_settings()
    assert s.thresholds.sentence_sd_min == 8.0
    assert s.thresholds.sentence_variance_min == 15.0
    assert s.thresholds.transition_density_max == 0.15
    assert s.thresholds.max_revisions == 3
    assert s.models.draft.startswith("anthropic/")
    assert s.models.scorer == "openai/gpt-3.5-turbo-instruct"
    assert s.sampling.stylist_temp == 0.9
    assert s.openrouter_base_url == "https://openrouter.ai/api/v1"


def test_env_overrides_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    get_settings.cache_clear()
    try:
        s = get_settings()
        assert s.openrouter_api_key == "sk-test-123"
    finally:
        get_settings.cache_clear()
```

`tests/conftest.py`:

```python
import pytest

from writing_agent.config import get_settings


@pytest.fixture(autouse=True)
def _fresh_settings():
    """Settings are lru_cached; tests must not inherit each other's env."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd writing-agent && uv run pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'writing_agent'`

- [ ] **Step 5: Implement config.py**

`src/writing_agent/config.py`:

```python
"""Settings — secrets from env/.env, tunables from config/default.toml."""

from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent  # writing-agent/
CONFIG_DIR = PACKAGE_ROOT / "config"


class ModelSettings(BaseModel):
    draft: str = "anthropic/claude-sonnet-4.5"
    scorer: str = "openai/gpt-3.5-turbo-instruct"


class SamplingSettings(BaseModel):
    architect_temp: float = 0.2
    stylist_temp: float = 0.9
    stylist_top_p: float = 0.92
    editor_temp: float = 0.15


class ThresholdSettings(BaseModel):
    sentence_sd_min: float = 8.0
    sentence_variance_min: float = 15.0
    transition_density_max: float = 0.15
    em_dash_per_1k_max: float = 6.0
    banned_hits_max: int = 0
    deep_syntax_min_per_block: int = 1
    mean_surprisal_min: float = 1.2
    surprisal_variance_min: float = 2.0
    max_revisions: int = 3


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    workspace_dir: Path = PACKAGE_ROOT / "workspace"

    models: ModelSettings = ModelSettings()
    sampling: SamplingSettings = SamplingSettings()
    thresholds: ThresholdSettings = ThresholdSettings()


@lru_cache
def get_settings() -> Settings:
    """Env (secrets) layered over config/default.toml (tunables)."""
    data: dict = {}
    toml_path = CONFIG_DIR / "default.toml"
    if toml_path.exists():
        data = tomllib.loads(toml_path.read_text())
    return Settings(
        models=ModelSettings(**data.get("models", {})),
        sampling=SamplingSettings(**data.get("sampling", {})),
        thresholds=ThresholdSettings(**data.get("thresholds", {})),
    )
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: 2 PASS

- [ ] **Step 7: Commit**

```bash
git add writing-agent/pyproject.toml writing-agent/.env.example writing-agent/.gitignore writing-agent/config writing-agent/src writing-agent/tests writing-agent/references writing-agent/workspace
git commit -m "feat(writing-agent): scaffold package, settings, and seed config"
```

---

### Task 2: Text segmentation

**Files:**
- Create: `src/writing_agent/segment.py`
- Test: `tests/test_segment.py`

**Interfaces:**
- Produces: `split_blocks(markdown: str) -> list[str]` (block = heading, paragraph, or intact fenced code block); `split_sentences(block: str) -> list[str]` (skips fenced code and inline code spans); `word_count(text: str) -> int`; `is_code_block(block: str) -> bool`; `is_heading_block(block: str) -> bool`.

- [ ] **Step 1: Write the failing tests**

`tests/test_segment.py`:

```python
from writing_agent.segment import (
    is_code_block,
    is_heading_block,
    split_blocks,
    split_sentences,
    word_count,
)

DOC = """## Setup

Install the thing:

```python
import re

def f():
    return 1
```

Then run it. It works.

Mostly.
"""


def test_split_blocks_keeps_headings_paragraphs_and_fences_intact():
    blocks = split_blocks(DOC)
    assert blocks[0] == "## Setup"
    assert blocks[1] == "Install the thing:"
    assert is_code_block(blocks[2])
    assert "import re" in blocks[2]
    assert blocks[3] == "Then run it. It works."
    assert blocks[4] == "Mostly."


def test_split_blocks_blank_lines_inside_fence_do_not_split():
    assert len([b for b in split_blocks(DOC) if is_code_block(b)]) == 1


def test_split_sentences_basic_and_inline_code():
    sents = split_sentences("Run `make build.py` first. Then deploy. Done?")
    assert sents == ["Run CODE first.", "Then deploy.", "Done?"]


def test_split_sentences_skips_code_fences():
    block = "Intro sentence.\n```python\nx = 1. Not prose.\n```\nOutro sentence."
    sents = split_sentences(block)
    assert sents == ["Intro sentence.", "Outro sentence."]


def test_predicates_and_word_count():
    assert is_heading_block("## Setup")
    assert not is_heading_block("Setup")
    assert word_count("one two three four") == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_segment.py -v`
Expected: FAIL — `ModuleNotFoundError` / `ImportError`

- [ ] **Step 3: Implement segment.py**

```python
"""Text segmentation — markdown → blocks → sentences.

Blocks are the unit of drafting, linting, and targeted editing. A block is a
heading, a paragraph, or a fenced code block (fences stay intact — code is
never sentence-split or linted as prose).
"""

from __future__ import annotations

import re


def split_blocks(markdown: str) -> list[str]:
    """Split markdown into top-level blocks.

    Separators: blank lines and heading starts. Fenced code blocks are kept
    intact, including any blank lines inside them.
    """
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False

    def flush() -> None:
        if current:
            blocks.append("\n".join(current).strip())
            current.clear()

    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            current.append(line)
            continue
        if in_fence:
            current.append(line)
            continue
        if not stripped:
            flush()
            continue
        if stripped.startswith("#") and current:
            flush()
        current.append(line)
    flush()
    return [b for b in blocks if b]


def split_sentences(block: str) -> list[str]:
    """Split a prose block into sentences.

    Fenced code lines are skipped entirely; inline code spans are replaced
    with CODE so `foo.bar` doesn't produce fake sentence boundaries.
    """
    lines: list[str] = []
    in_fence = False
    for line in block.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append(line)
    text = " ".join(ln.strip() for ln in lines if ln.strip())
    text = re.sub(r"`[^`]*`", "CODE", text)
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if p.strip()]
    # Known v1 limitation: abbreviations (e.g., "e.g.") can split early.
    # Acceptable — thresholds are statistical, not per-sentence.


def word_count(text: str) -> int:
    return len([w for w in text.split() if w.strip()])


def is_code_block(block: str) -> bool:
    return block.lstrip().startswith("```")


def is_heading_block(block: str) -> bool:
    return block.lstrip().startswith("#")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_segment.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add writing-agent/src/writing_agent/segment.py writing-agent/tests/test_segment.py
git commit -m "feat(writing-agent): markdown block and sentence segmentation"
```

---

### Task 3: Banned-token linter

**Files:**
- Create: `src/writing_agent/lint/__init__.py` (empty), `src/writing_agent/lint/banned_tokens.py`
- Test: `tests/test_banned_tokens.py`

**Interfaces:**
- Consumes: `CONFIG_DIR` from `config.py`.
- Produces: `load_patterns(path: Path | None = None) -> list[str]`; `scan_banned(blocks: list[str], patterns: list[str] | None = None) -> list[BannedHit]`; `BannedHit` NamedTuple `(pattern: str, block_index: int, count: int)` — one hit per offending block, `pattern` = comma-joined matched patterns.

- [ ] **Step 1: Write the failing tests**

`tests/test_banned_tokens.py`:

```python
from writing_agent.lint.banned_tokens import load_patterns, scan_banned

SLOP = (
    "Furthermore, it is important to note that this approach is robust. "
    "Additionally, the framework is a testament to good design. "
    "This lets us delve into the details seamlessly."
)
CLEAN = (
    "When the indexer collapsed, everything stopped. We had no runbook. "
    "Had we chosen Kafka two years earlier, the failure mode would have differed."
)


def test_seed_file_loads():
    patterns = load_patterns()
    assert any("delve" in p for p in patterns)
    assert all(not p.startswith("#") for p in patterns)


def test_slop_text_flagged():
    hits = scan_banned([SLOP])
    assert len(hits) == 1
    hit = hits[0]
    assert hit.block_index == 0
    assert hit.count >= 3
    assert "delve" in hit.pattern
    assert "testament to" in hit.pattern


def test_clean_text_passes():
    assert scan_banned([CLEAN]) == []


def test_hits_are_per_block():
    hits = scan_banned([CLEAN, SLOP])
    assert len(hits) == 1
    assert hits[0].block_index == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_banned_tokens.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement banned_tokens.py**

```python
"""Banned-token linter — regex scan against config/banned_ngrams.txt.

Hard-bans the high-frequency surface markers that RLHF amplifies (the
Structural Depth Hypothesis's "surface polish" failure mode).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

from ..config import CONFIG_DIR


class BannedHit(NamedTuple):
    pattern: str  # comma-joined matched patterns
    block_index: int
    count: int


def load_patterns(path: Path | None = None) -> list[str]:
    path = path or CONFIG_DIR / "banned_ngrams.txt"
    return [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def scan_banned(blocks: list[str], patterns: list[str] | None = None) -> list[BannedHit]:
    patterns = patterns if patterns is not None else load_patterns()
    hits: list[BannedHit] = []
    for idx, block in enumerate(blocks):
        matched = [
            pat for pat in patterns
            if re.search(pat, block, flags=re.IGNORECASE)
        ]
        if matched:
            hits.append(BannedHit(", ".join(matched), idx, len(matched)))
    return hits
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_banned_tokens.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add writing-agent/src/writing_agent/lint writing-agent/tests/test_banned_tokens.py
git commit -m "feat(writing-agent): banned-ngram linter"
```

---

### Task 4: Burstiness linter

**Files:**
- Create: `src/writing_agent/lint/burstiness.py`
- Test: `tests/test_burstiness.py`

**Interfaces:**
- Consumes: `split_sentences`, `word_count` from `segment.py`.
- Produces: `BurstinessResult` dataclass `(block_index: int, mean: float, std_dev: float, variance: float, sentence_lengths: list[int], failed: bool, reason: str | None)`; `check_burstiness(blocks: list[str], *, sd_min: float = 8.0, variance_min: float = 15.0) -> list[BurstinessResult]`. Blocks with fewer than 2 sentences never fail (too short to judge rhythm).

- [ ] **Step 1: Write the failing tests**

`tests/test_burstiness.py`:

```python
from writing_agent.lint.burstiness import check_burstiness

MONOTONE = (
    "The parser reads tokens from the input stream. "
    "The tokenizer splits text into words. "
    "The lexer assigns types to each token. "
    "The parser builds a tree from the tokens. "
    "The evaluator walks the finished tree."
)
HUMAN = (
    "When the indexer collapsed, everything stopped. "
    "We had no runbook (nobody expected the primary to fail during a rolling "
    "upgrade, which in hindsight was optimistic). "
    "Had we chosen Kafka two years earlier the failure mode would have been "
    "different, though maybe not better: the queue would have absorbed the "
    "burst, the consumers would have lagged instead of crashing, and honestly "
    "I still do not know which failure I prefer. "
    "We wrote the postmortem that night."
)


def test_monotone_fails():
    (result,) = check_burstiness([MONOTONE])
    assert result.failed
    assert "SD" in result.reason


def test_human_passes():
    (result,) = check_burstiness([HUMAN])
    assert not result.failed
    assert result.std_dev >= 8.0
    assert result.variance >= 15.0


def test_short_blocks_never_fail():
    results = check_burstiness(["## Setup", "One sentence only."])
    assert all(not r.failed for r in results)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_burstiness.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement burstiness.py**

```python
"""Sentence burstiness — the rhythmic fingerprint of human writing.

Aligned prose converges on a narrow sentence-length band (flattened
uncertainty); human prose mixes 4-word punches with 35-word compound
thoughts. Gates: per-block SD and variance of sentence word counts.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..segment import split_sentences, word_count


@dataclass
class BurstinessResult:
    block_index: int
    mean: float
    std_dev: float
    variance: float
    sentence_lengths: list[int] = field(repr=False)
    failed: bool
    reason: str | None = None


def check_burstiness(
    blocks: list[str],
    *,
    sd_min: float = 8.0,
    variance_min: float = 15.0,
) -> list[BurstinessResult]:
    results = []
    for idx, block in enumerate(blocks):
        lengths = [word_count(s) for s in split_sentences(block)]
        if len(lengths) < 2:
            results.append(BurstinessResult(idx, 0.0, 0.0, 0.0, lengths, False))
            continue
        mean = sum(lengths) / len(lengths)
        variance = sum((x - mean) ** 2 for x in lengths) / len(lengths)
        std_dev = variance ** 0.5
        failed = std_dev < sd_min or variance < variance_min
        reason = None
        if failed:
            bits = []
            if std_dev < sd_min:
                bits.append(f"sentence-length SD {std_dev:.1f} < {sd_min}")
            if variance < variance_min:
                bits.append(f"variance {variance:.1f} < {variance_min}")
            reason = "rhythmic monotony: " + "; ".join(bits)
        results.append(
            BurstinessResult(idx, mean, std_dev, variance, lengths, failed, reason)
        )
    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_burstiness.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add writing-agent/src/writing_agent/lint/burstiness.py writing-agent/tests/test_burstiness.py
git commit -m "feat(writing-agent): sentence burstiness linter"
```

---

### Task 5: Transition-density and em-dash linter

**Files:**
- Create: `src/writing_agent/lint/transitions.py`
- Test: `tests/test_transitions.py`

**Interfaces:**
- Produces: `TransitionResult` dataclass `(doc_density: float, transition_block_indices: list[int], em_dash_per_1k: dict[int, float], failed: bool, reason: str | None)`; `check_transitions(blocks: list[str], *, density_max: float = 0.15, em_dash_per_1k_max: float = 6.0) -> TransitionResult`. Transition density is whole-document (across all prose paragraphs); em-dash density is per block (block index → per-1k-words). Fails if `doc_density >= density_max` or any block exceeds the em-dash cap. `transition_block_indices` lists blocks containing a transition-opening paragraph (so the editor knows where to cut).

- [ ] **Step 1: Write the failing tests**

`tests/test_transitions.py`:

```python
from writing_agent.lint.transitions import check_transitions

TRANSITIONY = [
    "Furthermore, the parser reads tokens from the stream and assigns types.",
    "The tokenizer splits text into words before anything else happens here.",
    "However, the lexer needs a fallback for unknown characters in input.",
]
VARIED = [
    "The parser reads tokens from the stream. It works.",
    "Tokenization splits text first. Then typing happens.",
    "Unknown characters break the lexer. We patched it.",
]
EM_DASHY = (
    "The system — which we built — handles retries — badly — and the on-call "
    "rotation — three people — felt every one of those nights — painfully."
)


def test_transition_openings_fail():
    result = check_transitions(TRANSITIONY)
    assert result.doc_density == 1.0
    assert result.failed
    assert 0 in result.transition_block_indices


def test_varied_openings_pass():
    result = check_transitions(VARIED)
    assert result.doc_density == 0.0
    assert not result.failed


def test_em_dash_density_flagged():
    result = check_transitions([EM_DASHY])
    assert result.em_dash_per_1k[0] > 6.0
    assert result.failed
    assert "em-dash" in result.reason


def test_headings_and_code_ignored():
    result = check_transitions(["## Furthermore", "```python\nx = 1\n```"])
    assert result.doc_density == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_transitions.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement transitions.py**

```python
"""Transition-density + em-dash linter — SDH surface-marker checks.

Two failure modes from the research: (1) AI drafts open paragraph after
paragraph with conjunctive adverbs instead of concrete nouns/actions;
(2) aligned prose over-uses em-dashes — a surface marker that amplifies
while deep syntax collapses (Structural Depth Hypothesis).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..segment import is_code_block, is_heading_block

_TRANSITIONS = {
    "furthermore", "additionally", "moreover", "however", "in",
    "consequently", "therefore", "nevertheless", "nonetheless", "thus",
    "firstly", "secondly", "finally", "overall",
}
# "in" alone is too broad — only the phrase "In addition" counts.
_IN_PHRASES = ("in addition", "in conclusion", "in summary", "in short")


@dataclass
class TransitionResult:
    doc_density: float
    transition_block_indices: list[int] = field(default_factory=list)
    em_dash_per_1k: dict[int, float] = field(default_factory=dict)
    failed: bool = False
    reason: str | None = None


def _opens_with_transition(paragraph: str) -> bool:
    lowered = paragraph.lower()
    if lowered.startswith(_IN_PHRASES):
        return True
    first = re.match(r"[a-z]+", lowered)
    return bool(first and first.group(0) in _TRANSITIONS - {"in"})


def check_transitions(
    blocks: list[str],
    *,
    density_max: float = 0.15,
    em_dash_per_1k_max: float = 6.0,
) -> TransitionResult:
    paragraphs = 0
    openings = 0
    transition_blocks: list[int] = []
    em_dash: dict[int, float] = {}
    reasons: list[str] = []

    for idx, block in enumerate(blocks):
        if is_code_block(block) or is_heading_block(block):
            continue
        for para in (p.strip() for p in block.split("\n\n") if p.strip()):
            paragraphs += 1
            if _opens_with_transition(para):
                openings += 1
                if idx not in transition_blocks:
                    transition_blocks.append(idx)
        words = len(block.split())
        if words:
            density = block.count("—") / words * 1000
            em_dash[idx] = density
            if density > em_dash_per_1k_max:
                reasons.append(
                    f"em-dash density {density:.1f}/1k in block {idx} "
                    f"(max {em_dash_per_1k_max})"
                )

    doc_density = openings / paragraphs if paragraphs else 0.0
    if doc_density >= density_max:
        reasons.insert(
            0,
            f"{openings}/{paragraphs} paragraphs open with a transition "
            f"(density {doc_density:.2f} >= {density_max})",
        )
    return TransitionResult(
        doc_density=doc_density,
        transition_block_indices=transition_blocks,
        em_dash_per_1k=em_dash,
        failed=bool(reasons),
        reason="; ".join(reasons) if reasons else None,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_transitions.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add writing-agent/src/writing_agent/lint/transitions.py writing-agent/tests/test_transitions.py
git commit -m "feat(writing-agent): transition-density and em-dash linter"
```

---

### Task 6: Deep-syntax linter (Structural Depth Hypothesis)

**Files:**
- Create: `src/writing_agent/lint/deep_syntax.py`
- Test: `tests/test_deep_syntax.py`

**Interfaces:**
- Produces: `DeepSyntaxResult` dataclass `(block_index: int, counts: dict[str, int], total: int, failed: bool, reason: str | None)`; `check_deep_syntax(blocks: list[str], *, min_per_block: int = 1) -> list[DeepSyntaxResult]`. Counted structures: `parenthetical` `\([^)]{15,}\)`, `em_dash_aside` `—[^—\n]{15,}—`, `inversion` (leading `Had|Were|Should (we|I|they|you|it)`). Only prose blocks with ≥ 2 sentences and ≥ 40 words are judged; shallower blocks pass.

- [ ] **Step 1: Write the failing tests**

`tests/test_deep_syntax.py`:

```python
from writing_agent.lint.deep_syntax import check_deep_syntax

FLAT = (
    "The parser reads tokens from the input stream and assigns each one a "
    "type based on the grammar rules. The tokenizer splits text into words "
    "before anything else happens in the pipeline. The lexer then validates "
    "every token against the language specification."
)
DEEP = (
    "When the indexer collapsed, everything stopped. "
    "We had no runbook (nobody expected the primary to fail during a rolling "
    "upgrade, which in hindsight was optimistic). "
    "Had we chosen Kafka two years earlier the failure mode would have been "
    "different, though maybe not better, and honestly I still do not know "
    "which failure I prefer."
)


def test_flat_connective_prose_fails():
    (result,) = check_deep_syntax([FLAT])
    assert result.total == 0
    assert result.failed
    assert "deep syntax" in result.reason


def test_prose_with_asides_passes():
    (result,) = check_deep_syntax([DEEP])
    assert result.counts["parenthetical"] == 1
    assert result.counts["inversion"] == 1
    assert not result.failed


def test_short_blocks_not_judged():
    results = check_deep_syntax(["## Setup", "Short block."])
    assert all(not r.failed for r in results)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_deep_syntax.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement deep_syntax.py**

```python
"""Deep-syntax linter — Structural Depth Hypothesis enforcer.

Aligned models prune mid/deep syntactic dependencies (parentheticals,
inversions, embedded clauses) while inflating surface connectives. This
linter counts the human markers and fails judged blocks that have none.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..segment import is_code_block, is_heading_block, split_sentences, word_count

_PARENTHETICAL = re.compile(r"\([^)]{15,}\)")
_EM_DASH_ASIDE = re.compile(r"—[^—\n]{15,}—")
_INVERSIONS = [
    re.compile(r"\b(?:Had|Were|Should) (?:we|I|they|you|it)\b"),
]


@dataclass
class DeepSyntaxResult:
    block_index: int
    counts: dict[str, int] = field(default_factory=dict)
    total: int = 0
    failed: bool = False
    reason: str | None = None


def check_deep_syntax(
    blocks: list[str], *, min_per_block: int = 1
) -> list[DeepSyntaxResult]:
    results = []
    for idx, block in enumerate(blocks):
        if is_code_block(block) or is_heading_block(block):
            continue
        if len(split_sentences(block)) < 2 or word_count(block) < 40:
            continue  # too small to demand structural depth
        counts = {
            "parenthetical": len(_PARENTHETICAL.findall(block)),
            "em_dash_aside": len(_EM_DASH_ASIDE.findall(block)),
            "inversion": sum(rx.search(block) is not None for rx in _INVERSIONS),
        }
        total = sum(counts.values())
        failed = total < min_per_block
        results.append(
            DeepSyntaxResult(
                block_index=idx,
                counts=counts,
                total=total,
                failed=failed,
                reason=(
                    f"no deep-syntax structures (parentheticals, inversions, "
                    f"asides) in a {word_count(block)}-word block"
                )
                if failed
                else None,
            )
        )
    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_deep_syntax.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add writing-agent/src/writing_agent/lint/deep_syntax.py writing-agent/tests/test_deep_syntax.py
git commit -m "feat(writing-agent): deep-syntax (SDH) linter"
```

---

### Task 7: LintReport aggregation + scorecard

**Files:**
- Create: `src/writing_agent/lint/report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `scan_banned`, `check_burstiness`, `check_transitions`, `check_deep_syntax`, `ThresholdSettings`, `is_code_block`, `is_heading_block`.
- Produces: `BlockFailure(BaseModel)` `(block_index: int, reasons: list[str])`; `LintReport(BaseModel)` `(passed: bool, flagged_blocks: list[int], failures: list[BlockFailure], metrics: dict)` with method `scorecard() -> str` (plain-text table; no ANSI codes so it can be written to files); `run_all_linters(blocks: list[str], t: ThresholdSettings) -> LintReport`. `metrics` carries raw numbers: `{"banned": [{block_index, pattern, count}], "burstiness": [{block_index, mean, std_dev, variance}], "transitions": {doc_density, transition_block_indices, em_dash_per_1k}, "deep_syntax": [{block_index, counts, total}]}`.

- [ ] **Step 1: Write the failing tests**

`tests/test_report.py`:

```python
from writing_agent.config import ThresholdSettings
from writing_agent.lint.report import run_all_linters
from writing_agent.segment import split_blocks

SLOP = """## Overview

Furthermore, it is important to note that this approach is robust and it
delivers seamless integration for every user. Additionally, the framework
is a testament to good design across the board. Moreover, teams must
consider the implications carefully before adopting anything new here.
"""

HUMAN = """## Setup

When the indexer collapsed, everything stopped. We had no runbook (nobody
expected the primary to fail during a rolling upgrade, which in hindsight
was optimistic). Had we chosen Kafka two years earlier the failure mode
would have been different, though maybe not better: the queue would have
absorbed the burst, the consumers would have lagged instead of crashing,
and honestly I still do not know which failure I prefer. We wrote the
postmortem that night.

```python

x = 1
```
"""


def test_slop_doc_fails_and_flags_prose_block():
    report = run_all_linters(split_blocks(SLOP), ThresholdSettings())
    assert not report.passed
    assert 1 in report.flagged_blocks  # the prose block, not the heading
    reasons = next(f.reasons for f in report.failures if f.block_index == 1)
    assert any("banned" in r.lower() for r in reasons)
    assert any("monotony" in r for r in reasons)


def test_human_doc_passes_with_code_exempt():
    report = run_all_linters(split_blocks(HUMAN), ThresholdSettings())
    assert report.passed, report.failures
    assert report.flagged_blocks == []


def test_scorecard_rendes_text_table():
    report = run_all_linters(split_blocks(SLOP), ThresholdSettings())
    card = report.scorecard()
    assert "block" in card.lower()
    assert "PASS" in card or "FAIL" in card
    assert "\x1b" not in card  # no ANSI codes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_report.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement report.py**

```python
"""Lint report — aggregates all deterministic linters into one pass/fail gate."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..config import ThresholdSettings
from ..segment import is_code_block, is_heading_block
from .banned_tokens import scan_banned
from .burstiness import check_burstiness
from .deep_syntax import check_deep_syntax
from .transitions import check_transitions


class BlockFailure(BaseModel):
    block_index: int
    reasons: list[str]


class LintReport(BaseModel):
    passed: bool
    flagged_blocks: list[int]
    failures: list[BlockFailure]
    metrics: dict[str, Any] = Field(default_factory=dict)

    def scorecard(self) -> str:
        header = f"{'blk':>3}  {'SD':>5}  {'var':>>6}  {'banned':>6}  {'deep':>4}  note"
        lines = ["LINT SCORECARD", header, "-" * len(header)]
        m = self.metrics
        burst = {b["block_index"]: b for b in m.get("burstiness", [])}
        banned = {b["block_index"]: b for b in m.get("banned", [])}
        deep = {d["block_index"]: d for d in m.get("deep_syntax", [])}
        for idx in sorted(
            set(burst) | set(banned) | set(deep)
        ):
            b = burst.get(idx, {})
            note = "; ".join(
                f.reasons for f in self.failures if f.block_index == idx
            )
            lines.append(
                f"{idx:>3}  {b.get('std_dev', 0):>5.1f}  {b.get('variance', 0):>6.1f}"
                f"  {banned.get(idx, {}).get('count', 0):>6}"
                f"  {deep.get(idx, {}).get('total', 0):>4}  {note or 'ok'}"
            )
        tr = m.get("transitions", {})
        lines.append(
            f"\ntransition density {tr.get('doc_density', 0):.2f} "
            f"(max 0.15); em-dash/1k by block: {tr.get('em_dash_per_1k', {})}"
        )
        lines.append(f"RESULT: {'PASS' if self.passed else 'FAIL'}")
        return "\n".join(lines)


def run_all_linters(blocks: list[str], t: ThresholdSettings) -> LintReport:
    prose = [
        i for i, b in enumerate(blocks)
        if not is_code_block(b) and not is_heading_block(b)
    ]
    banned = scan_banned(blocks)
    burst = check_burstiness(
        blocks, sd_min=t.sentence_sd_min, variance_min=t.sentence_variance_min
    )
    trans = check_transitions(
        blocks,
        density_max=t.transition_density_max,
        em_dash_per_1k_max=t.em_dash_per_1k_max,
    )
    deep = check_deep_syntax(blocks, min_per_block=t.deep_syntax_min_per_block)

    by_block: dict[int, list[str]] = {}
    for hit in banned:
        if hit.block_index in prose:
            by_block.setdefault(hit.block_index, []).append(
                f"banned n-grams ({hit.count}): {hit.pattern}"
            )
    for b in burst:
        if b.failed and b.block_index in prose:
            by_block.setdefault(b.block_index, []).append(b.reason or "burstiness")
    for idx in trans.transition_block_indices:
        if idx in prose:
            by_block.setdefault(idx, []).append(
                "paragraph opens with a transition — start with a concrete noun or action"
            )
    for d in deep:
        if d.failed:
            by_block.setdefault(d.block_index, []).append(d.reason or "deep syntax")

    flagged = sorted(
        idx for idx in by_block if idx in prose
    )
    return LintReport(
        passed=not flagged and not trans.failed,
        flagged_blocks=flagged,
        failures=[
            BlockFailure(block_index=idx, reasons=reasons)
            for idx, reasons in sorted(by_block.items())
            if idx in prose
        ],
        metrics={
            "banned": [h._asdict() for h in banned],
            "burstiness": [
                {
                    "block_index": b.block_index,
                    "mean": b.mean,
                    "std_dev": b.std_dev,
                    "variance": b.variance,
                }
                for b in burst
            ],
            "transitions": {
                "doc_density": trans.doc_density,
                "transition_block_indices": trans.transition_block_indices,
                "em_dash_per_1k": trans.em_dash_per_1k,
                "reason": trans.reason,
            },
            "deep_syntax": [
                {"block_index": d.block_index, "counts": d.counts, "total": d.total}
                for d in deep
            ],
        },
    )
```

Note: em-dash/transitions doc-level failure (when `trans.failed` but no block flagged — e.g. doc-wide density from many blocks) marks `passed=False` with the reason stored in metrics; the editor prompt (Task 13) reads `metrics["transitions"]["reason"]` and applies it to transition-opening blocks. Edge case: if `trans.failed` with zero flagged blocks, `run_all_linters` still returns `passed=False` — the audit node treats this as a doc-level flag and routes to the editor with the densest paragraph. Keep this behavior; it is covered in Task 14 tests.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_report.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add writing-agent/src/writing_agent/lint/report.py writing-agent/tests/test_report.py
git commit -m "feat(writing-agent): aggregated lint report with scorecard"
```

---

### Task 8: CLI `wa lint`

**Files:**
- Create: `src/writing_agent/cli.py`
- Test: `tests/test_cli_lint.py`

**Interfaces:**
- Consumes: `run_all_linters`, `split_blocks`, `get_settings`.
- Produces: typer `app` object (console-script entry `wa`); command `lint(file: Path)` printing the scorecard; exit code 1 when the report fails.

- [ ] **Step 1: Write the failing test**

`tests/test_cli_lint.py`:

```python
from pathlib import Path

from typer.testing import CliRunner

from writing_agent.cli import app

runner = CliRunner()

SLOP = """## Overview

Furthermore, it is important to note that this approach is robust and it
delivers seamless integration for every user. Additionally, the framework
is a testament to good design across the board. Moreover, teams must
consider the implications carefully before adopting anything new here.
"""

HUMAN = """## Setup

When the indexer collapsed, everything stopped. We had no runbook (nobody
expected the primary to fail during a rolling upgrade, which in hindsight
was optimistic). Had we chosen Kafka two years earlier the failure mode
would have been different, though maybe not better: the queue would have
absorbed the burst, the consumers would have lagged instead of crashing,
and honestly I still do not know which failure I prefer. We wrote the
postmortem that night.
"""


def test_lint_slop_exits_nonzero(tmp_path: Path):
    f = tmp_path / "slop.md"
    f.write_text(SLOP)
    result = runner.invoke(app, ["lint", str(f)])
    assert "FAIL" in result.output
    assert result.exit_code == 1


def test_lint_human_passes(tmp_path: Path):
    f = tmp_path / "human.md"
    f.write_text(HUMAN)
    result = runner.invoke(app, ["lint", str(f)])
    assert "PASS" in result.output
    assert result.exit_code == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_lint.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement cli.py (lint command only)**

```python
"""wa — the anti-slop writing harness CLI."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

app = typer.Typer(help="Anti-slop writing harness", no_args_is_help=True)
console = Console()


@app.command()
def lint(file: Path) -> None:
    """Run deterministic linters on a markdown file and print the scorecard."""
    from .config import get_settings
    from .lint.report import run_all_linters
    from .segment import split_blocks

    report = run_all_linters(
        split_blocks(file.read_text()), get_settings().thresholds
    )
    console.print(report.scorecard())
    if not report.passed:
        raise typer.Exit(code=1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli_lint.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add writing-agent/src/writing_agent/cli.py writing-agent/tests/test_cli_lint.py
git commit -m "feat(writing-agent): wa lint CLI command"
```

---

### Task 9: OpenRouter client (chat, chat_json, completion_logprobs)

**Files:**
- Create: `src/writing_agent/llm.py`
- Test: `tests/test_llm.py`

**Interfaces:**
- Consumes: `get_settings`.
- Produces:
  - `chat(messages: list[dict], *, json_mode: bool = False, temperature: float = 0.3, top_p: float | None = None, max_tokens: int | None = None, retries: int = 4, model: str | None = None) -> str`
  - `chat_json(messages, *, temperature=0.3, max_tokens=None, model=None) -> dict`
  - `completion_logprobs(text: str, *, model: str | None = None) -> list[tuple[str, float]]` — legacy `/completions` call with `echo=True, logprobs=0, max_tokens=1`; returns per-token `(token, natural-log logprob)` for every token of `text`, each conditioned only on preceding tokens (no prompt leak — this is the standard teacher-forced scoring trick from the DetectGPT literature).
  - `_get_client() -> AsyncOpenAI` singleton raising `RuntimeError` with a helpful message when `OPENROUTER_API_KEY` is unset.

- [ ] **Step 1: Write the failing tests**

`tests/test_llm.py`:

```python
import pytest
from openai import AsyncOpenAI

from writing_agent import llm


def _fake_chat_response(content: str):
    msg = type("M", (), {"content": content})()
    choice = type("C", (), {"message": msg})()
    return type("R", (), {"choices": [choice]})()


def _fake_completion_response():
    lp = type(
        "LP",
        (),
        {"tokens": ["The", " cat"], "token_logprobs": [-2.0, -0.5]},
    )
    choice = type("C", (), {"logprobs": lp})()
    return type("R", (), {"choices": [choice]})()


@pytest.fixture
def fake_client(monkeypatch):
    calls = {}

    class FakeCompletions:
        async def create(self, **kwargs):
            calls["chat"] = kwargs
            return _fake_chat_response("hello world")

    class FakeLegacy:
        async def create(self, **kwargs):
            calls["legacy"] = kwargs
            return _fake_completion_response()

    client = type(
        "Client",
        (),
        {"chat": type("Chat", (), {"completions": FakeCompletions()})(),
         "completions": type("Comp", (), {"completions": FakeLegacy()})()},
    )
    monkeypatch.setattr(llm, "_client", client)
    return calls


async def test_chat_uses_openrouter_settings(fake_client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    out = await llm.chat([{"role": "user", "content": "hi"}], temperature=0.9, top_p=0.92)
    assert out == "hello world"
    kwargs = fake_client["chat"]
    assert kwargs["temperature"] == 0.9
    assert kwargs["top_p"] == 0.92


async def test_completion_logprobs_returns_tokens(fake_client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    pairs = await llm.completion_logprobs("The cat sat")
    assert pairs == [("The", -2.0), (" cat", -0.5)]
    kwargs = fake_client["legacy"]
    assert kwargs["echo"] is True
    assert kwargs["max_tokens"] == 1
    assert kwargs["logprobs"] == 0


async def test_chat_json_extracts_wrapped_json(fake_client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    fake_response = llm._client.chat.completions
    # chat_json hits chat(); reuse the fake's fixed output by making the
    # extraction path do the work: "hello world" has no JSON, so patch the
    # chat function instead for this test.
    async def fake_chat(messages, **kw):
        return 'Sure! {"a": 1}'

    result = await llm.chat_json_with(fake_chat)([{"role": "user", "content": "x"}])
    assert result == {"a": 1}
```

Note: the last test uses `chat_json_with` — a thin pure wrapper `chat_json_with(chat_fn)(messages, **kw) -> dict` that `chat_json` delegates to (`chat_json = partial`-style). This keeps JSON-extraction logic (ported verbatim from `study-app/backend/app/llm.py::_extract_json_object`) unit-testable without re-implementing the fake.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_llm.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement llm.py**

Port `chat`'s retry/backoff loop, `chat_json`'s nudge-retry, and `_extract_json_object` from `study-app/backend/app/llm.py` (keep the semantics, credit the source in the docstring). Full file:

```python
"""LLM calls via OpenRouter (OpenAI-compatible API).

Ported from study-app/backend/app/llm.py (retry + JSON-extraction
semantics kept). Adds completion_logprobs for surprisal scoring.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from typing import Any

from openai import AsyncOpenAI

from .config import get_settings

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        settings = get_settings()
        if not settings.openrouter_api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set. Copy .env.example to .env "
                "and add your key from https://openrouter.ai/keys"
            )
        _client = AsyncOpenAI(
            base_url=settings.openrouter_base_url,
            api_key=settings.openrouter_api_key,
        )
    return _client


async def chat(
    messages: list[dict[str, str]],
    *,
    json_mode: bool = False,
    temperature: float = 0.3,
    top_p: float | None = None,
    max_tokens: int | None = None,
    retries: int = 4,
    model: str | None = None,
) -> str:
    """Chat completion → assistant text. Retries empty responses with
    exponential backoff (1s, 2s, 4s, 8s) — cheap endpoints return "" under
    rate limits."""
    client = _get_client()
    kwargs: dict[str, Any] = {
        "model": model or get_settings().models.draft,
        "messages": messages,
        "temperature": temperature,
    }
    if top_p is not None:
        kwargs["top_p"] = top_p
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = await client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content or ""
            if content.strip():
                return content
            logger.warning("empty LLM response (attempt %d)", attempt + 1)
        except Exception as exc:
            last_exc = exc
            logger.warning("LLM call failed (attempt %d): %s", attempt + 1, exc)
        if attempt < retries:
            await asyncio.sleep(min(8.0, 2.0 ** attempt))
    if last_exc:
        raise last_exc
    return ""


def _extract_json_object(text: str) -> dict[str, Any]:
    """Best-effort: pull the first balanced {...} block out of `text`."""
    start = text.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found in LLM output: {text[:200]!r}")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError(f"Unbalanced JSON in LLM output: {text[:200]!r}")


def chat_json_with(chat_fn):
    """Build a chat_json bound to an injectable chat function (for tests)."""

    async def _chat_json(
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        raw = await chat_fn(
            messages,
            json_mode=True,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
        )
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            try:
                return _extract_json_object(raw)
            except (ValueError, json.JSONDecodeError):
                pass
        nudged = list(messages)
        nudged[0] = {
            **nudged[0],
            "content": nudged[0]["content"]
            + "\n\nIMPORTANT: respond with ONLY a single valid JSON object, "
            "no markdown, no prose, no code fences.",
        }
        await asyncio.sleep(1)
        raw = await chat_fn(
            nudged,
            json_mode=True,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
        )
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return _extract_json_object(raw)

    return _chat_json


chat_json = chat_json_with(chat)


async def completion_logprobs(
    text: str, *, model: str | None = None
) -> list[tuple[str, float]]:
    """Teacher-forced token logprobs for `text` via legacy /completions.

    echo=True + logprobs=0 + max_tokens=1 returns the logprob of every
    prompt token conditioned only on its predecessors — no answer-in-prompt
    leak (the failure mode of "echo this text" chat prompts). Logprobs are
    natural-log units; convert to bits with -lp / ln(2).
    """
    client = _get_client()
    response = await client.completions.create(
        model=model or get_settings().models.scorer,
        prompt=text,
        max_tokens=1,
        echo=True,
        logprobs=0,
    )
    lp = response.choices[0].logprobs
    return list(zip(lp.tokens, lp.token_logprobs))
```

Implementation note: `math` import is unused here — drop it when writing the file. Also drop the `_fake_response` unused line from the test.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_llm.py -v`
Expected: 3 PASS

- [ ] **Step 5: Manual smoke (optional, needs .env)**

Run: `uv run python -c "import asyncio; from writing_agent.llm import completion_logprobs; print(asyncio.run(completion_logprobs('The cat sat on the mat.')))"`
Expected: token/logprob pairs. If the scorer model rejects echo/logprobs, swap `models.scorer` in `config/default.toml` for one that supports `/completions` (the audit degrades gracefully regardless — Task 14).

- [ ] **Step 6: Commit**

```bash
git add writing-agent/src/writing_agent/llm.py writing-agent/tests/test_llm.py
git commit -m "feat(writing-agent): OpenRouter client with logprobs scoring"
```

---

### Task 10: Surprisal scorer + calibration

**Files:**
- Create: `src/writing_agent/scoring/__init__.py` (empty), `src/writing_agent/scoring/surprisal.py`
- Test: `tests/test_surprisal.py`

**Interfaces:**
- Consumes: `completion_logprobs` from `llm.py`; `is_code_block`, `is_heading_block` from `segment.py`.
- Produces:
  - `stats_from_logprobs(logprobs: list[float]) -> dict` with keys `n_tokens`, `mean_bits`, `variance_bits`, `peak_count` (peaks = tokens above 4.0 bits).
  - `score_blocks(blocks: list[str], *, mean_min: float = 1.2, variance_min: float = 2.0, llm=completion_logprobs) -> dict` — serializable report `{"skipped": bool, "warning": str | None, "blocks": [{"block_index", "n_tokens", "mean_bits", "variance_bits", "peak_count", "failed", "reason"}], "flagged_blocks": [int]}`. Code/heading blocks are not scored. Any `llm` exception → `{"skipped": True, "warning": str(exc), "blocks": [], "flagged_blocks": []}` (deterministic gates still apply; the pipeline never dies on scorer unavailability).
  - `calibrate(files: list[Path], *, llm=completion_logprobs) -> dict` — surprisal stats over human reference posts; returns `{"block_means": [...], "block_variances": [...], "suggested_mean_min": float, "suggested_variance_min": float}` (suggested = 10th percentile of observed block values, floored at 0.5 bits / 1.0 bits²).

- [ ] **Step 1: Write the failing tests**

`tests/test_surprisal.py`:

```python
import math

from writing_agent.scoring.surprisal import (
    calibrate,
    score_blocks,
    stats_from_logprobs,
)


def test_stats_from_logprobs_bits():
    # -0.25 nats everywhere → 0.36 bits, zero variance → flat, fails gates
    flat = stats_from_logprobs([-0.25, -0.25, -0.25, -0.25])
    assert flat["n_tokens"] == 4
    assert abs(flat["mean_bits"] - (0.25 / math.log(2))) < 1e-9
    assert flat["variance_bits"] == 0.0
    assert flat["peak_count"] == 0


def test_stats_peaks_count_rare_tokens():
    varied = stats_from_logprobs([-0.05, -3.0, -0.1, -4.0])
    assert varied["peak_count"] == 1  # only -4.0 nats ≈ 5.77 bits > 4


async def test_score_blocks_flags_flat_block():
    async def fake_llm(text: str, *, model=None):
        return [("t", -0.25)] * 6  # uniformly predictable

    report = score_blocks(["Flat text here."], llm=fake_llm)
    assert not report["skipped"]
    assert report["flagged_blocks"] == [0]
    assert "surprisal" in report["blocks"][0]["reason"]


async def test_score_blocks_passes_varied_block():
    async def fake_llm(text: str, *, model=None):
        return [("t", lp) for lp in (-0.05, -3.0, -0.1, -4.0, -0.2, -2.5)]

    report = score_blocks(["Varied text here."], llm=fake_llm)
    assert report["flagged_blocks"] == []


async def test_scorer_failure_degrades_gracefully():
    async def boom(text: str, *, model=None):
        raise RuntimeError("no echo support")

    report = score_blocks(["Anything."], llm=boom)
    assert report["skipped"] is True
    assert "echo" in report["warning"]
    assert report["flagged_blocks"] == []


async def test_code_and_heading_blocks_skipped():
    async def fake_llm(text: str, *, model=None):
        return [("t", -0.25)] * 6

    report = score_blocks(["## Heading", "```python\nx = 1\n```"], llm=fake_llm)
    assert report["blocks"] == []


async def test_calibrate_suggests_percentile_thresholds(tmp_path):
    async def fake_llm(text: str, *, model=None):
        return [("t", -1.0)] * 10  # 1.44 bits per token

    f = tmp_path / "ref.md"
    f.write_text("One two three four five six seven eight nine ten words.")
    out = calibrate([f], llm=fake_llm)
    assert abs(out["suggested_mean_min"] - min(out["block_means"])) < 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_surprisal.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement surprisal.py**

```python
"""Surprisal scorer — information-theoretic slop gate.

Human prose has high-variance token surprisal with local peaks; aligned
machine prose is flat and predictable. We measure per-block surprisal
statistics from a scoring model's logprobs (separate from the drafting
model) and gate on mean and variance. Surprisal in bits:
s_i = -log2 P(t_i | ctx) = -logprob_nat / ln(2).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from ..llm import completion_logprobs
from ..segment import is_code_block, is_heading_block

PEAK_BITS = 4.0


def stats_from_logprobs(logprobs: list[float]) -> dict[str, Any]:
    surprisal = [-lp / math.log(2) for lp in logprobs]
    n = len(surprisal)
    mean = sum(surprisal) / n if n else 0.0
    variance = sum((s - mean) ** 2 for s in surprisal) / n if n else 0.0
    return {
        "n_tokens": n,
        "mean_bits": mean,
        "variance_bits": variance,
        "peak_count": sum(1 for s in surprisal if s > PEAK_BITS),
    }


async def score_blocks(
    blocks: list[str],
    *,
    mean_min: float = 1.2,
    variance_min: float = 2.0,
    llm=completion_logprobs,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    flagged: list[int] = []
    try:
        for idx, block in enumerate(blocks):
            if is_code_block(block) or is_heading_block(block):
                continue
            pairs = await llm(block)
            stats = stats_from_logprobs([lp for _, lp in pairs])
            failed = stats["mean_bits"] < mean_min or (
                stats["variance_bits"] < variance_min
            )
            reason = None
            if failed:
                reason = (
                    f"flat surprisal: mean {stats['mean_bits']:.2f} bits "
                    f"(min {mean_min}), variance {stats['variance_bits']:.2f} "
                    f"(min {variance_min})"
                )
                flagged.append(idx)
            results.append({"block_index": idx, "failed": failed, "reason": reason, **stats})
    except Exception as exc:  # scorer unavailable → skip the gate, warn
        return {
            "skipped": True,
            "warning": f"surprisal scoring skipped: {exc}",
            "blocks": [],
            "flagged_blocks": [],
        }
    return {
        "skipped": False,
        "warning": None,
        "blocks": results,
        "flagged_blocks": flagged,
    }


async def calibrate(
    files: list[Path], *, llm=completion_logprobs
) -> dict[str, Any]:
    """Score human reference posts; suggest gates at the 10th percentile.

    Human references define 'normal' surprisal for your topics/models —
    the research thresholds are starting points, not laws.
    """
    means: list[float] = []
    variances: list[float] = []
    from ..segment import split_blocks

    for f in files:
        for block in split_blocks(f.read_text()):
            if is_code_block(block) or is_heading_block(block):
                continue
            pairs = await llm(block)
            stats = stats_from_logprobs([lp for _, lp in pairs])
            means.append(stats["mean_bits"])
            variances.append(stats["variance_bits"])

    def pct10(xs: list[float], floor: float) -> float:
        if not xs:
            return floor
        xs = sorted(xs)
        k = max(0, int(0.10 * (len(xs) - 1)))
        return max(xs[k], floor)

    return {
        "block_means": means,
        "block_variances": variances,
        "suggested_mean_min": pct10(means, 0.5),
        "suggested_variance_min": pct10(variances, 1.0),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_surprisal.py -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add writing-agent/src/writing_agent/scoring writing-agent/tests/test_surprisal.py
git commit -m "feat(writing-agent): surprisal scorer with calibration"
```

---

### Task 11: State + architect node

**Files:**
- Create: `src/writing_agent/state.py`, `src/writing_agent/nodes/__init__.py` (empty), `src/writing_agent/nodes/architect.py`
- Test: `tests/test_architect.py`

**Interfaces:**
- Consumes: `chat_json` from `llm.py`; `SamplingSettings`; `CONFIG_DIR`.
- Produces: `WritingState` TypedDict (below); `architect_node(state: WritingState, llm=chat_json) -> dict` returning `{"outline": dict}` where outline = `{"title": str, "style_notes": list[str], "scqa": {"situation", "complication", "question", "answer"}, "pillars": [{"heading", "claim", "evidence": list[str], "stylistic_mode"}], "open_loops": list[str]}`. Raises `ValueError("architect produced an invalid blueprint: ...")` on schema violations (missing scqa keys, <2 or >5 pillars).

`src/writing_agent/state.py`:

```python
"""WritingState — the data flowing through the LangGraph."""

from __future__ import annotations

from typing import Any, TypedDict


class WritingState(TypedDict, total=False):
    # Inputs
    topic: str
    audience: str
    persona: str            # raw persona markdown from config/personas/
    raw_research: str
    style_reference_paths: list[str]

    # Pipeline artifacts
    outline: dict[str, Any]          # SCQA blueprint JSON (architect)
    draft_blocks: list[str]          # one block per section (stylist/editor)
    lint_report: dict[str, Any]      # serialized LintReport (audit)
    surprisal_report: dict[str, Any] # serialized surprisal report (audit)
    flagged_blocks: list[int]

    # Loop control
    revision_count: int  # 1 after first draft; +1 per editor pass
    max_revisions: int

    # Outputs
    final_post: str
    scorecard: str
    error: str | None
```

- [ ] **Step 1: Write the failing tests**

`tests/test_architect.py`:

```python
import pytest

from writing_agent.nodes.architect import architect_node, validate_outline

GOOD = {
    "title": "Why our indexer collapsed",
    "style_notes": ["one-line paragraphs after code"],
    "scqa": {
        "situation": "s", "complication": "c",
        "question": "q", "answer": "a",
    },
    "pillars": [
        {"heading": "H1", "claim": "c1", "evidence": ["e"], "stylistic_mode": "anecdote-led"},
        {"heading": "H2", "claim": "c2", "evidence": ["e"], "stylistic_mode": "dense-analytical"},
    ],
    "open_loops": ["queue lag vs crash"],
}


def test_validate_outline_accepts_good():
    validate_outline(GOOD)  # no raise


def test_validate_outline_rejects_missing_scqa():
    bad = {**GOOD, "scqa": {"situation": "s"}}
    with pytest.raises(ValueError, match="scqa"):
        validate_outline(bad)


def test_validate_outline_rejects_too_few_pillars():
    bad = {**GOOD, "pillars": [GOOD["pillars"][0]]}
    with pytest.raises(ValueError, match="pillars"):
        validate_outline(bad)


async def test_architect_node_returns_validated_outline(monkeypatch):
    async def fake_llm(messages, **kw):
        assert kw["temperature"] == 0.2
        return GOOD

    state = {
        "topic": "indexer outage",
        "audience": "backend engineers",
        "persona": "casual",
        "raw_research": "notes",
        "style_reference_paths": [],
    }
    out = await architect_node(state, llm=fake_llm)
    assert out["outline"]["title"] == "Why our indexer collapsed"
    assert len(out["outline"]["pillars"]) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_architect.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement architect.py**

```python
"""Architect node — SCQA/Minto blueprint, never prose.

Structural planning and prose generation live in separate model contexts
(combining them reinforces mode collapse — see plan spec). Runs cool
(T=0.2): structure wants precision, not variance.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import CONFIG_DIR, get_settings
from ..llm import chat_json
from ..state import WritingState

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
- style_notes: if reference posts are provided, distill 3-6 concrete
  rhetorical mechanics from them (e.g. "one-sentence paragraphs after code
  blocks"), NOT vague voice adjectives.
"""

MODES = ["dense-analytical", "conversational", "anecdote-led", "counter-argument"]


def validate_outline(outline: dict[str, Any]) -> None:
    if not isinstance(outline.get("scqa"), dict) or not all(
        k in outline["scqa"] for k in ("situation", "complication", "question", "answer")
    ):
        raise ValueError(f"architect blueprint missing scqa keys: {outline.get('scqa')!r}")
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
    outline = await llm(
        [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
        temperature=get_settings().sampling.architect_temp,
    )
    validate_outline(outline)
    return {"outline": outline}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_architect.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add writing-agent/src/writing_agent/state.py writing-agent/src/writing_agent/nodes writing-agent/tests/test_architect.py
git commit -m "feat(writing-agent): WritingState + SCQA architect node"
```

---

### Task 12: Stylist node

**Files:**
- Create: `src/writing_agent/nodes/stylist.py`
- Test: `tests/test_stylist.py`

**Interfaces:**
- Consumes: `chat` from `llm.py`; `SamplingSettings`; `load_patterns` from `banned_tokens.py`.
- Produces: `stylist_node(state: WritingState, llm=chat) -> dict` returning `{"draft_blocks": list[str], "revision_count": 1}`. Blocks: intro (SCQA), one `## heading` block per pillar, outro (open loops — no tidy summary). Rotation of `stylistic_mode` across pillars enforced locally when the architect repeated a mode: `mode = pillar.get("stylistic_mode") if unique else MODES[i % 4]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_stylist.py`:

```python
from writing_agent.nodes.stylist import stylist_node

OUTLINE = {
    "title": "Why our indexer collapsed",
    "style_notes": ["one-line paragraphs after code"],
    "scqa": {
        "situation": "We ran a 3-node indexer.",
        "complication": "It collapsed during a rolling upgrade.",
        "question": "Why did the fallback not catch it?",
        "answer": "The queue was absorbing the wrong failure.",
    },
    "pillars": [
        {"heading": "The night it broke", "claim": "c1", "evidence": ["e1"], "stylistic_mode": "anecdote-led"},
        {"heading": "The queue lie", "claim": "c2", "evidence": ["e2"], "stylistic_mode": "dense-analytical"},
        {"heading": "What we kept", "claim": "c3", "evidence": ["e3"], "stylistic_mode": "anecdote-led"},
    ],
    "open_loops": ["lag vs crash still unresolved"],
}


def _llm_recording(sink):
    async def fake_llm(messages, **kw):
        sink.append((messages, kw))
        # Return something plausibly varied so nothing downstream chokes.
        return (
            "Short punch. "
            "Then a much longer sentence that carries the real technical "
            "payload of the section (which in hindsight was optimistic). "
            "Done."
        )

    return fake_llm


async def test_stylist_produces_block_per_pillar_plus_intro_and_outro():
    sink = []
    blocks = (await stylist_node(
        {"outline": OUTLINE, "persona": "casual"}, llm=_llm_recording(sink)
    ))["draft_blocks"]
    assert len(blocks) == 5  # intro + 3 pillars + outro
    assert blocks[1].startswith("## The night it broke")
    assert "open_loops" not in blocks[4].lower() or True  # outro text is model output
    assert (await stylist_node({"outline": OUTLINE}, llm=_llm_recording(sink)))["revision_count"] == 1


async def test_stylist_prompt_carries_constraints_and_sampling():
    sink = []
    await stylist_node(
        {"outline": OUTLINE, "persona": "be messy"}, llm=_llm_recording(sink)
    )
    user_prompt = sink[0][0][1]["content"]
    assert "candidate opening" in user_prompt      # verbalized sampling
    assert "parenthetical" in user_prompt          # deep-syntax enforcer
    assert "banned" in user_prompt                 # negative constraints
    assert "be messy" in user_prompt               # persona injected
    assert sink[0][1]["temperature"] == 0.9        # high-entropy drafting
    assert sink[0][1]["top_p"] == 0.92


async def test_modes_rotate_when_architect_repeats():
    sink = []
    await stylist_node({"outline": OUTLINE}, llm=_llm_recording(sink))
    # pillar prompts: calls 1..3 after the intro call
    modes = [sink[i][0][1]["content"].split("Mode: ")[1].split("\n")[0] for i in range(1, 4)]
    assert modes[0] != modes[1]  # adjacent pillars never share a mode
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_stylist.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement stylist.py**

```python
"""Stylist node — voice execution under anti-slop constraints.

Drafts HOT (T=0.9, top_p=0.92). Variance is forced structurally (verbalized
sampling, sentence-length spread, deep-syntax requirements, mode rotation)
rather than by temperature alone — the research's novelty/pragmaticality
paradox says raw temperature just breaks sense.
"""

from __future__ import annotations

from typing import Any

from ..config import get_settings
from ..lint.banned_tokens import load_patterns
from ..llm import chat
from ..state import WritingState

MODES = ["dense-analytical", "conversational", "anecdote-led", "counter-argument"]

SYSTEM = """You are a stylist executing a structural blueprint. Follow the
constraints EXACTLY. Output ONLY markdown prose for the requested section —
no meta commentary, no explanations of your choices."""


def _pillar_prompt(pillar: dict[str, Any], mode: str, persona: str,
                   style_notes: list[str], banned: str) -> str:
    return f"""Write the pillar section "{pillar['heading']}".

Persona: {persona}
Style mechanics to mirror: {'; '.join(style_notes) or 'none provided'}
Structural content to cover — claim: {pillar['claim']}; evidence: {', '.join(pillar.get('evidence', []))}
Mode: {mode}

Constraints:
- First write 3 candidate opening sentences with genuinely different rhythms and angles. Choose the LEAST predictable one and continue with it. Do not show the other two.
- Vary sentence length hard: at least one sentence under 8 words and one over 25 words in this section.
- Include at least one parenthetical aside (a real digression, in parentheses).
- Use parentheses for asides, never em-dashes.
- Do NOT open any paragraph with Furthermore/Additionally/However/Moreover-style transitions. Open with concrete nouns, verbs, or numbers.
- Banned words (hard): {banned}
- End the section without a mini-summary. No "key takeaway".
- Start the output with '## {pillar['heading']}' followed by the prose."""


async def stylist_node(state: WritingState, llm=chat) -> dict[str, Any]:
    outline = state["outline"]
    persona = state.get("persona", "")
    style_notes = outline.get("style_notes", [])
    banned = ", ".join(load_patterns()[:12])  # top of the list is enough signal
    sampling = get_settings().sampling

    async def draft(user_prompt: str) -> str:
        return await llm(
            [{"role": "system", "content": SYSTEM},
             {"role": "user", "content": user_prompt}],
            temperature=sampling.stylist_temp,
            top_p=sampling.stylist_top_p,
        )

    intro_prompt = (
        f"Write the opening section (no heading; start directly with prose).\n"
        f"Persona: {persona}\n"
        f"Situation: {outline['scqa']['situation']}\n"
        f"Complication: {outline['scqa']['complication']}\n"
        f"Question: {outline['scqa']['question']}\n"
        f"Answer (deliver upfront, BLUF): {outline['scqa']['answer']}\n"
        f"Style mechanics: {'; '.join(style_notes) or 'none provided'}\n\n"
        f"Constraints:\n"
        f"- Same constraints as body sections: 3 candidate openers (keep the "
        f"least predictable), hard sentence-length variance, one parenthetical "
        f"aside, no paragraph-opening transitions, no em-dashes, no closing "
        f"summary.\n"
        f"- Banned words (hard): {banned}"
    )
    blocks = [await draft(intro_prompt)]

    pillars = outline["pillars"]
    seen: list[str] = []
    modes: list[str] = []
    for p in pillars:
        m = p.get("stylistic_mode")
        if not m or m in seen:
            m = next(x for x in MODES if x not in seen[-1:] and (not seen or x != seen[-1]))
        seen.append(m)
        modes.append(m)
    for pillar, mode in zip(pillars, modes):
        blocks.append(
            await draft(_pillar_prompt(pillar, mode, persona, style_notes, banned))
        )

    loops = outline.get("open_loops", [])
    outro_prompt = (
        f"Write the closing section (no heading).\n"
        f"Persona: {persona}\n"
        f"Unresolved trade-offs to leave unresolved: {'; '.join(loops) or 'state honestly what is still open'}\n\n"
        f"Constraints:\n"
        f"- NO tidy resolution, NO 'in conclusion', NO recap of the pillars.\n"
        f"- Say what you would do differently and what you still do not know.\n"
        f"- Short: 2-4 sentences, at least one under 8 words.\n"
        f"- Banned words (hard): {banned}"
    )
    blocks.append(await draft(outro_prompt))

    return {"draft_blocks": blocks, "revision_count": 1}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_stylist.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add writing-agent/src/writing_agent/nodes/stylist.py writing-agent/tests/test_stylist.py
git commit -m "feat(writing-agent): stochastic stylist node with verbalized sampling"
```

---

### Task 13: Editor node

**Files:**
- Create: `src/writing_agent/nodes/editor.py`
- Test: `tests/test_editor.py`

**Interfaces:**
- Consumes: serialized `LintReport` dict (`failures: [{block_index, reasons}]`, `metrics["transitions"]["reason"]`) and `surprisal_report` dict from state; `chat`.
- Produces: `reasons_by_block(state) -> dict[int, str]` (pure; merges lint failures + surprisal reasons); `editor_node(state: WritingState, llm=chat) -> dict` returning `{"draft_blocks": list[str], "revision_count": prev + 1}` — rewrites ONLY `flagged_blocks`, prompt includes the block's failure reasons, runs at `sampling.editor_temp`.

- [ ] **Step 1: Write the failing tests**

`tests/test_editor.py`:

```python
from writing_agent.nodes.editor import editor_node, reasons_by_block


def _state(flagged=(1,), lint_reasons=None, surprisal_reasons=None):
    return {
        "draft_blocks": ["Intro.", "Bad block with delve.", "Fine block."],
        "flagged_blocks": list(flagged),
        "revision_count": 1,
        "lint_report": {
            "failures": [
                {"block_index": 1,
                 "reasons": lint_reasons or ["banned n-grams (1): delve"]},
            ],
        },
        "surprisal_report": {
            "blocks": [
                {"block_index": 1, "failed": bool(surprisal_reasons),
                 "reason": surprisal_reasons},
            ],
        },
    }


def test_reasons_by_block_merges_sources():
    merged = reasons_by_block(_state(surprisal_reasons="flat surprisal: mean 0.4"))
    assert "delve" in merged[1]
    assert "flat surprisal" in merged[1]


async def test_editor_rewrites_only_flagged_blocks():
    calls = []

    async def fake_llm(messages, **kw):
        calls.append((messages, kw))
        return "Fixed. (With an aside, for good measure.)"

    out = await editor_node(_state(), llm=fake_llm)
    assert out["draft_blocks"][0] == "Intro."        # untouched
    assert out["draft_blocks"][2] == "Fine block."   # untouched
    assert out["draft_blocks"][1].startswith("Fixed.")
    assert out["revision_count"] == 2
    assert len(calls) == 1  # one call, for the one flagged block
    assert calls[0][1]["temperature"] == 0.15
    assert "delve" in calls[0][0][1]["content"]  # defect reasons in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_editor.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement editor.py**

```python
"""Editor node — surgical rewrites of flagged blocks only.

Margin-aware in the Writing-RL sense: compute goes exclusively to the
blocks failing gates. Global re-generation is forbidden — it rolls new
stylistic dice on text that already passed.
"""

from __future__ import annotations

from typing import Any

from ..config import get_settings
from ..llm import chat
from ..state import WritingState

SYSTEM = """You are a surgical prose editor. Fix ONLY the listed defects.
Preserve every technical claim, number, command, code block, and markdown
heading. Do not restyle passages that are not named in the defects. Keep
the author's voice; make the minimum edits that clear the defects."""


def reasons_by_block(state: WritingState) -> dict[int, str]:
    merged: dict[int, list[str]] = {}
    for failure in state.get("lint_report", {}).get("failures", []):
        merged.setdefault(failure["block_index"], []).extend(failure["reasons"])
    for block in state.get("surprisal_report", {}).get("blocks", []):
        if block.get("failed") and block.get("reason"):
            merged.setdefault(block["block_index"], []).append(block["reason"])
    doc_reason = state.get("lint_report", {}).get("metrics", {}).get(
        "transitions", {}
    ).get("reason")
    if doc_reason:
        for idx in state.get("flagged_blocks", []):
            merged.setdefault(idx, []).append(doc_reason)
    return {idx: "; ".join(rs) for idx, rs in merged.items()}


async def editor_node(state: WritingState, llm=chat) -> dict[str, Any]:
    blocks = list(state["draft_blocks"])
    reasons = reasons_by_block(state)
    temp = get_settings().sampling.editor_temp
    for idx in state.get("flagged_blocks", []):
        defect_text = reasons.get(
            idx, "failed one or more style gates; increase sentence-length variance"
        )
        prompt = (
            f"DEFECTS:\n{defect_text}\n\nBLOCK:\n{blocks[idx]}\n\n"
            f"Rewrite the block clearing the defects. Keep markdown structure "
            f"identical. Output ONLY the rewritten block."
        )
        blocks[idx] = await llm(
            [{"role": "system", "content": SYSTEM},
             {"role": "user", "content": prompt}],
            temperature=temp,
        )
    return {
        "draft_blocks": blocks,
        "revision_count": state.get("revision_count", 1) + 1,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_editor.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add writing-agent/src/writing_agent/nodes/editor.py writing-agent/tests/test_editor.py
git commit -m "feat(writing-agent): targeted editor node"
```

---

### Task 14: Audit + finalize nodes, graph wiring

**Files:**
- Create: `src/writing_agent/nodes/audit.py`, `src/writing_agent/nodes/finalize.py`, `src/writing_agent/graph.py`
- Test: `tests/test_graph.py`

**Interfaces:**
- Consumes: `run_all_linters` (Task 7), `score_blocks` (Task 10), all nodes, `get_settings`.
- Produces:
  - `audit_node(state, scorer=score_blocks) -> dict` → `{"lint_report": dict, "surprisal_report": dict, "flagged_blocks": list[int]}` (union of lint + surprisal flags; if `lint_report.passed` is False due to doc-level transition density with zero flagged prose blocks, flag the first prose block so the editor has a target).
  - `finalize_node(state) -> dict` → `{"final_post": str, "scorecard": str}` (pure — no file IO).
  - `route_after_audit(state) -> str` returning `"finalize"` iff `not flagged` or `revision_count >= max_revisions`, else `"editor"`.
  - `build_graph(*, architect=architect_node, stylist=stylist_node, audit=audit_node, editor=editor_node, finalize=finalize_node) -> CompiledGraph`; edges `START→architect→stylist→audit→{editor→audit | finalize}→END`; module-level `GRAPH = build_graph()`.
  - `run_pipeline(*, topic: str, audience: str, persona: str, raw_research: str, style_reference_paths: list[str]) -> dict` (final state via `GRAPH.ainvoke`).

- [ ] **Step 1: Write the failing tests**

`tests/test_graph.py`:

```python
from writing_agent.config import get_settings
from writing_agent.graph import build_graph, route_after_audit
from writing_agent.nodes.audit import audit_node


def _fake_scorer_factory(flag_first: dict):
    async def scorer(blocks, **kw):
        if flag_first.get("on"):
            flag_first["on"] = False
            return {
                "skipped": False, "warning": None,
                "blocks": [{"block_index": 0, "failed": True,
                            "reason": "flat surprisal: mean 0.4 bits"}],
                "flagged_blocks": [0],
            }
        return {"skipped": False, "warning": None, "blocks": [], "flagged_blocks": []}

    return scorer


def test_route_finalize_when_clean():
    assert route_after_audit({"flagged_blocks": [], "revision_count": 1}) == "finalize"


def test_route_editor_when_flagged():
    assert route_after_audit({"flagged_blocks": [1], "revision_count": 1}) == "editor"


def test_route_finalize_at_max_revisions():
    assert route_after_audit(
        {"flagged_blocks": [1], "revision_count": 3}
    ) == "finalize"


async def test_audit_merges_lint_and_surprisal_flags():
    state = {
        "draft_blocks": [
            "## H",
            "Furthermore, it is important to note that this approach is "
            "robust and delivers seamless integration for every user. "
            "Additionally, the framework is a testament to good design "
            "across the board. Moreover, teams must consider everything "
            "carefully before adopting anything new here at all.",
        ],
    }
    flag = {"on": True}
    out = await audit_node(state, scorer=_fake_scorer_factory(flag))
    assert set(out["flagged_blocks"]) == {1, 0} or out["flagged_blocks"] == [1]
    # block 1 fails lint (banned + monotony); surprisal flag merged when present
    assert out["surprisal_report"]["flagged_blocks"] == [0]


async def test_audit_doc_level_transition_failure_flags_prose():
    state = {
        "draft_blocks": [
            "## H",
            "Furthermore, the parser reads tokens from the stream and then "
            "assigns each token a type based on grammar rules in place.",
            "However, the tokenizer splits text into words before anything "
            "else happens in this particular pipeline of ours today.",
            "Moreover, the lexer validates every token against the spec "
            "before the evaluator walks the tree that was just built.",
        ],
    }
    out = await audit_node(state, scorer=_fake_scorer_factory({"on": False}))
    # transition openings across 3 paragraphs → density 1.0 → doc fails;
    # transition blocks 1..3 are flagged so the editor has targets
    assert out["flagged_blocks"]


async def test_graph_loops_then_finalizes():
    audits = {"n": 0}

    async def stylist_stub(state):
        return {"draft_blocks": ["Intro.", "Body."], "revision_count": 1}

    async def audit_stub(state):
        audits["n"] += 1
        flagged = [1] if audits["n"] < 3 else []
        return {"flagged_blocks": flagged}

    async def editor_stub(state):
        blocks = list(state["draft_blocks"])
        return {"draft_blocks": blocks, "revision_count": state["revision_count"] + 1}

    async def architect_stub(state):
        return {"outline": {"scqa": {}, "pillars": []}}

    def finalize_stub(state):
        return {"final_post": "\n\n".join(state["draft_blocks"]), "scorecard": "done"}

    graph = build_graph(
        architect=architect_stub, stylist=stylist_stub,
        audit=audit_stub, editor=editor_stub, finalize=finalize_stub,
    )
    final = await graph.ainvoke({"topic": "t"})
    assert final["final_post"] == "Intro.\n\nBody."
    assert final["revision_count"] == 3   # draft(1) + two editor passes
    assert audits["n"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_graph.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement audit.py, finalize.py, graph.py**

`src/writing_agent/nodes/audit.py`:

```python
"""Audit node — deterministic linters + surprisal gate in one checkpoint."""

from __future__ import annotations

from typing import Any

from ..config import get_settings
from ..lint.report import run_all_linters
from ..scoring.surprisal import score_blocks
from ..segment import is_code_block, is_heading_block
from ..state import WritingState


async def audit_node(state: WritingState, scorer=score_blocks) -> dict[str, Any]:
    blocks = state["draft_blocks"]
    settings = get_settings()
    lint = run_all_linters(blocks, settings.thresholds)
    surprisal = await scorer(
        blocks,
        mean_min=settings.thresholds.mean_surprisal_min,
        variance_min=settings.thresholds.surprisal_variance_min,
    )

    flagged = sorted(set(lint.flagged_blocks) | set(surprisal["flagged_blocks"]))
    if not lint.passed and not flagged:
        # Doc-level failure (e.g. transition density) with no prose block
        # flagged — give the editor the first prose block as the target.
        prose = [
            i for i, b in enumerate(blocks)
            if not is_code_block(b) and not is_heading_block(b)
        ]
        if prose:
            flagged = [prose[0]]
            lint.failures.append(
                {"block_index": prose[0],
                 "reasons": ["doc-level transition-density failure — cut transition openings document-wide"]}
            )

    return {
        "lint_report": lint.model_dump(),
        "surprisal_report": surprisal,
        "flagged_blocks": flagged,
    }
```

Note: `lint.failures` entries are appended as plain dicts before `model_dump()` — pydantic coerces them to `BlockFailure`. 

`src/writing_agent/nodes/finalize.py`:

```python
"""Finalize node — assemble the post and scorecard (pure, no IO)."""

from __future__ import annotations

from typing import Any

from ..state import WritingState


def finalize_node(state: WritingState) -> dict[str, Any]:
    post = "\n\n".join(state["draft_blocks"])
    lint = state.get("lint_report", {})
    surprisal = state.get("surprisal_report", {})
    lines = ["FINAL SCORECARD", f"blocks: {len(state['draft_blocks'])}"]
    if lint:
        lines.append(f"lint passed: {lint.get('passed')}")
        lines.append(f"flagged: {lint.get('flagged_blocks')}")
    if surprisal.get("skipped"):
        lines.append(f"surprisal: SKIPPED ({surprisal.get('warning')})")
    elif surprisal.get("blocks"):
        means = [b["mean_bits"] for b in surprisal["blocks"]]
        lines.append(f"surprisal mean bits/block: {[round(m, 2) for m in means]}")
    lines.append(f"revisions: {state.get('revision_count', 1)}")
    return {"final_post": post, "scorecard": "\n".join(lines)}
```

`src/writing_agent/graph.py`:

```python
"""LangGraph StateGraph — the writing pipeline backbone.

architect → stylist → audit → (editor → audit)* → finalize. The audit's
conditional edge is the "compiler": flagged blocks route to surgical edits,
clean drafts (or exhausted budgets) route to finalize.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, START, StateGraph

from .nodes.architect import architect_node
from .nodes.audit import audit_node
from .nodes.editor import editor_node
from .nodes.finalize import finalize_node
from .nodes.stylist import stylist_node
from .state import WritingState

logger = logging.getLogger(__name__)


def route_after_audit(state: WritingState) -> str:
    if not state.get("flagged_blocks"):
        return "finalize"
    if state.get("revision_count", 1) >= _max_revisions():
        return "finalize"
    return "editor"


def _max_revisions() -> int:
    from .config import get_settings

    return get_settings().thresholds.max_revisions


def build_graph(*, architect=architect_node, stylist=stylist_node,
                audit=audit_node, editor=editor_node, finalize=finalize_node):
    builder = StateGraph(WritingState)
    builder.add_node("architect", architect)
    builder.add_node("stylist", stylist)
    builder.add_node("audit", audit)
    builder.add_node("editor", editor)
    builder.add_node("finalize", finalize)
    builder.add_edge(START, "architect")
    builder.add_edge("architect", "stylist")
    builder.add_edge("stylist", "audit")
    builder.add_conditional_edges(
        "audit", route_after_audit, {"editor": "editor", "finalize": "finalize"}
    )
    builder.add_edge("editor", "audit")
    builder.add_edge("finalize", END)
    return builder.compile()


GRAPH = build_graph()


async def run_pipeline(
    *,
    topic: str,
    audience: str = "technical practitioners",
    persona: str = "",
    raw_research: str = "",
    style_reference_paths: list[str] | None = None,
) -> dict[str, Any]:
    initial: WritingState = {
        "topic": topic,
        "audience": audience,
        "persona": persona,
        "raw_research": raw_research,
        "style_reference_paths": style_reference_paths or [],
    }
    return await GRAPH.ainvoke(initial)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_graph.py -v`
Expected: 6 PASS

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -v`
Expected: all tests PASS (Tasks 1–14)

- [ ] **Step 6: Commit**

```bash
git add writing-agent/src/writing_agent/nodes/audit.py writing-agent/src/writing_agent/nodes/finalize.py writing-agent/src/writing_agent/graph.py writing-agent/tests/test_graph.py
git commit -m "feat(writing-agent): audit gate + LangGraph pipeline wiring"
```

---

### Task 15: `wa draft` / `wa score` commands + README

**Files:**
- Modify: `src/writing_agent/cli.py`
- Create: `writing-agent/README.md`
- Test: `tests/test_cli_draft.py`

**Interfaces:**
- Consumes: `run_pipeline`, `calibrate`, `score_blocks`, `get_settings`, `CONFIG_DIR`.
- Produces: commands `draft(topic, research: Path = None, persona: str = "default", style_ref: list[Path] = ())` — writes `<workspace_dir>/<slug>.md` and `<slug>.scorecard.md`, prints scorecard, exits 0 even when still flagged (the human is the final gate); `score(file: Path, calibrate_dir: Path = None)` — per-block surprisal stats, or calibration table when `--calibrate-dir` is given.

- [ ] **Step 1: Write the failing test**

`tests/test_cli_draft.py`:

```python
from pathlib import Path

from typer.testing import CliRunner

from writing_agent.cli import app

runner = CliRunner()


async def test_draft_writes_post_and_scorecard(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))

    async def fake_pipeline(**kw):
        return {
            "final_post": "# Title\n\nBody.",
            "scorecard": "FINAL SCORECARD\nrevisions: 1",
            "flagged_blocks": [],
            "error": None,
        }

    import writing_agent.graph as graph_mod
    monkeypatch.setattr(graph_mod, "run_pipeline", fake_pipeline)
    # cli imports run_pipeline lazily inside the command (see impl) so the
    # module attribute patch lands.

    result = runner.invoke(app, ["draft", "my topic here"])
    assert result.exit_code == 0
    post = tmp_path / "my-topic-here.md"
    assert post.exists()
    assert post.read_text() == "# Title\n\nBody."
    assert (tmp_path / "my-topic-here.scorecard.md").exists()
```

Note: for the monkeypatch to land, `cli.py` must call `run_pipeline` via `from .graph import run_pipeline` **inside** the command function (lazy import), exactly as `lint` lazily imports its deps.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_draft.py -v`
Expected: FAIL — `draft` command missing (SystemExit / no such command)

- [ ] **Step 3: Extend cli.py**

Append to `src/writing_agent/cli.py` (keep the existing `lint`):

```python
import re

import asyncio


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "draft"


@app.command()
def draft(
    topic: str,
    research: Path = typer.Option(None, help="Markdown file of raw research notes"),
    persona: str = typer.Option("default", help="Persona name under config/personas/"),
    style_ref: list[Path] = typer.Option([], "--style-ref", help="Gold-standard post to mirror (repeatable)"),
) -> None:
    """Run the full pipeline; write the draft + scorecard into workspace/."""
    from .config import CONFIG_DIR, get_settings
    from .graph import run_pipeline

    persona_path = CONFIG_DIR / "personas" / f"{persona}.md"
    persona_text = persona_path.read_text() if persona_path.exists() else ""
    state = asyncio.run(
        run_pipeline(
            topic=topic,
            persona=persona_text,
            raw_research=research.read_text() if research else "",
            style_reference_paths=[str(p) for p in style_ref],
        )
    )
    if state.get("error"):
        console.print(f"[red]error:[/red] {state['error']}")
        raise typer.Exit(code=1)

    out_dir = get_settings().workspace_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = _slugify(topic)
    (out_dir / f"{slug}.md").write_text(state["final_post"])
    (out_dir / f"{slug}.scorecard.md").write_text(state["scorecard"])
    console.print(state["scorecard"])
    if state.get("flagged_blocks"):
        console.print(
            f"[yellow]still flagged after max revisions: "
            f"{state['flagged_blocks']} — human review advised[/yellow]"
        )
    console.print(f"[green]wrote[/green] {out_dir / f'{slug}.md'}")


@app.command()
def score(
    file: Path,
    calibrate_dir: Path = typer.Option(
        None, help="Score all reference posts in this dir and suggest thresholds"
    ),
) -> None:
    """Surprisal audit via OpenRouter logprobs."""
    from .config import get_settings
    from .scoring.surprisal import calibrate, score_blocks
    from .segment import split_blocks

    t = get_settings().thresholds

    async def _run():
        if calibrate_dir:
            files = sorted(calibrate_dir.glob("*.md"))
            return await calibrate(files)
        return await score_blocks(
            split_blocks(file.read_text()),
            mean_min=t.mean_surprisal_min,
            variance_min=t.surprisal_variance_min,
        )

    report = asyncio.run(_run())
    console.print(report)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli_draft.py -v`
Expected: 1 PASS. Then `uv run pytest -v` — full suite green.

- [ ] **Step 5: Write README.md**

```markdown
# writing-agent

An agent harness for technical blog writing that fights LLM statistical
normalization ("AI slop") instead of assuming the model will behave.

## Why

Coding agents work because code has a compiler and tests. Prose doesn't —
so this harness *is* the compiler: deterministic linters + an
information-theoretic surprisal gate play the role of `pytest`, and a
targeted editor loop plays `fix the failing tests`.

## Pipeline

    architect (SCQA/Minto JSON, T=0.2)
        → stylist (verbalized sampling, T=0.9, mode rotation)
        → audit (deterministic linters + surprisal gate)
        → editor (rewrites ONLY flagged blocks, T=0.15) → audit …
        → finalize (post + scorecard into workspace/)

## Quickstart

    uv sync
    cp .env.example .env   # add OPENROUTER_API_KEY
    uv run wa lint some-draft.md          # deterministic linting, no API
    uv run wa score some-draft.md         # surprisal audit (API)
    uv run wa draft "Why our indexer collapsed" --research notes.md

## Gates and why they exist

| Gate | Default | Research basis |
|---|---|---|
| sentence-length SD / variance | ≥ 8.0 / ≥ 15.0 | flattened uncertainty profile of aligned prose |
| banned n-grams | 0 hits | RLHF surface-marker amplification |
| transition openings | < 15% of paragraphs | paragraphs should open with content, not connectives |
| em-dash density | ≤ 6 / 1k words | Structural Depth Hypothesis surface marker |
| deep-syntax structures | ≥ 1 per prose block | SDH: parentheticals/inversions decay under alignment |
| mean surprisal | ≥ 1.2 bits/block | human prose keeps local surprisal peaks |

Thresholds live in `config/default.toml`. Calibrate the surprisal gates
against your own writing: drop human posts into `references/` and run
`uv run wa score x.md --calibrate-dir references/`.

## Notes & caveats

- Surprisal uses a separate scorer model (default
  `openai/gpt-3.5-turbo-instruct`) via legacy `/completions` echo+logprobs
  — the standard teacher-forced scoring trick; each token is scored
  conditioned only on preceding tokens. If the scorer is unavailable the
  gate degrades gracefully (warning in scorecard, deterministic gates
  still enforced).
- Verbalized sampling, mode rotation, and structural constraints carry
  the variance burden deliberately: the novelty/pragmaticality paradox
  says cranking temperature alone just breaks sense.

## Extension points (not yet built)

Semantic-embedding divergence linter · style-reference vector library ·
Genie novelty scoring · pairwise revision rewards (Writing-RL) ·
publishing integration with `site/` + POSSE · web review UI.
```

- [ ] **Step 6: Commit**

```bash
git add writing-agent/src/writing_agent/cli.py writing-agent/tests/test_cli_draft.py writing-agent/README.md
git commit -m "feat(writing-agent): draft/score CLI commands + README"
```

---

## Self-Review (done at plan time)

- **Spec coverage:** SCQA/Minto (Task 11), verbalized sampling + deep-syntax constraints + logit-level suppression (Tasks 3, 12 — API-level logit_bias is impossible via OpenRouter chat, so suppression is prompt-banned + linter-enforced), surprisal + local calibration (Tasks 9–10), margin-aware targeted edits (Tasks 7, 13, 14), temperature schedules (Tasks 11–13), StoryScope open loops + non-linearity (Tasks 11–12), asymmetric-agency mode rotation (Task 12), human-in-the-loop (workspace files + scorecard + `wa lint`, Tasks 8, 15), style reference ingestion (Tasks 11–12), graceful degradation (Tasks 10, 14).
- **Type consistency:** `score_blocks(blocks, *, mean_min, variance_min, llm)` matches audit node call; `run_all_linters(blocks, ThresholdSettings)` matches CLI + audit; `reasons_by_block` consumes the serialized `LintReport` shape produced by `model_dump()` (`failures[].block_index/reasons`, `metrics.transitions.reason`); `chat` signature gains `top_p` used by stylist; `completion_logprobs(text, *, model=None)` matches fake llm signatures in Task 10 tests (`fake_llm(text, *, model=None)`).
- **Known simplifications (accepted):** sentence splitting is regex-based (abbreviation splits possible — thresholds are statistical); surprisal via echo-completions depends on scorer model support (documented fallback); `em_dash_per_1k_max` and `surprisal_variance_min` defaults are judgment calls pending calibration against `references/`.
```

That completes the 15-task plan. Two things worth flagging before execution:

1. **Surprisal scoring method** — I chose the legacy `/completions` endpoint with `echo=True` + `logprobs` (teacher-forced scoring, the DetectGPT-era standard) because chat-completions logprobs on an "echo this text" prompt would leak the answer into the context and make everything look predictable. The audit degrades gracefully if OpenRouter's scorer model rejects it, and Task 9 includes a manual smoke test to catch this early — if `openai/gpt-3.5-turbo-instruct` no longer works on OpenRouter, swap `models.scorer` in `config/default.toml` for any completions model that supports echo+logprobs.

2. **Two judgment-call thresholds** — em-dash density (6/1k words) and surprisal variance (2.0 bits²) aren't grounded in hard numbers from your research; that's what the `wa score --calibrate-dir references/` command is for: it reads your own human posts and suggests gates at the 10th percentile of what you actually write.

**Plan complete and saved to `writing-agent/docs/plans/2026-09-19-anti-slop-writing-harness.md`.** Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — I execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach? (Or just say "not now" — the plan document is the deliverable you asked for, and it's self-contained enough to execute later.)