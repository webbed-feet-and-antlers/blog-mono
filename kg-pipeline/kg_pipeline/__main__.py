"""CLI: normalize a raw-record JSONL file through an adapter into the store.

Example (ICEWS sample):
    python -m kg_pipeline --source icews --input rows.jsonl --out data

T-REx with a qualifier cache:
    python -m kg_pipeline --source trex --input records.jsonl \
        --qualifiers qualifiers.json --out data
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
    parser.add_argument("--input", required=True, help="JSONL of raw records for the adapter")
    parser.add_argument("--out", default="data", help="output root (default: ./data)")
    parser.add_argument("--qualifiers", help="adapter-specific, trex: JSON qualifier cache path")
    args = parser.parse_args(argv)

    records = [json.loads(line) for line in Path(args.input).read_text(encoding="utf-8").splitlines() if line.strip()]
    adapter = load_adapter(args.source)

    kwargs = {}
    if args.qualifiers and hasattr(adapter, "make_lookup"):
        kwargs["qualifier_lookup"] = adapter.make_lookup(args.qualifiers)

    docs, issues = adapter.normalize(records, **kwargs)
    docs, report = sanitize_docs(docs)
    docs, dupes = dedup_quads(docs)

    out_root = safe_out_dir(args.out, Path.cwd())
    store_paths = write_docs(docs, out_root, args.source, out_root)
    unified_paths = write_unified(docs, out_root / "unified" / safe_name(args.source), out_root)

    n_quads = sum(len(d.quads) for d in docs)
    print(f"[{args.source}] records={len(records)} docs={len(docs)} quads={n_quads}")
    print(f"[{args.source}] sanitize: {report.summary()}")
    print(f"[{args.source}] dedup removed {dupes} quads")
    if issues:
        detail = ", ".join(f"{k}={v}" for k, v in sorted(issues.items()))
        print(f"[{args.source}] adapter issues: {detail}")
    for label, path in {**store_paths, **unified_paths}.items():
        print(f"[{args.source}] wrote {label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
