"""Orchestrator: run MTEB pipeline stages via subprocess.

Stage modules have filenames starting with digits (``01_chunk_reports.py``
etc.) so they can't be imported normally. We run them as subprocesses so each
stage's argparse and asyncio loop are cleanly isolated.

Usage::

    python -m scripts --subset 5
    python -m scripts --stage 2            # run only stage 2 (legacy; --task preferred)
    python -m scripts --task sts           # run the STS pipeline (stage 1 → 4)
    python -m scripts --task retrieval     # default: stages 1 → 2 → 3
    python -m scripts --task all           # retrieval + all 6 new tasks
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent

# Each stage's script and its output marker (used to skip when resuming).
STAGE_SCRIPTS: dict[int, Path] = {
    1: SCRIPTS_DIR / "01_chunk_reports.py",
    2: SCRIPTS_DIR / "02_generate_queries.py",
    3: SCRIPTS_DIR / "03_build_retrieval.py",
    4: SCRIPTS_DIR / "04_sts.py",
    5: SCRIPTS_DIR / "05_summary_sts.py",
    6: SCRIPTS_DIR / "06_clustering.py",
    7: SCRIPTS_DIR / "07_reranking.py",
    8: SCRIPTS_DIR / "08_cross_report.py",
    9: SCRIPTS_DIR / "09_pair_classification.py",
}

STAGE_OUTPUT_FILES: dict[int, Path] = {
    1: SCRIPTS_DIR.parent / "intermediate" / "chunks.jsonl",
    2: SCRIPTS_DIR.parent / "intermediate" / "queries.jsonl",
    3: SCRIPTS_DIR.parent / "datasets" / "govreport_retrieval" / "corpus.jsonl",
    4: SCRIPTS_DIR.parent / "intermediate" / "sts_pairs.jsonl",
    5: SCRIPTS_DIR.parent / "intermediate" / "summary_sts_pairs.jsonl",
    6: SCRIPTS_DIR.parent / "intermediate" / "topics.jsonl",
    7: SCRIPTS_DIR.parent / "intermediate" / "reranking_scores.jsonl",
    8: SCRIPTS_DIR.parent / "intermediate" / "cross_report_qrels.jsonl",
    9: SCRIPTS_DIR.parent / "intermediate" / "pair_classification.jsonl",
}

STAGE_LABELS: dict[int, str] = {
    1: "stage-1 chunk",
    2: "stage-2 queries",
    3: "stage-3 retrieval-build",
    4: "stage-4 sts",
    5: "stage-5 summary-sts",
    6: "stage-6 clustering",
    7: "stage-7 reranking",
    8: "stage-8 cross-report",
    9: "stage-9 pair-classification",
}

# Mapping of --task value → ordered list of stage numbers to run.
TASK_PIPELINES: dict[str, list[int]] = {
    "retrieval":           [1, 2, 3],
    "sts":                 [1, 4],
    "summary_sts":         [1, 5],
    "clustering":          [1, 6],
    "reranking":           [1, 2, 7],
    "cross_report":        [1, 2, 8],
    "pair_classification": [1, 9],
    "all":                 [1, 2, 3, 4, 5, 6, 7, 8, 9],
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m scripts",
        description="Run MTEB pipeline stages end-to-end.",
    )
    p.add_argument(
        "--subset", type=int, default=int(os.environ.get("MTEB_SUBSET", "50")),
        help="Reports to process (forwarded to stages 1, 4, 5, 6, 9; default: 50)",
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
        "--task", choices=sorted(TASK_PIPELINES.keys()), default="retrieval",
        help="Which pipeline to run (default: retrieval). "
             "'all' = retrieval + all 6 new tasks.",
    )
    p.add_argument(
        "--stage", type=int, choices=tuple(STAGE_SCRIPTS.keys()), default=None,
        help="Run only this single stage (overrides --task). For backwards compat.",
    )
    p.add_argument("--verbose", action="store_true", help="DEBUG logging")
    p.add_argument("--no-cache", action="store_true", help="Bypass cache reads")
    p.add_argument(
        "--restart", action="store_true",
        help="Truncate stage outputs before running instead of resuming "
             "(use when you change --model or prompts)",
    )
    p.add_argument(
        "--skip-existing", action="store_true", default=True,
        help="Skip stages whose output file already exists (resume-aware). "
             "Default on; pass --no-skip-existing to force re-run.",
    )
    p.add_argument(
        "--no-skip-existing", dest="skip_existing", action="store_false",
        help="Disable resume-aware skipping — run every stage in --task even if output exists.",
    )
    p.add_argument("--api-key", default=os.environ.get("DEEPSEEK_API_KEY", ""))
    return p.parse_args()


def stage_takes_subset(stage: int) -> bool:
    """Stages that load GovReport directly take --subset; others don't."""
    return stage in (1, 4, 5, 6, 9)


def run_stage(stage: int, args: argparse.Namespace) -> bool:
    """Run a single stage as a subprocess. Returns True if it ran, False if skipped."""
    script = STAGE_SCRIPTS[stage]
    label = STAGE_LABELS[stage]
    if not script.is_file():
        sys.exit(f"Stage script not found: {script}")

    # Resume-aware skip: if the stage's output already exists and we're not
    # forcing a restart, treat the stage as done. Stages themselves also
    # support resume at finer granularity (per-row).
    out_file = STAGE_OUTPUT_FILES.get(stage)
    if args.skip_existing and not args.restart and out_file and out_file.is_file():
        print(
            f"\n=== {label}: skipping (output exists: {out_file.name}) ===",
            file=sys.stderr,
        )
        return False

    print(f"\n=== {label} ({script.name}) ===", file=sys.stderr)
    cmd: list[str] = [sys.executable, str(script)]
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
    if stage_takes_subset(stage):
        cmd += ["--subset", str(args.subset)]

    print(f"  cmd: {' '.join(cmd)}", file=sys.stderr)
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        sys.exit(f"{label} failed with exit code {result.returncode}")
    return True


def main() -> None:
    args = parse_args()
    if args.stage is not None:
        stages = [args.stage]
    else:
        stages = TASK_PIPELINES[args.task]

    ran_any = False
    for stage in stages:
        if run_stage(stage, args):
            ran_any = True

    if ran_any:
        print("\nAll requested stages completed.", file=sys.stderr)
    else:
        print("\nAll stages already complete (nothing to do).", file=sys.stderr)


if __name__ == "__main__":
    main()
