"""Shared paths, logging, and CLI argument parsing for the MTEB pipeline."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

# Repo-local paths. The scripts may be invoked from anywhere; we resolve
# relative to this file so cwd doesn't matter.
SCRIPTS_DIR = Path(__file__).resolve().parent
MTEB_DIR = SCRIPTS_DIR.parent                      # embeddings/mteb/
EMBEDDINGS_DIR = MTEB_DIR.parent                   # embeddings/

INTERMEDIATE_DIR = MTEB_DIR / "intermediate"
CACHE_DIR = MTEB_DIR / "cache"
DATASETS_DIR = MTEB_DIR / "datasets"

CHUNKS_JSONL = INTERMEDIATE_DIR / "chunks.jsonl"
QUERIES_JSONL = INTERMEDIATE_DIR / "queries.jsonl"
FAILURES_JSONL = INTERMEDIATE_DIR / "_failures.jsonl"

# Per-task intermediate files (stages 04-09).
STS_PAIRS_JSONL = INTERMEDIATE_DIR / "sts_pairs.jsonl"
SUMMARY_STS_PAIRS_JSONL = INTERMEDIATE_DIR / "summary_sts_pairs.jsonl"
TOPICS_JSONL = INTERMEDIATE_DIR / "topics.jsonl"
RERANKING_SCORES_JSONL = INTERMEDIATE_DIR / "reranking_scores.jsonl"
CROSS_REPORT_QRELS_JSONL = INTERMEDIATE_DIR / "cross_report_qrels.jsonl"
PAIR_CLASSIFICATION_JSONL = INTERMEDIATE_DIR / "pair_classification.jsonl"


def ensure_dirs() -> None:
    """Create the intermediate/cache/datasets directories if missing."""
    for d in (INTERMEDIATE_DIR, CACHE_DIR, DATASETS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def setup_logging(verbose: bool = False) -> None:
    """Configure root logging. DEBUG if --verbose, else INFO."""
    level = logging.DEBUG if verbose else logging.INFO
    # Re-configuring basicConfig is a no-op after the first call in some
    # interpreters; force it by removing existing handlers.
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stderr,
    )


@dataclass
class CommonArgs:
    """Resolved common argument values."""

    subset: int
    model: str
    concurrency: int
    api_key: str
    split: str
    verbose: bool
    no_cache: bool


def add_common_args(parser: argparse.ArgumentParser) -> None:
    """Attach the standard MTEB flags to *parser*.

    Env-var fallbacks are honoured; explicit CLI flags win.
    """
    parser.add_argument(
        "--subset", type=int,
        default=int(os.environ.get("MTEB_SUBSET", "50")),
        help="Reports to process, taken proportionally across splits (env: MTEB_SUBSET, default: 50)",
    )
    parser.add_argument(
        "--model", type=str,
        default=os.environ.get("MTEB_MODEL", "deepseek-chat"),
        help="DeepSeek model id (env: MTEB_MODEL, default: deepseek-chat)",
    )
    parser.add_argument(
        "--concurrency", type=int,
        default=int(os.environ.get("MTEB_CONCURRENCY", "10")),
        help="Max parallel API calls (env: MTEB_CONCURRENCY, default: 10)",
    )
    parser.add_argument(
        "--api-key", type=str,
        default=os.environ.get("DEEPSEEK_API_KEY", ""),
        help="DeepSeek API key (env: DEEPSEEK_API_KEY)",
    )
    parser.add_argument(
        "--split", type=str,
        default=os.environ.get("MTEB_SPLIT", "train"),
        help="GovReport split to use, e.g. train/validation/test (env: MTEB_SPLIT, default: train)",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Enable DEBUG logging",
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Bypass cache reads (still writes)",
    )
    parser.add_argument(
        "--restart", action="store_true",
        help="Truncate this stage's intermediate output(s) before running, "
             "instead of resuming. Use when you change --model or prompts.",
    )


def resolve_api_key(api_key: str | None) -> str:
    """Return a non-empty API key or sys.exit() with a helpful message."""
    key = (api_key or os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        sys.exit(
            "Set DEEPSEEK_API_KEY (see mteb/.env.example) "
            "or pass --api-key on the command line."
        )
    return key
