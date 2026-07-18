"""Orchestrator: run stages 1 → 2 → 3 via subprocess.

Stage modules have filenames starting with digits (``01_chunk_reports.py``
etc.) so they can't be imported normally. We run them as subprocesses so each
stage's argparse and asyncio loop are cleanly isolated.

Usage::

    python -m scripts --subset 5
    python -m scripts --stage 2        # run only stage 2 (assumes 1 is done)
    python -m scripts                  # run all stages with defaults
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
STAGES = [
    ("stage-1 chunk", SCRIPTS_DIR / "01_chunk_reports.py"),
    ("stage-2 queries", SCRIPTS_DIR / "02_generate_queries.py"),
    ("stage-3 build", SCRIPTS_DIR / "03_build_retrieval.py"),
]


def parse_args() -> argparse.Namespace:
    """Parse orchestrator args. We accept a small subset and forward the rest."""
    p = argparse.ArgumentParser(
        prog="python -m scripts",
        description="Run all 3 MTEB pipeline stages end-to-end.",
    )
    p.add_argument(
        "--subset", type=int, default=int(os.environ.get("MTEB_SUBSET", "50")),
        help="Reports to process (forwarded to stages 1; default: 50)",
    )
    p.add_argument(
        "--model", default=os.environ.get("MTEB_MODEL", "deepseek-chat"),
        help="DeepSeek model id (default: deepseek-chat)",
    )
    p.add_argument(
        "--concurrency", type=int,
        default=int(os.environ.get("MTEB_CONCURRENCY", "10")),
        help="Max parallel API calls (default: 10)",
    )
    p.add_argument(
        "--split", default=os.environ.get("MTEB_SPLIT", "train"),
        help="GovReport split (default: train)",
    )
    p.add_argument(
        "--stage", type=int, choices=(1, 2, 3), default=None,
        help="Run only this stage (default: run all)",
    )
    p.add_argument("--verbose", action="store_true", help="DEBUG logging")
    p.add_argument("--no-cache", action="store_true", help="Bypass cache reads")
    p.add_argument(
        "--restart", action="store_true",
        help="Truncate stage outputs before running instead of resuming "
             "(use when you change --model or prompts)",
    )
    p.add_argument("--api-key", default=os.environ.get("DEEPSEEK_API_KEY", ""))
    return p.parse_args()


def run_stage(label: str, script: Path, args: argparse.Namespace) -> None:
    """Run a single stage as a subprocess, inheriting stdio."""
    print(f"\n=== {label} ({script.name}) ===", file=sys.stderr)

    cmd = [sys.executable, str(script)]
    # Forward common flags. Stages 1 and 2 use --subset/--model/--concurrency/--split;
    # stage 3 ignores them (it has no LLM). Forwarding extra flags is harmless.
    cmd += ["--model", args.model]
    cmd += ["--concurrency", str(args.concurrency)]
    if args.split:
        cmd += ["--split", args.split]
    if args.verbose:
        cmd += ["--verbose"]
    if args.no_cache:
        cmd += ["--no-cache"]
    if args.restart:
        cmd += ["--restart"]
    if args.api_key:
        cmd += ["--api-key", args.api_key]

    # Stage 1 takes --subset.
    if "01_" in script.name:
        cmd += ["--subset", str(args.subset)]

    print(f"  cmd: {' '.join(cmd)}", file=sys.stderr)
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        sys.exit(f"{label} failed with exit code {result.returncode}")


def main() -> None:
    args = parse_args()
    stages = STAGES if args.stage is None else [STAGES[args.stage - 1]]
    for label, script in stages:
        if not script.is_file():
            sys.exit(f"Stage script not found: {script}")
        run_stage(label, script, args)
    print("\nAll requested stages completed.", file=sys.stderr)


if __name__ == "__main__":
    main()
