# kg-pipeline

Data pipeline for training, testing, and evaluating a **temporal knowledge
graph extraction** model (the GLiNER/GLiDRE-style unified trainer in
`Ideal_KG_Training_new.ipynb`) on realistic data — generic pre-training,
synthetic-enterprise bridging, and continuous company adaptation, while the
company data itself doesn't exist yet.

## Architecture

```
[Data plane]     adapters -> canonical quad Documents -> versioned store (JSONL/parquet)
                 + dual output in the raw formats UnifiedDataset already parses
[Training plane] phase 1 generic (temporal-enriched) -> 1.5 synthetic-enterprise -> 2 continuous
[Serving plane]  connectors (Drive/Confluence/Slack) -> extraction svc -> temporal graph store
```

The core contract is `kg_pipeline/schema.py`: every adapter emits `Document`s
with entities, quads, and optional `TimeBound`s. Quads carry namespaced
relation labels (`P108`, `CAMEO:190`, `ENT:BEFORE`, `COMPANY:OWNS_SERVICE`)
that pass verbatim into the trainer's `[REL] …` label space, plus an optional
`claim_status` (synthetic corpora distinguish accurate claims from
deliberately injected conflicts). The crucial temporal distinction is
`explicit_in_text`: Wikidata-qualifier times are not stated in the text,
ICEWS/kg-data `explicit_in_text`-tagged quads are — two supervision regimes
that eval must separate.

### Convergence with kg-data (2026-10-23)

kg-data (sibling directory) is the single home for the synthetic-enterprise
generator (worldgen/docgen/messify/evalgen: revisions, conflicts, implicit
obsolescence, staleness QA) AND for T-REx/Wikidata fetching+enrichment. This
repo's `adapters/kgdata` is the only seam between the projects: it reads
kg-data's stage-7 export and emits canonical Documents. The former
`adapters/trex` here was removed — one T-REx path, through kg-data.

## Layout

```
kg_pipeline/        schema, time normalization, labels, sanitize/dedup,
                    dual-output converters, store, registry, CLI, HTTP/path guards
adapters/kgdata/    kg-data stage-7 export (synthetic worlds + public trex/wikidata)
                    -> canonical; COMPANY: namespacing, claim_status, per-revision docs
adapters/icews/     ICEWS .tab -> canonical; CAMEO-namespace relations; .tab parser
tests/              49 tests: schema round-trips, notebook-format compatibility,
                    adapter aliases + drop accounting, registry/store, CLI end-to-end
```

## Quickstart

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest

# normalize a JSONL of raw records through an adapter
.venv/bin/python -m kg_pipeline --source icews --input rows.jsonl --out data

# bridge a kg-data export (split-suffixed outputs never clobber each other)
.venv/bin/python -m kg_pipeline --source kgdata \
    --input ../kg-data/data/synthetic --split train --out data
.venv/bin/python -m kg_pipeline --source kgdata \
    --input ../kg-data/data/public/trex --split train --out data
```

Outputs land under `--out` (with `--split`, under `<source>_<split>`):
- `normalized/<source[_split]>/docs.jsonl` (+ `manifest.json` with content
  hash, and `docs.parquet` when pyarrow is installed)
- `unified/<source[_split]>/{ner,re,el}.jsonl` — directly consumable by the
  notebook's `UnifiedDataset` builders (`_build_ner` / `_build_re` / `_build_el`)

## Datasets

| Source | Tier | License | Where | Status |
|---|---|---|---|---|
| kg-data synthetic worlds | C | generated in-repo | `../kg-data` (worldgen) | bridged ✓ — scale-up pending |
| T-REx + Wikidata qualifier enrichment | A | CC BY-SA 4.0 | `../kg-data` (relbert/t_rex + WDQS) | bridged ✓ via kgdata |
| ICEWS | A | CC BY 4.0 (verify at fetch) | Harvard Dataverse DOI `10.7910/DVN/28075` | adapter done, fetch to pin |
| Re-DocRED / DocRED, CoNLL, OntoNotes, Pile-NER, WikiANN, AIDA | A0 | various | already inline in the notebook | TODO: pin as adapters |
| MAVEN-ERE | A | CC BY-SA 4.0 (research) | github.com/THU-KEG/MAVEN-ERE | TODO |
| Enron, KEPs, GitHub issues, EDGAR | B | mixed, research-use | see plan (Enron partially in kg-data grounding) | TODO |
| TGB 2.0, TempLAMA, TimeQA | eval-only | various | libtgb.org / HF | TODO |

## Roadmap (approved plan, increments 1-2 complete + convergence)

1. [x] Scaffold + canonical schema + label registry + converter tests
2. [x] ICEWS adapter with dual output, verified vs UnifiedDataset formats
3. [ ] Pin Tier A0 as adapters; mixture registry replaces the notebook's inline lists
4. [ ] MAVEN-ERE adapter (+ REBEL when its signed agreement clears)
5. [~] Synthetic enterprise generator: EXISTS in kg-data (worldgen/messify/evalgen,
     pilot validated) — bridge done here; remaining: Enron adapter + scale-up +
     wiring kg-data's eval questions into this repo's eval harness
6. [ ] OSS proxies (KEPs, GitHub issues) + EDGAR
7. [ ] Phase 1.5 training run + regression suite
8. [ ] Serving skeleton (connectors, graph store, belief scoring)

Also pending from the plan: RE negative-pair sampling for training mixtures,
and the eval harness (temporal-bound metrics wrapping the notebook's
`evaluate_zero_shot`; kg-data's question kinds map onto it).

## Notes

- All outbound fetches go through `kg_pipeline/httpguard.py`: http/https
  only, public targets only, redirects blocked. All writes go through
  `kg_pipeline/paths.py`: no `..`, contained in an explicit allowed root,
  zip members validated before extraction.
- Wikidata-qualifier times are distant supervision (`explicit_in_text=false`);
  never treat them as text-stated dates in eval. kg-data quads carry their own
  `time_source` and the bridge maps only `explicit_in_text` to true.
- The trainer's EL labels are surface strings today; canonical docs always
  carry `el_id` (QID or synthetic entity id) so phase-2 company-KB linking
  can switch on IDs.
