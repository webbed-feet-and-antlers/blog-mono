"""CLI entry point for the embedding evaluator.

Usage::

    # Evaluate one model on all 7 tasks
    python3 mteb/evaluate/__main__.py \\
        --provider openai --model text-embedding-3-small

    # Restrict to specific tasks
    python3 mteb/evaluate/__main__.py --provider openai \\
        --model text-embedding-3-small --tasks retrieval,sts

    # Full matrix (all configured models)
    python3 mteb/evaluate/__main__.py --all

    # Local model with GPU
    python3 mteb/evaluate/__main__.py \\
        --provider sentence-transformers --model bge-base-en-v1.5 --device cuda
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Ensure mteb/ is on sys.path when run as a script (``python3 mteb/evaluate/__main__.py``).
_HERE = Path(__file__).resolve().parent
_MTEB_DIR = _HERE.parent
if str(_MTEB_DIR) not in sys.path:
    sys.path.insert(0, str(_MTEB_DIR))

from scripts.common import DATASETS_DIR  # noqa: E402

from evaluate.run import run_all, run_one  # noqa: E402
from evaluate.tasks import ALL_TASKS  # noqa: E402

logger = logging.getLogger("evaluate")


DEFAULT_RESULTS_DIR = _MTEB_DIR / "results"
DEFAULT_CACHE_DIR = _MTEB_DIR / "cache" / "embeddings"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m evaluate",
        description="Evaluate embedding models on the 7 GovReport MTEB tasks.",
    )
    p.add_argument(
        "--provider",
        choices=("openai", "gemini", "sentence-transformers"),
        help="Embedding provider (required unless --all).",
    )
    p.add_argument("--model", help="Model name (provider-specific). Required unless --all.")
    p.add_argument(
        "--tasks",
        default=",".join(ALL_TASKS),
        help=f"Comma-separated task names (default: all 7). Choices: {list(ALL_TASKS)}",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="Run the full MODEL_MATRIX (one process per model).",
    )
    p.add_argument(
        "--device",
        default=os.environ.get("EVAL_DEVICE", "cpu"),
        help="sentence-transformers device: cpu | cuda | mps (default: cpu)",
    )
    p.add_argument(
        "--api-key",
        default=None,
        help="Override provider API key (else OPENAI_API_KEY / GOOGLE_API_KEY env).",
    )
    p.add_argument(
        "--dim",
        type=int,
        default=None,
        help="Embedding dim override (auto-detected from MODEL_MATRIX by default).",
    )
    p.add_argument(
        "--datasets-dir",
        type=Path,
        default=DATASETS_DIR,
        help=f"Path to datasets/ folder (default: {DATASETS_DIR})",
    )
    p.add_argument(
        "--results-dir",
        type=Path,
        default=DEFAULT_RESULTS_DIR,
        help=f"Path to results/ folder (default: {DEFAULT_RESULTS_DIR})",
    )
    p.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass the on-disk embedding cache (re-encode everything).",
    )
    p.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help=f"Embedding cache root (default: {DEFAULT_CACHE_DIR})",
    )
    p.add_argument(
        "--verbose", action="store_true",
        help="DEBUG logging",
    )
    return p


def _parse_tasks(raw: str) -> list[str]:
    out: list[str] = []
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if tok not in ALL_TASKS:
            sys.exit(f"Unknown task {tok!r}. Choices: {list(ALL_TASKS)}")
        out.append(tok)
    if not out:
        sys.exit("No tasks selected — check --tasks")
    return out


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stderr,
    )


def main() -> None:
    args = build_parser().parse_args()
    setup_logging(args.verbose)

    if args.all:
        count = run_all(
            datasets_dir=args.datasets_dir,
            results_dir=args.results_dir,
            device=args.device,
            no_cache=args.no_cache,
            cache_dir=args.cache_dir,
        )
        logger.info("Evaluated %d model(s)", count)
        return

    if not args.provider or not args.model:
        sys.exit("Either --all or both --provider and --model are required.")

    tasks = _parse_tasks(args.tasks)
    results = run_one(
        provider=args.provider,
        model=args.model,
        tasks=tasks,
        datasets_dir=args.datasets_dir,
        results_dir=args.results_dir,
        device=args.device,
        api_key=args.api_key,
        dim=args.dim,
        no_cache=args.no_cache,
        cache_dir=args.cache_dir,
    )
    logger.info("Done: %d task results", len(results))


if __name__ == "__main__":
    main()
