# MTEB Custom Datasets (GovReport + DeepSeek)

Builds seven custom **MTEB tasks** from `ccdv/govreport-summarization` using
the DeepSeek API to chunk reports, generate queries, score pair similarity,
assign topic labels, judge relevance, and find cross-report golds.

## Pipeline

```
   ┌────────────────────┐    ┌────────────────────┐    ┌────────────────────┐
   │ 01_chunk_reports   │ →  │ 02_generate_queries│ →  │ 03_build_retrieval │
   │ DeepSeek chunks    │    │ DeepSeek queries   │    │ pure local         │
   │ chunks.jsonl       │    │ queries.jsonl      │    │ MTEB retrieval     │
   └────────────────────┘    └────────────────────┘    └────────────────────┘
              │                         │
              ↓                         ↓
   ┌────────────────────┐    ┌────────────────────┐
   │ 04_sts             │    │ 07_reranking       │
   │ 05_summary_sts     │    │ 08_cross_report    │
   │ 06_clustering      │    └────────────────────┘
   │ 09_pair_classify   │
   └────────────────────┘
```

Stages 04–09 are "combined gen + build" — each one reads chunks.jsonl (and
optionally queries.jsonl), calls DeepSeek as needed, and writes both an
intermediate file and the final MTEB-format dataset directory.

Each stage is independent and resumable: LLM responses are cached on disk, so
re-running picks up where it left off.

## Tasks

| Stage | Task | Output dir | MTEB row schema |
|---|---|---|---|
| 03 | Retrieval | `datasets/govreport_retrieval/` | corpus.jsonl + queries.jsonl + qrels/test.tsv |
| 04 | STS | `datasets/govreport_sts/test.jsonl` | `{sent1, sent2, score}` (0–5) |
| 05 | Summary STS | `datasets/govreport_summary_sts/test.jsonl` | same as STS |
| 06 | Clustering | `datasets/govreport_clustering/test.jsonl` | `{text, label}` (15-topic vocab) |
| 07 | Reranking | `datasets/govreport_reranking/test.jsonl` | `{query, positive: [...], negative: [...]}` |
| 08 | Cross-report retrieval | `datasets/govreport_cross_report/` | same layout as retrieval; qrels have ≥ 2 golds for some queries |
| 09 | Pair Classification | `datasets/govreport_pair_classification/test.jsonl` | `{sent1, sent2, labels: [0\|1]}` |

### Standard retrieval format

```
datasets/govreport_retrieval/
├── corpus.jsonl      → {"_id": "train_0__c0", "title": "...", "text": "..."}
├── queries.jsonl     → {"_id": "train_0__c0__q0", "text": "...the query..."}
└── qrels/test.tsv    → query-id\tcorpus-id\tscore  (1.0 for gold; header row)
```

Each retrieval query maps to exactly one gold chunk. Cross-report retrieval
uses the same layout but adds LLM-found positives from other reports.

## Setup

```bash
cd embeddings
uv pip install -e ".[mteb]"
```

Create a `.env` (or export in your shell) from `.env.example`:

```
DEEPSEEK_API_KEY=sk-...
# Optional overrides:
# MTEB_SUBSET=50
# MTEB_MODEL=deepseek-chat
# MTEB_CONCURRENCY=10
```

## Running

### Smoke test (5 reports, a few cents of API spend)

```bash
cd embeddings/mteb
export DEEPSEEK_API_KEY=sk-...
python -m scripts --subset 5 --task retrieval
```

Stage 2 will print 3 sample (chunk, queries) pairs to stderr. Eyeball them:
queries should be non-generic, non-quoting, and answerable only from that chunk.

### Per-task runs via the `--task` flag

```bash
python -m scripts --task sts                  # stage 1 → 4
python -m scripts --task summary_sts          # stage 1 → 5
python -m scripts --task clustering           # stage 1 → 6
python -m scripts --task reranking            # stage 1 → 2 → 7
python -m scripts --task cross_report         # stage 1 → 2 → 8
python -m scripts --task pair_classification  # stage 1 → 9
python -m scripts --task all                  # retrieval + all 6 new tasks
```

The orchestrator skips any stage whose output already exists (pass
`--no-skip-existing` to force re-runs).

### Dev run (50 reports, the default)

```bash
cd embeddings
task mteb:dev                 # retrieval only (50 reports)
task mteb:everything          # all 7 task types end-to-end
```

