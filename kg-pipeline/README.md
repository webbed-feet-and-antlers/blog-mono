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
that pass verbatim into the trainer's `[REL] …` label space. The crucial
temporal distinction is `explicit_in_text`: T-REx times come from Wikidata
qualifiers (not stated in the text), ICEWS/MAVEN-ERE/Enron times are event
dates — two different supervision regimes that eval must separate.

## Layout

```
kg_pipeline/        schema, time normalization, labels, sanitize/dedup,
                    dual-output converters, store, registry, CLI, HTTP/path guards
adapters/trex/      T-REx -> canonical; P580/P582 qualifier enrichment (SPARQL
                    query builder + cache loader; network fetch stubs included)
adapters/icews/     ICEWS .tab -> canonical; CAMEO-namespace relations; .tab parser
tests/              44 tests: schema round-trips, notebook-format compatibility,
                    adapter aliases + drop accounting, registry/store, CLI end-to-end
```

## Quickstart

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest

# smoke: normalize a JSONL of raw records through an adapter
.venv/bin/python -m kg_pipeline --source icews --input rows.jsonl --out data
.venv/bin/python -m kg_pipeline --source trex --input records.jsonl \
    --qualifiers qualifiers.json --out data
```

Outputs land under `--out`:
- `normalized/<source>/docs.jsonl` (+ `manifest.json` with content hash, and
  `docs.parquet` when pyarrow is installed)
- `unified/<source>/{ner,re,el}.jsonl` — directly consumable by the
  notebook's `UnifiedDataset` builders (`_build_ner` / `_build_re` / `_build_el`)

## Datasets

| Source | Tier | License | Where | Status |
|---|---|---|---|---|
| T-REx + Wikidata qualifiers | A | CC BY-SA 4.0 | https://hadyelsahar.io/t-rex/ (HF mirror `QinyuanWu/T-Rex-MC`) | adapter done, fetch to pin |
| ICEWS | A | CC BY 4.0 (verify at fetch) | Harvard Dataverse DOI `10.7910/DVN/28075` | adapter done, fetch to pin |
| Re-DocRED / DocRED, CoNLL, OntoNotes, Pile-NER, WikiANN, AIDA | A0 | various | already inline in the notebook | TODO: pin as adapters |
| MAVEN-ERE | A | CC BY-SA 4.0 (research) | github.com/THU-KEG/MAVEN-ERE | TODO |
| Enron, KEPs, GitHub issues, EDGAR | B | mixed, research-use | see plan | TODO |
| Synthetic enterprise lifecycle | C | own generation | OpenRouter LLM generation | TODO |
| TGB 2.0, TempLAMA, TimeQA | eval-only | various | libtgb.org / HF | TODO |

## Roadmap (approved plan, increment 1 of 8 complete)

1. [x] Scaffold + canonical schema + label registry + converter tests
2. [x] T-REx + ICEWS adapters with dual output, verified vs UnifiedDataset formats
3. [ ] Pin Tier A0 as adapters; mixture registry replaces the notebook's inline lists
4. [ ] MAVEN-ERE adapter (+ REBEL when its signed agreement clears)
5. [ ] Synthetic enterprise generator v0 + Enron adapter + staleness QA harness
6. [ ] OSS proxies (KEPs, GitHub issues) + EDGAR
7. [ ] Phase 1.5 training run + regression suite
8. [ ] Serving skeleton (connectors, graph store, belief scoring)

Also pending from the plan: RE negative-pair sampling for training mixtures,
and the eval harness (temporal-bound metrics wrapping the notebook's
`evaluate_zero_shot`).

## Notes

- All outbound fetches go through `kg_pipeline/httpguard.py`: http/https
  only, public targets only, redirects blocked. All writes go through
  `kg_pipeline/paths.py`: no `..`, contained in an explicit allowed root,
  zip members validated before extraction.
- T-REx qualifier times are distant supervision (`explicit_in_text=false`);
  never treat them as text-stated dates in eval.
- The trainer's EL labels are surface strings today; canonical docs always
  carry `el_id` (QID) so phase-2 company-KB linking can switch on IDs.
