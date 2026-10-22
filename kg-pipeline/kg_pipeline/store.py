"""Versioned corpus store: JSONL always, parquet when pyarrow is available.

Parquet stores one string column with the full JSON document — nested quad
schemas make wide-column layouts brittle for no query gain at this stage.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List

from .paths import safe_name, safe_out_dir
from .schema import SCHEMA_VERSION, Document


def write_docs(docs: List[Document], out_dir, source: str, allowed_root) -> Dict[str, Path]:
    base = safe_out_dir(Path(out_dir) / "normalized" / safe_name(source), allowed_root)
    base.mkdir(parents=True, exist_ok=True)

    payload = "".join(
        json.dumps(d.to_dict(), ensure_ascii=False, sort_keys=True) + "\n" for d in docs
    )
    jsonl_path = base / "docs.jsonl"
    jsonl_path.write_text(payload, encoding="utf-8")

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "source": source,
        "doc_count": len(docs),
        "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (base / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    paths = {"jsonl": jsonl_path, "manifest": base / "manifest.json"}

    try:
        import pyarrow as pa
    except ImportError:
        return paths

    table = pa.table({"doc": [json.dumps(d.to_dict(), ensure_ascii=False, sort_keys=True) for d in docs]})
    parquet_path = base / "docs.parquet"
    pa.parquet.write_table(table, parquet_path)
    paths["parquet"] = parquet_path
    return paths


def read_docs(path) -> Iterable[Document]:
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield Document.from_dict(json.loads(line))
