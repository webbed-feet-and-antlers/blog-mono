# Streamlit MTEB Leaderboard

Read-only interactive viewer over the per-model JSON files written by
`mteb/evaluate/results.py`. The markdown `_leaderboard.md` remains the
canonical artifact for PR diffs; this app is just a viewer over the same
JSON files.

## Quickstart

```bash
cd embeddings
uv pip install -e ".[leaderboard]"
task leaderboard:run
# or: streamlit run mteb/leaderboard/app.py
```

The app expects results at `mteb/results/<provider>_<model>.json`. With no
JSONs present it renders an empty-state info banner.

## Layout

```
mteb/leaderboard/
├── __init__.py     # empty
├── app.py          # Streamlit entrypoint + all UI
├── loader.py       # pure functions: scan results dir, parse JSON, shape tables
└── README.md       # this file
```

`loader.py` contains no Streamlit imports — it's the data layer and is
testable in isolation.

## Data shape

Each JSON file mirrors this schema (see `mteb/results/README.md` for the
authoritative version):

```json
{
  "model": "openai/text-embedding-3-small",
  "provider": "openai",
  "run_at": "2026-07-18T19:30:00Z",
  "git_sha": "abc1234",
  "task_results": [
    {"task": "govreport_retrieval", "metric": "ndcg@10",
     "score": 0.612, "n_examples": 300, "runtime_seconds": 14.3}
  ]
}
```

### Primary metric per task

The main leaderboard table shows one column per task family, using its
**primary** metric. Secondary metrics (e.g. `map@10`, `recall@5`,
`accuracy@0.5`) appear in the per-model detail view.

| Short task           | Full task name                      | Primary metric |
|----------------------|-------------------------------------|----------------|
| `retrieval`          | `govreport_retrieval`               | `ndcg@10`      |
| `cross_report`       | `govreport_cross_report`            | `ndcg@10`      |
| `sts`                | `govreport_sts`                     | `spearman`     |
| `summary_sts`        | `govreport_summary_sts`             | `spearman`     |
| `clustering`         | `govreport_clustering`              | `v_measure`    |
| `reranking`          | `govreport_reranking`               | `map@10`       |
| `pair_classification`| `govreport_pair_classification`     | `roc_auc`      |

This list lives in `loader.PRIMARY_METRICS`.

## Filters

- **Providers** — multiselect; defaults to all providers found on disk.
- **Tasks** — multiselect; defaults to all 7 task families.
- **Refresh** — re-globs the results directory without restarting the app
  (Streamlit does not watch the filesystem).

## Out of scope (matches the plan)

- No historical comparison across git SHAs. Per-model JSONs are
  overwritten in place; `git log` is the history layer.
- No composite/mean scores. Raw metrics only.
- No charts. The user picked "Table + filters".
- No write-back. The app is strictly read-only over `mteb/results/`.

## Dev notes

- Streamlit is not a runtime dependency of `embeddings` proper — install
  it via the `leaderboard` extra (`uv pip install -e ".[leaderboard]"`).
- The main table highlights the max score per column. NaN cells display
  as `—`.
- `loader.py` imports `pandas` unconditionally. If you want to use it
  somewhere pandas isn't available, factor out the parsing first.
