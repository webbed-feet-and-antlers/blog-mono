"""Per-model JSON writer + leaderboard markdown regeneration.

Layout (under ``embeddings/mteb/results/``)::

    <provider>_<model>.json   one per evaluated model
    _leaderboard.md           regenerated on every run by scanning all *.json

Per-model JSON schema::

    {
      "model": "openai/text-embedding-3-small",
      "provider": "openai",
      "run_at": "2026-07-18T19:30:00Z",
      "git_sha": "abc1234",
      "task_results": [
        {"task": "govreport_retrieval", "metric": "ndcg@10",
         "score": 0.612, "n_examples": 300, "runtime_seconds": 14.3},
        ...
      ]
    }
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .tasks import TaskResult

logger = logging.getLogger(__name__)


# ----- Filename helpers -----------------------------------------------------


def _sanitize(name: str) -> str:
    """``openai/text-embedding-3-small`` → ``openai_text-embedding-3-small``.

    Slashes are illegal in filenames; everything else legal as-is.
    """
    return name.replace("/", "_")


def provider_of(model_name: str) -> str:
    """``openai/text-embedding-3-small`` → ``openai``."""
    return model_name.split("/", 1)[0] if "/" in model_name else "unknown"


def result_filename(model_name: str) -> str:
    """``openai/text-embedding-3-small`` → ``openai_text-embedding-3-small.json``."""
    return f"{_sanitize(model_name)}.json"


# ----- Per-model JSON writer ------------------------------------------------


def _current_git_sha() -> str:
    """Best-effort short git SHA. Returns ``"unknown"`` if git fails."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return "unknown"


def write_model_result(
    results_dir: Path,
    *,
    model_name: str,
    task_results: list[TaskResult],
    run_at: datetime | None = None,
    git_sha: str | None = None,
) -> Path:
    """Write (overwrite) the per-model JSON. Returns the written path."""
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "model": model_name,
        "provider": provider_of(model_name),
        "run_at": (run_at or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_sha": git_sha if git_sha is not None else _current_git_sha(),
        "task_results": [tr.to_dict() for tr in task_results],
    }

    path = results_dir / result_filename(model_name)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)
    logger.info("Wrote %s (%d task results)", path.name, len(task_results))
    return path


# ----- Leaderboard regeneration --------------------------------------------


# Map of (task_key, metric) → leaderboard column header. ``task_key`` is the
# short name used in MODEL_MATRIX / CLI; we look up the matching (task, metric)
# pair from each model's task_results.
DEFAULT_COLUMNS: list[tuple[str, str, str]] = [
    ("retrieval", "ndcg@10", "Retrieval (NDCG@10)"),
    ("cross_report", "ndcg@10", "Cross-Report (NDCG@10)"),
    ("sts", "spearman", "STS (Spearman)"),
    ("summary_sts", "spearman", "Summary STS (Spearman)"),
    ("clustering", "v_measure", "Clustering (V-measure)"),
    ("reranking", "map@10", "Reranking (MAP@10)"),
    ("pair_classification", "roc_auc", "Pair Class. (ROC-AUC)"),
]


def _scan_models(results_dir: Path) -> list[dict]:
    """Read every per-model JSON. Returns sorted by model name."""
    out: list[dict] = []
    for p in sorted(results_dir.glob("*.json")):
        # Skip non-model files (e.g. metadata).
        if p.name.startswith("_"):
            continue
        try:
            with p.open("r", encoding="utf-8") as f:
                out.append(json.load(f))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Skipping unreadable %s: %s", p, e)
    out.sort(key=lambda d: d.get("model", ""))
    return out


def regenerate_leaderboard(
    results_dir: Path,
    *,
    columns: list[tuple[str, str, str]] | None = None,
) -> Path:
    """Scan all per-model JSONs and write ``_leaderboard.md``. Returns its path."""
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    cols = columns or DEFAULT_COLUMNS

    models = _scan_models(results_dir)

    # Index each model's task_results by (task, metric) → score.
    indexed: list[tuple[str, dict[tuple[str, str], float]]] = []
    for m in models:
        table: dict[tuple[str, str], float] = {}
        for tr in m.get("task_results", []):
            task_full = tr.get("task", "")  # e.g. "govreport_retrieval"
            metric = tr.get("metric", "")
            score = tr.get("score")
            if not task_full or not metric or score is None:
                continue
            # Strip "govreport_" prefix to get the short key.
            short = (
                task_full[len("govreport_"):]
                if task_full.startswith("govreport_")
                else task_full
            )
            # Keep the first-seen score per (short, metric) — matches DEFAULT_COLUMNS.
            table.setdefault((short, metric), float(score))
        indexed.append((m.get("model", "?"), table))

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = []
    lines.append("# Embedding Model Leaderboard")
    lines.append("")
    lines.append(f"Last updated: {now}")
    lines.append("")
    header = "| Model | " + " | ".join(c[2] for c in cols) + " |"
    sep = "|---" * (len(cols) + 1) + "|"
    lines.append(header)
    lines.append(sep)
    for model_name, table in indexed:
        cells: list[str] = [model_name]
        for short, metric, _label in cols:
            v = table.get((short, metric))
            cells.append("—" if v is None else f"{v:.3f}")
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lines.append(
        "Each cell is the metric produced by `python -m evaluate` for that "
        "model+task. Empty cells (—) indicate the task was skipped (missing "
        "dataset) or not run. Re-running `task mteb:evaluate:all` regenerates "
        "this file."
    )
    lines.append("")

    out_path = results_dir / "_leaderboard.md"
    with out_path.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info("Wrote %s (%d models)", out_path.name, len(indexed))
    return out_path
