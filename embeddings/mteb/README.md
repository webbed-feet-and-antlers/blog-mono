# MTEB Custom Retrieval Dataset (GovReport + DeepSeek)

Builds a custom **MTEB retrieval task** from `ccdv/govreport-summarization`
using the DeepSeek API to (1) chunk reports semantically and (2) generate
per-chunk queries that only that chunk can answer.

## Pipeline

```
   ┌────────────────────┐    ┌────────────────────┐    ┌────────────────────┐
   │ 01_chunk_reports   │ →  │ 02_generate_queries│ →  │ 03_build_retrieval │
   │ DeepSeek chunks    │    │ DeepSeek queries   │    │ pure local         │
   │ chunks.jsonl       │    │ queries.jsonl      │    │ MTEB layout        │
   └────────────────────┘    └────────────────────┘    └────────────────────┘
```

Each stage is independent and resumable: LLM responses are cached on disk, so
re-running picks up where it left off.

## Output format (standard MTEB retrieval)

```
datasets/govreport_retrieval/
├── corpus.jsonl      → {"_id": "train_0__c0", "title": "...", "text": "..."}
├── queries.jsonl     → {"_id": "train_0__c0__q0", "text": "...the query..."}
└── qrels/test.tsv    → query-id\tcorpus-id\tscore  (1.0 for gold; header row)
```

Each query maps to exactly one gold chunk. MTEB treats all unlisted corpus
items as negatives implicitly.

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
python -m scripts --subset 5
```

Stage 2 will print 3 sample (chunk, queries) pairs to stderr. Eyeball them:
queries should be non-generic, non-quoting, and answerable only from that chunk.

### Dev run (50 reports, the default)

```bash
cd embeddings
task mteb:dev
```

Or stage-by-stage:

```bash
task mteb:chunk          # Stage 1
task mteb:queries        # Stage 2
task mteb:build          # Stage 3 (no LLM)
```

Override flags via Task variables:

```bash
task mteb:all SUBSET=20 MODEL=deepseek-chat CONCURRENCY=4
```

### Validation

Stage 3 runs `validate_mteb_dir()` internally. Re-run it manually:

```bash
cd embeddings/mteb
python -c "
from pathlib import Path
from scripts.dataset_io import validate_mteb_dir
stats = validate_mteb_dir(Path('datasets/govreport_retrieval'))
print(stats)
"
```

### Line-count sanity check

For a 50-report subset you should see roughly:

```
wc -l datasets/govreport_retrieval/corpus.jsonl    # ~150 (50 reports × ~3 chunks)
wc -l datasets/govreport_retrieval/queries.jsonl   # ~300 (150 chunks × ~2 queries)
wc -l datasets/govreport_retrieval/qrels/test.tsv  # queries + 1 header
```

## Resume & recovery

The pipeline is built to crash safely and pick up where it left off. Two
layers of recovery:

1. **LLM-response cache** (`cache/<sha256-prefix>.json`) — every successful
   DeepSeek response is persisted on disk. Re-running any stage hits the
   cache for sections/queries that already succeeded, so retries cost nothing
   in API spend.
2. **Resume from intermediate output** — Stage 1 reads
   `intermediate/chunks.jsonl` (if present) and skips report_ids already
   written. Stage 2 does the same with `intermediate/queries.jsonl` and
   chunk_ids. Output files are opened in **append** mode and flushed after
   every row, so a Ctrl-C or OOM leaves a usable partial file on disk.

To force a clean restart (e.g. after changing `--model` or the prompts):

```bash
python -m scripts --restart            # all stages
python 01_chunk_reports.py --restart   # just stage 1
python 02_generate_queries.py --restart
```

`--restart` truncates that stage's intermediate output(s) before running.
The LLM cache is untouched (it's keyed by model + messages, so changing
`--model` naturally invalidates relevant entries anyway).

Edge case: if a report had a partial section failure last run (some sections
succeeded, one failed), its successful chunks are already in `chunks.jsonl`
and Stage 1 will treat the whole report as done. To retry the failed
sections, delete that report's rows from `chunks.jsonl` (or use `--restart`).

## Common flags

| Flag | Default | Env | Purpose |
|---|---|---|---|
| `--subset N` | 50 | `MTEB_SUBSET` | Reports to process (proportional across splits) |
| `--model NAME` | `deepseek-chat` | `MTEB_MODEL` | DeepSeek model id |
| `--concurrency N` | 10 | `MTEB_CONCURRENCY` | Max parallel API calls |
| `--api-key KEY` | — | `DEEPSEEK_API_KEY` | Flag wins, then env, else error |
| `--split NAME` | `train` | `MTEB_SPLIT` | GovReport split |
| `--verbose` | off | — | DEBUG logging |
| `--no-cache` | off | — | Bypass cache reads (still writes) |
| `--restart` | off | — | Truncate this stage's output(s) before running |

Stage 1 also has `--max-report-chars 24000` (long-report pre-split threshold).
Stage 2 also has `--sample-print 3`.
Stage 3 also has `--dataset-name govreport_retrieval` and `--strict-verbatim`.

## Cache

Every LLM response is cached under `cache/<sha256-prefix>.json` — one file per
request, atomic writes, fully inspectable. The cache key includes the model
id, messages, temperature, and max_tokens, so changing `--model` cleanly
invalidates entries.

```bash
ls cache/ | wc -l             # ~200 files for a 50-report run
cat cache/<some-key>.json | jq .parsed
```

`cache/`, `intermediate/`, and `datasets/` are all gitignored — they're
reproducible from the dataset + prompts.

## Cost

A 50-report run is ~50 chunking + ~150 query calls ≈ ~200 API calls and ~1.2M
tokens total. At deepseek-chat pricing this is well under USD 1.

If you hit 429 storms, drop concurrency: `--concurrency 4`.

## Troubleshooting

- **Long reports (> 24k chars)** — Stage 1 pre-splits on paragraph boundaries
  and chunks each section independently.
- **LLM paraphrases chunks** — Stage 3 warns on near-duplicate chunk text
  (SequenceMatcher ratio ≥ 0.85). Use `--strict-verbatim` to drop them.
- **Empty queries list** — Stage 1 schema requires ≥ 1 chunk; the repair
  prompt fires automatically. Persistent failures land in
  `intermediate/_failures.jsonl` and the chunk is dropped from the dataset.
- **Mid-run crash** — every JSONL is flushed per row, the cache holds raw API
  responses, and stages resume from existing intermediate files. Just re-run
  the same command. Use `--restart` to start over (e.g. after a prompt tweak).

## Out of scope for this MVP

- STS, cross-report retrieval, summary STS, clustering tasks.
- GCP deploy, Docker, HF Hub push.
- Automated smoke test with the `mteb` Python library (the produced directory
  matches MTEB's `LocalDataset` layout and should be loadable directly).