Or stage-by-stage:

```bash
task mteb:chunk               # Stage 1
task mteb:queries             # Stage 2
task mteb:build               # Stage 3 (no LLM)
task mteb:sts                 # Stage 4
task mteb:summary-sts         # Stage 5
task mteb:clustering          # Stage 6
task mteb:reranking           # Stage 7
task mteb:cross-report        # Stage 8
task mteb:pair-classification # Stage 9
```

Override flags via Task variables:

```bash
task mteb:everything SUBSET=20 MODEL=deepseek-chat CONCURRENCY=4
```

### Validation

Every stage runs its task-specific validator internally. Re-run them manually:

```bash
cd embeddings/mteb
python -c "
from pathlib import Path
from scripts.dataset_io import (
    validate_mteb_dir,
    validate_sts_dir,
    validate_clustering_dir,
    validate_reranking_dir,
    validate_pair_classification_dir,
    validate_cross_report_dir,
)
print('retrieval:', validate_mteb_dir(Path('datasets/govreport_retrieval')))
print('sts:', validate_sts_dir(Path('datasets/govreport_sts')))
print('summary_sts:', validate_sts_dir(Path('datasets/govreport_summary_sts')))
print('clustering:', validate_clustering_dir(Path('datasets/govreport_clustering')))
print('reranking:', validate_reranking_dir(Path('datasets/govreport_reranking')))
print('pair_classification:', validate_pair_classification_dir(Path('datasets/govreport_pair_classification')))
print('cross_report:', validate_cross_report_dir(Path('datasets/govreport_cross_report')))
"
```

### Line-count sanity check

For a 50-report subset (~150 chunks, ~300 queries) you should see roughly:

```
wc -l datasets/govreport_retrieval/corpus.jsonl               # ~150
wc -l datasets/govreport_retrieval/queries.jsonl              # ~300
wc -l datasets/govreport_retrieval/qrels/test.tsv             # ~300 + 1 header
wc -l datasets/govreport_sts/test.jsonl                       # ~300
wc -l datasets/govreport_summary_sts/test.jsonl               # ~300
wc -l datasets/govreport_clustering/test.jsonl                # ~150
wc -l datasets/govreport_reranking/test.jsonl                 # ~300 (queries)
wc -l datasets/govreport_cross_report/qrels/test.tsv          # >300 (cross-report golds added)
wc -l datasets/govreport_pair_classification/test.jsonl       # ~300
```

## Evaluation

Once datasets exist, evaluate embedding models against them:

```bash
cd embeddings
uv pip install -e ".[evaluate-all]"      # API + local providers
```

Per-model results land in `mteb/results/` (committed; visible in PR diffs).
Each run writes/overwrites a per-model JSON and regenerates `_leaderboard.md`.

### Single-model smoke

```bash
python3 mteb/evaluate/__main__.py \
    --provider openai --model text-embedding-3-small --tasks sts
```

### Full matrix

```bash
task mteb:evaluate:all
cat mteb/results/_leaderboard.md
```

### Providers

| Provider | Models | API key env |
|---|---|---|
| `openai` | `text-embedding-3-small`, `text-embedding-3-large` | `OPENAI_API_KEY` |
| `gemini` | `text-embedding-004`, `gemini-embedding-001` | `GOOGLE_API_KEY` (alias `GEMINI_API_KEY`) |
| `sentence-transformers` | `all-MiniLM-L6-v2`, `bge-base-en-v1.5` | (none — local) |

### CLI flags

| Flag | Default | Purpose |
|---|---|---|
| `--provider` | — | `openai` \| `gemini` \| `sentence-transformers` |
| `--model` | — | Provider model id |
| `--tasks` | all 7 | Comma-separated task names |
| `--all` | off | Run the full MODEL_MATRIX |
| `--device` | `cpu` | `cpu` \| `cuda` \| `mps` (sentence-transformers only) |
| `--api-key` | env | Override provider key |
| `--datasets-dir` | `mteb/datasets` | Path to datasets/ |
| `--results-dir` | `mteb/results` | Path to results/ |
| `--no-cache` | off | Bypass the embedding cache (re-encode everything) |
| `--cache-dir` | `mteb/cache/embeddings` | Embedding cache root |
| `--verbose` | off | DEBUG logging |

### Embedding cache

