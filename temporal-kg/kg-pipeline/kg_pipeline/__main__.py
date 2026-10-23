"""CLI: normalize raw records or a kg-data export through an adapter.

Examples:
    python -m kg_pipeline --source icews --input rows.jsonl --out data
    python -m kg_pipeline --source kgdata --input ../kg-data/data/synthetic \
        --split train --out data

Adapters may expose load_input(path, split) to merge their own on-disk
exports (the kgdata bridge does); otherwise --input is a JSONL of raw
records. With --split, outputs land under <source>_<split> so train/val/test
never overwrite each other.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

from .paths import safe_name, safe_out_dir
from .store import write_docs
from .unified import write_unified
from .verify import dedup_quads, sanitize_docs

REPO_ROOT = Path(__file__).resolve().parents[1]
ADAPTERS_DIR = REPO_ROOT / "adapters"


def load_adapter(source: str):
    mod_path = ADAPTERS_DIR / safe_name(source) / "normalize.py"
    if not mod_path.exists():
        raise SystemExit(f"no adapter for source {source!r} in {ADAPTERS_DIR}")
    spec = importlib.util.spec_from_file_location(f"adapters_{source}_normalize", mod_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="kg_pipeline")
    parser.add_argument(
        "--source",
        required=True,
        choices=sorted(d.name for d in ADAPTERS_DIR.iterdir() if d.is_dir()),
    )
    parser.add_argument("--input", required=True, help="JSONL of raw records, or an export dir for adapters with load_input")
    parser.add_argument("--out", default="data", help="output root (default: ./data)")
    parser.add_argument("--split", choices=["train", "val", "test"], default=None,
                        help="for export-dir inputs: filter by split; outputs go under <source>_<split>")
    parser.add_argument("--name", default=None,
                        help="override the output source name (e.g. --name kgdata_synth vs kgdata_public) "
                             "so multiple exports from one adapter never clobber each other")
    args = parser.parse_args(argv)

    adapter = load_adapter(args.source)
    if hasattr(adapter, "load_input"):
        records = adapter.load_input(args.input, split=args.split)
    else:
        if args.split is not None:
            raise SystemExit(f"--split is not supported by adapter {args.source!r} (no load_input)")
        records = [json.loads(line) for line in Path(args.input).read_text(encoding="utf-8").splitlines() if line.strip()]

    docs, issues = adapter.normalize(records)
    docs, report = sanitize_docs(docs)
    docs, dupes = dedup_quads(docs)

    source_out = safe_name(args.name) if args.name else safe_name(
        args.source + (f"_{args.split}" if args.split else ""))
    out_root = safe_out_dir(args.out, Path.cwd())
    store_paths = write_docs(docs, out_root, source_out, out_root)
    unified_paths = write_unified(docs, out_root / "unified" / source_out, out_root)

    n_quads = sum(len(d.quads) for d in docs)
    print(f"[{source_out}] records={len(records)} docs={len(docs)} quads={n_quads}")
    print(f"[{source_out}] sanitize: {report.summary()}")
    print(f"[{source_out}] dedup removed {dupes} quads")
    if issues:
        detail = ", ".join(f"{k}={v}" for k, v in sorted(issues.items()))
        print(f"[{source_out}] adapter issues: {detail}")
    for label, path in {**store_paths, **unified_paths}.items():
        print(f"[{source_out}] wrote {label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
