# temporal-kg

Everything for training, testing, and evaluating a **temporal knowledge-graph
extraction** model — text → (head, relation, tail, valid_from/valid_to)
quads with staleness, conflicts, revisions, and deletions — grounded in the
enterprise scenario where company knowledge is scattered across Slack /
Confluence / Drive / email and old documents are never explicitly retired.

Two sub-projects with their own toolchains, one data flow:

```
kg-data/                                   kg-pipeline/
  stage 0  grounding (real corpora)          adapters/kgdata  ← bridge over kg-data exports
  stage 1  worldgen  (ground-truth timelines)  adapters/icews   ← event quads (CAMEO)
  stage 2  docgen + messify                  kg_pipeline/schema  canonical Documents
  stage 3  annotate  (gold spans)            kg_pipeline/unified UnifiedDataset {ner,re,el}
  stage 4  evalgen   (questions, pure code)  kg_pipeline/store   versioned normalized/ + unified/
  stage 5  T-REx + Wikidata adapters         registry, sanitize/dedup, http/path guards
  stage 6  baseline extractor + QA
  stage 7  export ──────────────────────────▶ --source kgdata --input ../kg-data/data/...
  stage 8  score (triple/temporal F1, QA)

references/   the training notebooks both halves serve (Ideal_KG_Training*.ipynb)
```

- **`kg-data/`** generates the corpus and owns ground truth: a synthetic
  enterprise lifecycle (seeded timelines → multi-channel documents stated
  *as of their date*, with injected conflicts, revisions, deletions, and a
  noise channel measured from real Ubuntu IRC / Enron / Stack Overflow text),
  the public T-REx + Wikidata-qualifier adapters, eval questions derived from
  the timeline DB, a baseline extractor, and the scorer. Output is a neutral
  JSONL format (docs, gold quads with char offsets, questions, ingestion log,
  sha256 manifest). See `kg-data/README.md`.
- **`kg-pipeline/`** is the trainer-facing side: adapters normalize sources
  into one canonical quad schema (namespaced relation labels, `explicit_in_text`
  distinguishing text-stated times from KB-qualifier distant supervision),
  then emit the raw formats the notebook's `UnifiedDataset` already parses.
  The `kgdata` adapter is the bridge — kg-data is the single home for the
  synthetic generator and the T-REx path. See `kg-pipeline/README.md`.

## Running the whole flow

```bash
cd kg-data && uv sync && uv run pytest tests/ -q                 # 40 tests
uv run python -m kg_data grounding                               # once
uv run python -m kg_data generate --worlds 2 --docs-per-world 40 # pilot (20×80 to scale)
uv run python -m kg_data adapt --cap 3000 --enrich 300           # public data

cd ../kg-pipeline && uv sync --extra dev && .venv/bin/python -m pytest  # 51 tests
.venv/bin/python -m kg_pipeline --source kgdata \
    --input ../kg-data/data/synthetic --split train --out data
.venv/bin/python -m kg_pipeline --source kgdata \
    --input ../kg-data/data/public/trex --split train --out data
```

Both `data/` directories are gitignored outputs; everything is reproducible
from seeds + manifests.
