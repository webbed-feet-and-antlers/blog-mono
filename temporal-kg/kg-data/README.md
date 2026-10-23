# kg-data

Real-grounded synthetic + public data pipeline for **temporal knowledge graph
extraction** — text → (head, relation, tail, valid_from/valid_to) quadruples,
with staleness, conflicts, revisions, and deletions.

Built to train/test/evaluate a tKG model against the enterprise grounding
scenario: company knowledge scattered across Slack / Confluence / Drive /
email / meeting notes, where old documents are rarely updated and nothing is
ever explicitly deprecated.

## Why synthetic + real

Real public datasets (T-REx, Wikidata) give real text and real temporal
qualifiers, but no staleness/conflict signal — no corpus says "this doc was
true in 2022 and nobody has contradicted it since". So the pipeline builds a
**synthetic enterprise lifecycle corpus** where ground truth is known by
construction, then grounds its *realism* in real messy corpora (Ubuntu IRC
dialogues, Enron emails, Stack Overflow threads) via style exemplars and
measured noise statistics.

## Pipeline

| Stage | Module | What it does |
|---|---|---|
| 0 | `grounding.py` | Downloads capped real corpora; derives per-channel style exemplars, noise stats, jargon lexicon |
| 1 | `worldgen.py` | Seeded scaffold (entities, predicates with volatility classes, scheduled change events) + one LLM call to name things. All gold labels derive from this DB, never from the LLM again |
| 2 | `docgen.py` + `messify.py` | Coverage matrix assigns facts per doc **as of its date** → implicit obsolescence is structural. ~10% conflict injections, ~20% doc revisions (fact-changing or cosmetic), ~5% deletions, ~15% never-stated facts. Offset-safe noise pass after rendering |
| 3 | `annotate.py` | Verbatim mention → char-offset alignment (span verification, one strict regeneration on failure) |
| 3½ | `audit.py` | Gold = what the text **asserts**, not what was planned: an LLM classifies each timeline-active fact as asserted / not_asserted / unsure (coreference-aware, hedged flag). Selections are validated against the timeline, so truth stays deterministic. Planned quads drop only on auditor denial **plus** mechanical no-same-sentence corroboration (auditor drop precision measured ~50%); omissions fail safe (kept); discovered assertions become new gold (`discovered: true`). Scorer reports F1 against planned-gold and full gold separately |
| 4 | `evalgen.py` | Pure-code eval questions: point-in-time, current-state, history, staleness (superseded **and** old-but-still-true), conflict |
| 5 | `adapters/trex.py` | `relbert/t_rex` (parquet revision) real-text triples + Wikidata SPARQL temporal enrichment (P580/P582) distant-supervised onto Wikipedia summaries |
| 6 | `extract.py` | Few-shot LLM baseline extractor + QA answering (validates the harness, sets a floor) |
| 7 | `export.py` | Splits **by world** (no timeline leakage), ingestion log (CDC stream), sha256 manifest |
| 8 | `score.py` | Triple F1 (relation matches predicate id OR label), temporal exact/overlap match, question accuracy, stream final-graph F1 with deletion semantics |

## Usage

```bash
cp .env.example .env          # add OPENROUTER_API_KEY
uv sync
uv run pytest tests/ -q       # offline, no network

uv run python -m kg_data grounding                      # stage 0, once
uv run python -m kg_data generate --worlds 2 --docs-per-world 40   # pilot
uv run python -m kg_data adapt --cap 3000 --enrich 300  # public data
uv run python -m kg_data extract --split val            # baseline extraction
uv run python -m kg_data answer --split val             # baseline QA
uv run python -m kg_data score --split val              # F1 + accuracy
uv run python -m kg_data audit --split train            # re-audit an export in place
```

The audit stage wants a sturdier model than the free flash tier (empty
responses under load, and assertion judgment benefits from it):

```bash
OPENROUTER_MODEL=openai/gpt-4o-mini CONCURRENCY=4 uv run python -m kg_data audit --split train
```

Scale-up is `generate --worlds 20 --docs-per-world 80` after eyeballing the
pilot. Everything lands in `data/` (gitignored); `data/manifest.json` records
sha256s, seeds, model, and split assignment.

## Reviewing quality before scale-up

```bash
uv run python -m kg_data review --split train
open data/review/review_train.html
```

A self-contained page (no server): each doc rendered the way its channel
reads — Slack as a chat thread, Confluence/Drive/meeting notes as pages,
email with a subject header — with gold head/tail spans highlighted inline
from their char offsets (red wavy = injected conflict), the doc's quads
listed with an automatic ✓/✗ "true at doc date" check against the timeline,
per-doc noise signals, a heuristic unlisted-proper-noun trap-line, and
corpus stats (per-channel messiness vs the real grounding corpora, opener
repetition, predicate coverage). Tabs switch to eval questions (answers +
gold-doc links) and world timelines. Mark docs ✓/✗ with notes; **export
verdicts** downloads them as JSON for acting on.

