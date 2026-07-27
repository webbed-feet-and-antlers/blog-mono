"""Run a single model on selected tasks and write results to disk.

Entry point used by ``__main__.py``. Loads the dataset dir, builds the encoder,
runs each task, writes the per-model JSON, then regenerates the leaderboard.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .cache import CachedEncoder
from .encoders import Encoder, MODEL_MATRIX, build_encoder
from .results import regenerate_leaderboard, write_model_result
from .tasks import ALL_TASKS, TASK_FUNCS, TaskResult

logger = logging.getLogger(__name__)


def run_one(
    *,
    provider: str,
    model: str,
    tasks: list[str],
    datasets_dir: Path,
    results_dir: Path,
    device: str = "cpu",
    api_key: str | None = None,
    dim: int | None = None,
    no_cache: bool = False,
    cache_dir: Path | None = None,
    precomputed_dir: Path | None = None,
) -> list[TaskResult]:
    """Evaluate one model on *tasks*; write JSON + refresh leaderboard.

    Returns the flattened list of :class:`TaskResult` for the caller (e.g. CLI
    pretty-printing).

    When *no_cache* is False (default), the encoder is wrapped in a
    :class:`CachedEncoder` so re-runs don't re-call the provider for inputs
    already encoded. *cache_dir* defaults to ``<datasets_dir>/../cache/embeddings``
    (matches the CLI default of ``mteb/cache/embeddings``).
    """
    datasets_dir = Path(datasets_dir).resolve()
    results_dir = Path(results_dir).resolve()
    if cache_dir is None:
        # Same default as the CLI: embeddings/mteb/cache/embeddings/
        cache_dir = datasets_dir.parent / "cache" / "embeddings"
    cache_dir = Path(cache_dir)

    unknown = [t for t in tasks if t not in TASK_FUNCS]
    if unknown:
        raise ValueError(
            f"Unknown task(s): {unknown}. Known: {list(TASK_FUNCS)}"
        )

    logger.info(
        "Building encoder %s/%s (device=%s)", provider, model, device
    )
    encoder: Encoder = build_encoder(
        provider, model, dim=dim, device=device, api_key=api_key,
        precomputed_dir=precomputed_dir,
    )
    if not no_cache:
        logger.info("Caching embeddings at %s", cache_dir)
        encoder = CachedEncoder(encoder, cache_root=cache_dir)  # type: ignore[assignment]

    try:
        all_results: list[TaskResult] = []
        for task in tasks:
            logger.info("=== %s / %s ===", encoder.name, task)
            fn = TASK_FUNCS[task]
            results = fn(datasets_dir, encoder)
            if not results:
                logger.warning("No results for %s — skipping", task)
                continue
            for r in results:
                logger.info(
                    "  %s %s = %.4f (n=%d, %.2fs)",
                    r.task, r.metric, r.score, r.n_examples, r.runtime_seconds,
                )
                all_results.append(r)

        write_model_result(
            results_dir,
            model_name=encoder.name,
            task_results=all_results,
        )
        regenerate_leaderboard(results_dir)
        return all_results
    finally:
        if isinstance(encoder, CachedEncoder):
            encoder.close()


def run_all(
    *,
    datasets_dir: Path,
    results_dir: Path,
    device: str = "cpu",
    tasks: list[str] | None = None,
    no_cache: bool = False,
    cache_dir: Path | None = None,
    precomputed_dir: Path | None = None,
) -> int:
    """Run the entire MODEL_MATRIX. Returns count of models evaluated."""
    selected = tasks or list(ALL_TASKS)
    count = 0
    for provider, model, _dim in MODEL_MATRIX:
        logger.info("########## %s / %s ##########", provider, model)
        try:
            run_one(
                provider=provider,
                model=model,
                tasks=selected,
                datasets_dir=datasets_dir,
                results_dir=results_dir,
                device=device,
                no_cache=no_cache,
                cache_dir=cache_dir,
                precomputed_dir=precomputed_dir,
            )
            count += 1
        except SystemExit as e:
            # Missing API key — log and continue with the next provider.
            logger.warning("Skipping %s/%s: %s", provider, model, e)
        except Exception as e:  # pragma: no cover — defensive
            logger.exception("Failed to evaluate %s/%s: %s", provider, model, e)
    return count
