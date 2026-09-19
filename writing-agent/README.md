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

The editor uses **pairwise revision selection** (Writing-RL): each flagged
block gets a cold surgical candidate and a warmer variance candidate, and
a pairwise judge picks whichever clears the defects and reads more human
(presentation order alternates to avoid position bias; judge failures
fall back to the cold candidate). Disable via `pairwise_selection = false`
in `config/default.toml`.

## Quickstart

    uv sync
    cp .env.example .env   # add OPENROUTER_API_KEY
    uv run wa lint some-draft.md          # deterministic linting, no API
    uv run wa score some-draft.md         # surprisal audit (API)
    uv run wa shape some-draft.md         # StoryScope discourse-shape audit (API)
    uv run wa draft "Why our indexer collapsed" --research notes.md

## Gates and why they exist

| Gate | Default | Research basis |
|---|---|---|
| sentence-length SD / variance | ≥ 8.0 / ≥ 15.0 | flattened uncertainty profile of aligned prose |
| banned n-grams | 0 hits | RLHF surface-marker amplification |
| transition openings | < 15% of paragraphs | paragraphs should open with content, not connectives |
| em-dash density | ≤ 6 / 1k words | Structural Depth Hypothesis surface marker |
| deep-syntax structures | ≥ 1 per prose block | SDH: parentheticals/inversions decay under alignment |
| continuation predictability | > 0.9 bits blind-continuation surprisal (overlap ≥ 55% as fallback when logprobs unavailable) | machine prose is what a blind model continues confidently |
| summary/lesson markers | 0 hits | StoryScope: AI over-explains themes (explicit commentary 77% vs 52%) |
| temporal texture (jumps + retro-explanations) | ≥ 0.5 per 1k words (docs ≥ 400 words) | StoryScope: human narrative time is discontinuous, AI is linear |
| unresolved trade-offs | ≥ 1 (docs ≥ 400 words) | StoryScope: AI endings resolve internally |
| lexical concentration (top-5 content words) | ≤ 18% | StoryScope: AI clusters in a narrow narrative space |
| discourse judge axes | each < 0.7 | LLM-judged over-explaining / linearity / tidy resolution / abstraction |
| cross-section redundancy | 4-gram Jaccard < 0.25 per section pair | AI re-explains instead of advancing (StoryScope over-explanation) |
| surprisal SD across blocks | ≥ 0.15 bits (docs with ≥ 4 scored blocks) | GPTZero-style burstiness: uniform predictability reads machine |
| convergent continuations | 3 blind samples at T=0.8 agree < 50% of tokens | Fast-DetectGPT curvature, adapted: machine contexts make models converge |
| semantic glide | largest adjacent-section embedding step ≥ 0.12 | arXiv:2609.07920: humans introduce semantic shifts, models elaborate |

**Novelty (Genie-style)** is reported rather than gated: the share of the
draft's 3-grams absent from your references + AI controls + other drafts.
It appears as the ninth radar axis in the Graph view and in `wa shape`.
**Semantic jumps** (embedding glide, via OpenRouter's /embeddings endpoint
with `openai/text-embedding-3-small`) is the tenth radar axis and gates in
every audit pass.

**Optional second observer** (Binoculars-style cross-model corroboration):
set `models.observer` in `config/default.toml` to any chat model from a
different family (no logprobs needed — try a Gemini or Llama `-latest`).
When set, each scored block gets one extra continuation call; if the two
families agree on where the text goes, flags are corroborated or added.
Disabled (empty) by default.

The **Graph** button adds the paper-style views: narrative-rarity percentile
by group, narrative-space scatter with group centroids, and a radar profile.
`wa shape` also prints an **authorship distance** — how much closer the text
sits to your `references/` posts than to AI-shaped controls (needs posts in
`references/`; ratio < 1 is human-side).

Thresholds live in `config/default.toml`. Calibrate the surprisal gates
against your own writing: drop human posts into `references/` and run
`uv run wa score x.md --calibrate-dir references/`.

## Collaborative web editor (React SPA)

Build once, then run:

    cd writing-agent/frontend && npm install && npm run build
    uv run wa ui        # → http://127.0.0.1:8765

Hot-reload development: `npm run dev` in one shell (proxies /api to :8765)
and `uv run wa ui` in another.

Vite + React 19 + TypeScript + Tailwind 4 SPA (mirrors study-app/frontend
conventions) with a resizable three-pane layout — drafts sidebar, CodeMirror
6 editor with live markdown preview, and a tabbed panel:

- **Gates** — run the deterministic linters (offline) and the surprisal
  audit; pass/fail chips per gate family, per-block defect cards, and
  pairwise-judged "AI fix" flows that open a review diff before applying.
- **Shape** — StoryScope graphs (rarity percentiles, narrative-space
  scatter with centroids, 10-axis radar), authorship distance, judge axes.
- **Agent** — live SSE trace of pipeline runs (node by node, with
  elapsed time), plus the co-editor chat: type instructions, select text
  in the editor to scope them; revisions open as reviewable diffs.

Everything human/agent shared: every save and accepted AI change snapshots
to `workspace/.history/` — the History drawer diffs and restores any
version. ⌘K opens the command palette, ⌘S saves, autosave after 2.5s idle,
light/dark themes, and the last-open draft restores on reload.

## Notes & caveats

- Models (config/default.toml): drafting on `anthropic/claude-sonnet-5`
  (newest Sonnet; swap to `~anthropic/claude-opus-latest` for max prose
  quality at ~2.5x cost), scoring on `~openai/gpt-mini-latest`.
- Surprisal gate method: prefix-continuation scoring. The scorer sees only
  the document-so-far and continues it blind; the gate reads the
  continuation's mean token surprisal in bits (logprobs from the OpenAI
  -latest family; Anthropic never exposes them). Measured separation:
  generic AI-slop prose continues at ~0.6 bits, distinctive human prose at
  ~1.1 — the default gate is 0.9. When logprobs are unavailable the gate
  falls back to token overlap between the blind continuation and the real
  text. The scored text never appears in the prompt, so there is no
  answer-in-prompt leak. If the scorer is unavailable the gate degrades
  gracefully (warning in scorecard, deterministic gates still enforced).
- Verbalized sampling, mode rotation, and structural constraints carry
  the variance burden deliberately: the novelty/pragmaticality paradox
  says cranking temperature alone just breaks sense.

## Extension points (not yet built)

Style-reference vector library · embedding-space novelty and reference
clustering · publishing integration with `site/` + POSSE · multi-user editing.

Implementation plan: `docs/plans/2026-09-19-anti-slop-writing-harness.md`.