Suggested pass bar for a ~20-doc stratified sample ("sample 20" button):
realism ≥80% "could pass as real" per channel; ≥95% of quads assert their
relation in the text (not just co-mention — the highlights make this a
5-second check); ✗ "not current" appearing ONLY on red conflict quads;
no unlisted real-world products; no opener template reused across >20% of
a channel.

## Pilot results (2026-10-22, 2 worlds, deepseek flash)

| Metric | Value |
|---|---|
| Docs / revisions / deletions | 80 / 82 / 2 |
| Gold quads (conflict-injected) | 326 (5) |
| Span-verification rate | 612/627 = 97.6% |
| Eval questions | 34 |
| Baseline extractor triple F1 | 0.327 (P 0.53 / R 0.24†) |
| Baseline QA accuracy | 52.9% — still_true 100%, conflict 0% |

† recall depressed: 41/80 docs lost to persistent empty responses from the
free flash model under load — rerun extraction with `CONCURRENCY=2` or a
stronger model. The 20–40% zero-shot F1 range matches the research
literature's calibration for this task.

## Output format (neutral — no trainer-specific fields)

- `synthetic/worlds.jsonl` — ground-truth timelines (entities, predicates,
  volatility, segments with valid_from/valid_to)
- `synthetic/corpus/{split}/docs.jsonl` — docs with all revisions
  (channel, author persona incl. offboarding, sim times, content hashes)
- `synthetic/gold/{split}/quads.jsonl` — gold quads: head/tail mentions with
  char offsets, relation, temporal bounds, `time_source`, `claim_status`
  (`accurate` vs `conflict_injected`), provenance (doc/rev)
- `synthetic/evals/questions.jsonl` — questions with answers derived from the
  timeline DB (never LLM-judged)
- `synthetic/ingestion_log.jsonl` — ordered create/revise/delete events (the
  CDC stream an incremental graph-updater consumes)
- `public/trex/{split}/` — real-text quads (time_source `none`) and Wikidata
  temporal quads (`kb_qualifier`)

Bi-temporality by construction: **valid time** comes from the world timeline;
**transaction time** is the sim time of the revision stating the quad.

### Gold conventions

- A quad records what a document *claims*. `claim_status=conflict_injected`
  marks deliberately wrong/outdated claims (accurate quads for the same
  (head, relation) exist elsewhere).
- Accurate quads carry `valid_from` = the true segment start (`time_source:
  explicit_in_text` when the text states it, else `doc_date` context) and
  `valid_to=None` — an extractor reading the doc cannot know the future.
- Conflict quads carry no bounds (`time_source: none`).

## Design notes

- **Splits by world**: a fictional company's whole lifecycle lives in one
  split; random doc-level splits would leak entity timelines.
- **Noise before alignment**: messify corrupts text outside protected mention
  spans and returns adjusted offsets, so gold spans are computed on the final
  messy text.
- **Seeded everywhere**: same seed + same model responses → same manifest
  hashes. (LLM nondeterminism aside, all structure is reproducible.)
- **LLM JSON drift defense**: prompts embed the exact field skeleton, keys
  are camelCase-normalized, and validation failures get exactly one repair
  pass (mirrors study-app's `llm.py`).
- **One asyncio.run per process** — the shared AsyncOpenAI client dies under
  a second event loop.

`../references/` holds the training notebooks this corpus is destined for;
adapting the trainer to consume the neutral format (incl. a temporal head) is
out of scope here — that is `../kg-pipeline`'s job (its `kgdata` bridge
adapter reads this project's `data/` exports and emits canonical Documents
plus UnifiedDataset trainer files).

## Verified data sources

- `relbert/t_rex` pinned to parquet commit `5baa776c…` (`TREX_PARQUET_REVISION`
  in `adapters/trex.py`; `refs/convert/parquet` is a moving branch and the
  script loader is unsupported by datasets>=3 — bump the pin deliberately)
- `ntcuong777/ubuntu_dialogue_corpus_train`, `corbt/enron-emails`,
  `HuggingFaceGECLM/StackExchange_Mar2023` (split `stackoverflow`)
- Wikidata Query Service (SPARQL) + Wikipedia REST summaries

"TempREX" (temporal-scoped T-REx) was referenced in earlier research but does
not verifiably exist; the Wikidata-qualifier enrichment covers that need.