The evaluator transparently caches embeddings on disk so re-runs pay only
for inputs not yet seen. This matters for API providers (cost + rate limits)
and for `sentence-transformers` (CPU/GPU time), and it makes the runner
crash-safe: a partial run preserves what was already encoded.

- **Location**: `mteb/cache/embeddings/<model>__<kind>__mc<max_chars>_d<dim>.npz`
  — one file per `(model, kind, max_chars, dim)` combo. Gitignored alongside
  the LLM-response cache.
- **Key**: the original (pre-truncation) text. `kind` (`query` / `document` /
  `text`) is part of the filename because E5/BGE/Gemini return different
  vectors for the same text under different task types.
- **Invalidation**: changing `--dim` or `--max-chars` writes a new file
  (different `_d<dim>` / `mc<max_chars>` suffix). Changing `--model` writes
  a new file. No silent staleness.
- **Lifecycle**: the file is loaded lazily on first `encode()` for a kind,
  and flushed once when the run finishes. Mid-run crashes preserve any
  embeddings already computed in prior runs, but the *current* run's new
  embeddings are only persisted on a clean exit.
- **`--no-cache`**: toggles cache reads AND writes together — never a
  half-state. Use it to force a clean re-encode.

```bash
# First run: populates the cache.
python3 mteb/evaluate/__main__.py --provider openai \
    --model text-embedding-3-small --tasks sts

# Second run: cache hits only, near-instant, zero API calls.
python3 mteb/evaluate/__main__.py --provider openai \
    --model text-embedding-3-small --tasks sts --verbose
```

The cache is single-process: if two processes race the same file, the last
writer wins. Delete files under `mteb/cache/embeddings/` to clear.

### Result format

See `mteb/results/README.md` for the JSON schema and leaderboard column docs.

## Per-task design notes

- **STS (04)** — for each chunk, samples one within-report pair and one
  cross-report pair (~300 pairs total). LLM scores 0–5.
- **Summary STS (05)** — pairs each chunk with its own report summary
  (positive) and one random other-report summary (negative). Same 0–5 score.
- **Clustering (06)** — fixed 15-topic vocabulary. LLM hallucinated topics
  trigger a batch failure and the chunks land in `_failures.jsonl`. The 15
  topics are: Healthcare, Defense & Military, Environment & Energy, Economy &
  Finance, Education, Technology & Telecom, Justice & Law Enforcement, Foreign
  Policy, Homeland Security, Housing & Urban Development, Labor & Employment,
  Science & Research, Social Services, Transportation, Veterans Affairs.
- **Reranking (07)** — per query, samples 1 gold + 4 within-report + 5
  cross-report candidates. LLM scores each 0–3. Output partition:
  **score ≥ 2 → positive; score ≤ 1 → negative**. The gold is force-injected
  into `positive` regardless of its LLM score. Use `--candidates-per-query 5`
  to halve API cost.
- **Cross-report (08)** — per query, samples 10 candidates from reports ≠
  gold's report. LLM emits binary relevance. Qrels combine original gold +
  LLM-found positives. Validator requires at least one query to have ≥ 2
  qrels (proves cross-report positives exist).
- **Pair Classification (09)** — same sampling pattern as STS. LLM emits 0/1
  per pair. `labels` is a 1-element list per MTEB convention.

## Resume & recovery

The pipeline is built to crash safely and pick up where it left off. Two
layers of recovery:

1. **LLM-response cache** (`cache/<sha256-prefix>.json`) — every successful
   DeepSeek response is persisted on disk. Re-running any stage hits the
   cache for sections/queries that already succeeded, so retries cost nothing
   in API spend.
2. **Resume from intermediate output** — every stage records its progress in
   a per-task intermediate file and skips IDs already present. Output files
   are opened in **append** mode and flushed after every row, so a Ctrl-C or
   OOM leaves a usable partial file on disk.

To force a clean restart (e.g. after changing `--model` or the prompts):

```bash
python -m scripts --task sts --restart        # all stages in --task
python 04_sts.py --restart                    # one stage only
```

`--restart` truncates that stage's intermediate output(s) before running.
The LLM cache is untouched (it's keyed by model + messages, so changing
`--model` naturally invalidates relevant entries anyway).

## Common flags

