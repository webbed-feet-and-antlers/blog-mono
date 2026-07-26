"""Pure-data layer for the MTEB Streamlit leaderboard.

No Streamlit imports here — keeps the data layer testable and reusable
from the evaluator or other tooling.

Public API:

- ``PRIMARY_METRICS`` — task short-name → primary metric.
- ``ModelResult`` / ``TaskResult`` — dataclasses mirroring the JSON shape.
- ``load_results(results_dir)`` — glob ``*.json``, skip ``_*``, parse each.
- ``build_leaderboard_frame(results, tasks=None)`` — wide DataFrame,
  one row per model, one column per task's primary metric.
- ``build_detail_frame(model)`` — long DataFrame of every task_result row.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


# ----- Constants ------------------------------------------------------------


# Short task name → primary metric. Short names strip the ``govreport_`` prefix
# used in the per-model JSON's ``task_results[*].task`` field. The loader
# matches both forms defensively so a future rename doesn't break rendering.
PRIMARY_METRICS: dict[str, str] = {
    "retrieval": "ndcg@10",
    "sts": "spearman",
    "summary_sts": "spearman",
    "clustering": "v_measure",
    "reranking": "map@10",
    "cross_report": "ndcg@10",
    "pair_classification": "roc_auc",
}

DEFAULT_TASK_ORDER: list[str] = [
    "retrieval",
    "cross_report",
    "sts",
    "summary_sts",
    "clustering",
    "reranking",
    "pair_classification",
]

_GOV_PREFIX = "govreport_"


def _short_task(task: str) -> str:
    """``govreport_retrieval`` → ``retrieval``. Pass-through if no prefix."""
    if task.startswith(_GOV_PREFIX):
        return task[len(_GOV_PREFIX):]
    return task


# ----- Dataclasses ----------------------------------------------------------


@dataclass(frozen=True)
class TaskResult:
    task: str           # full task name as written (e.g. "govreport_retrieval")
    metric: str
    score: float
    n_examples: int | None = None
    runtime_seconds: float | None = None


@dataclass(frozen=True)
class ModelResult:
    model: str
    provider: str
    run_at: str
    git_sha: str
    task_results: list[TaskResult] = field(default_factory=list)

    @property
    def short_to_primary(self) -> dict[str, float]:
        """``{short_task: score}`` for the primary metric of each task.

        If a task emits multiple metrics, only the primary one is kept.
        Missing metrics are simply absent from the dict.
        """
        out: dict[str, float] = {}
        for tr in self.task_results:
            short = _short_task(tr.task)
            primary_metric = PRIMARY_METRICS.get(short)
            if primary_metric is None or tr.metric != primary_metric:
                continue
            # First-seen wins (matches the markdown leaderboard's convention).
            out.setdefault(short, tr.score)
        return out


# ----- JSON loading ---------------------------------------------------------


def _parse_model_result(payload: dict) -> ModelResult | None:
    """Parse one JSON payload into a ``ModelResult``.

    Returns ``None`` if the payload is missing required fields or contains
    no usable task_results; the caller logs and skips.
    """
    model = payload.get("model")
    if not model:
        return None
    raw_tasks = payload.get("task_results") or []
    task_results: list[TaskResult] = []
    for tr in raw_tasks:
        task = tr.get("task")
        metric = tr.get("metric")
        score = tr.get("score")
        if task is None or metric is None or score is None:
            continue
        try:
            task_results.append(
                TaskResult(
                    task=str(task),
                    metric=str(metric),
                    score=float(score),
                    n_examples=tr.get("n_examples"),
                    runtime_seconds=tr.get("runtime_seconds"),
                )
            )
        except (TypeError, ValueError):
            continue
    return ModelResult(
        model=str(model),
        provider=str(payload.get("provider") or "unknown"),
        run_at=str(payload.get("run_at") or ""),
        git_sha=str(payload.get("git_sha") or ""),
        task_results=task_results,
    )


def load_results(results_dir: Path) -> list[ModelResult]:
    """Scan ``results_dir`` for per-model JSONs and return parsed records.

    - Skips files whose name starts with ``_`` (e.g. ``_leaderboard.md``,
      though that's a markdown file anyway).
    - Skips files that fail JSON parse or don't look like model results.
    - Empty list if the directory is missing or empty.
    - Sorted by model name for deterministic display.
    """
    results_dir = Path(results_dir)
    if not results_dir.is_dir():
        return []

    out: list[ModelResult] = []
    for p in sorted(results_dir.glob("*.json")):
        if p.name.startswith("_"):
            continue
        try:
            with p.open("r", encoding="utf-8") as f:
                payload = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Skipping unreadable %s: %s", p, e)
            continue
        if not isinstance(payload, dict):
            logger.warning("Skipping non-object JSON %s", p)
            continue
        parsed = _parse_model_result(payload)
        if parsed is None:
            logger.warning("Skipping %s: missing required fields", p)
            continue
        out.append(parsed)

    out.sort(key=lambda m: m.model)
    return out


# ----- Frame builders -------------------------------------------------------


def build_leaderboard_frame(
    results: list[ModelResult],
    tasks: list[str] | None = None,
) -> pd.DataFrame:
    """Wide DataFrame: one row per model, one column per task primary metric.

    - ``tasks`` filters which short-task columns to include. ``None`` means
      all of ``DEFAULT_TASK_ORDER`` that appear in ``PRIMARY_METRICS``.
    - Always includes a ``model`` and ``provider`` column.
    - Column header for each task is the metric in parentheses, e.g.
      ``Retrieval (ndcg@10)`` — keeps the table self-describing and matches
      the markdown leaderboard's labeling.
    - Missing scores render as ``NaN`` so callers can format them as ``—``.
    """
    selected = tasks if tasks is not None else DEFAULT_TASK_ORDER
    # Preserve caller order but drop anything not in PRIMARY_METRICS.
    columns: list[tuple[str, str]] = []
    for short in selected:
        metric = PRIMARY_METRICS.get(short)
        if metric is not None:
            columns.append((short, metric))

    # Pretty labels match the markdown leaderboard's column headers so users
    # can cross-reference the two views without translation.
    _PRETTY = {
        "retrieval": "Retrieval",
        "cross_report": "Cross-Report",
        "sts": "STS",
        "summary_sts": "Summary STS",
        "clustering": "Clustering",
        "reranking": "Reranking",
        "pair_classification": "Pair Class.",
    }

    def _label(short: str, metric: str) -> str:
        pretty = _PRETTY.get(short, short.replace("_", " ").title())
        return f"{pretty} ({metric})"

    rows: list[dict[str, object]] = []
    for m in results:
        row: dict[str, object] = {
            "Model": m.model,
            "Provider": m.provider,
        }
        primary = m.short_to_primary
        for short, metric in columns:
            row[_label(short, metric)] = primary.get(short)
        rows.append(row)

    col_order = ["Model", "Provider"] + [_label(s, m) for s, m in columns]
    frame = pd.DataFrame(rows, columns=col_order)
    # Coerce metric columns to numeric so missing cells are NaN (not None)
    # and downstream styler/highlight_max/sort_values behave predictably.
    for col in col_order[2:]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame


def build_detail_frame(model: ModelResult) -> pd.DataFrame:
    """Long DataFrame: one row per ``task_result``.

    Columns: ``task, metric, score, n_examples, runtime_seconds``.
    Sorted by task then metric for stable display.
    """
    rows = [
        {
            "task": tr.task,
            "metric": tr.metric,
            "score": tr.score,
            "n_examples": tr.n_examples,
            "runtime_seconds": tr.runtime_seconds,
        }
        for tr in model.task_results
    ]
    frame = pd.DataFrame(
        rows,
        columns=["task", "metric", "score", "n_examples", "runtime_seconds"],
    )
    if not frame.empty:
        frame = frame.sort_values(["task", "metric"]).reset_index(drop=True)
    return frame