| Flag | Default | Env | Purpose |
|---|---|---|---|
| `--task NAME` | `retrieval` | — | Pipeline to run |
| `--subset N` | 50 | `MTEB_SUBSET` | Reports to process (proportional across splits) |
| `--model NAME` | `deepseek-chat` | `MTEB_MODEL` | DeepSeek model id |
| `--concurrency N` | 10 | `MTEB_CONCURRENCY` | Max parallel API calls |
| `--api-key KEY` | — | `DEEPSEEK_API_KEY` | Flag wins, then env, else error |
| `--split NAME` | `train` | `MTEB_SPLIT` | GovReport split |
| `--verbose` | off | — | DEBUG logging |
| `--no-cache` | off | — | Bypass cache reads (still writes) |
| `--restart` | off | — | Truncate this stage's output(s) before running |
| `--no-skip-existing` | off | — | Disable orchestrator's resume-aware stage skipping |

Per-stage extras:

| Stage | Extra flags |
|---|---|
| 01 | `--max-report-chars 24000` |
| 02 | `--sample-print 3` |
| 03 | `--dataset-name`, `--strict-verbatim` |
| 04 (STS) | `--pairs-per-chunk 2`, `--batch-size 5`, `--dataset-name` |
| 05 (Summary STS) | `--negatives-per-chunk 1`, `--batch-size 5`, `--dataset-name` |
| 06 (Clustering) | `--batch-size 5`, `--dataset-name` |
| 07 (Reranking) | `--candidates-per-query 10`, `--dataset-name` |
| 08 (Cross-report) | `--candidates-per-query 10`, `--dataset-name` |
| 09 (Pair Classification) | `--pairs-per-chunk 2`, `--batch-size 5`, `--dataset-name` |

## Cache

Every LLM response is cached under `cache/<sha256-prefix>.json` — one file per
request, atomic writes, fully inspectable. The cache key includes the model
id, messages, temperature, and max_tokens, so changing `--model` cleanly
invalidates entries.

```bash
ls cache/ | wc -l             # ~1000 files for a full everything run
cat cache/<some-key>.json | jq .parsed
```

`cache/`, `intermediate/`, and `datasets/` are all gitignored — they're
reproducible from the dataset + prompts.

## Cost

A 50-report everything run (~150 chunks, ~300 queries):

| Task | Calls | Tokens | Notes |
|---|---|---|---|
| Retrieval | ~200 | ~1.2M | chunking + query gen |
| STS | ~60 | ~0.4M | batched 5/call |
| Summary STS | ~60 | ~0.4M | batched 5/call |
| Clustering | ~30 | ~0.2M | batched 5/call |
| Reranking | ~300 | ~1.0M | 10 candidates each |
| Cross-report | ~300 | ~1.0M | 10 candidates each |
| Pair Classification | ~60 | ~0.4M | batched 5/call |
| **Total** | **~1010** | **~4.6M** | sub-USD-3 at deepseek-chat pricing |

Cache makes re-runs free for unchanged inputs. If you hit 429 storms, drop
concurrency: `--concurrency 4`.

## Troubleshooting

- **Long reports (> 24k chars)** — Stage 1 pre-splits on paragraph boundaries
  and chunks each section independently.
- **LLM paraphrases chunks** — Stage 3 warns on near-duplicate chunk text
  (SequenceMatcher ratio ≥ 0.85). Use `--strict-verbatim` to drop them.
- **Empty queries list** — Stage 1 schema requires ≥ 1 chunk; the repair
  prompt fires automatically. Persistent failures land in
  `intermediate/_failures.jsonl` and the chunk is dropped from the dataset.
- **Clustering topic-vocab drift** — if the LLM emits a topic not in the
  15-item vocab, the batch fails and chunks are logged to `_failures.jsonl`.
- **Reranking imbalance** — if the LLM scores everything relevant (or
  irrelevant), the gold chunk is still force-injected into `positive`.
- **Cross-report false positives** — original gold is always preserved;
  LLM-found positives are additive and can't remove the gold.
- **Mid-run crash** — every JSONL is flushed per row, the cache holds raw API
  responses, and stages resume from existing intermediate files. Just re-run
  the same command. Use `--restart` to start over (e.g. after a prompt tweak).

## Out of scope for this MVP

- GCP deploy, Docker, HF Hub push.
- The `mteb` Python library is intentionally not used for evaluation — the
  custom runner in `mteb/evaluate/` covers the same 7 task types with a
  lighter dependency set. See the "Evaluation" section above.
