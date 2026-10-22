"""T-REx fetch (network-only; not exercised by tests).

Two routes:
  - HF mirror via `datasets` (recommended; pin a revision in meta.yaml on
    first full fetch)
  - direct zip download from the T-REx distribution

Both go through guarded_urlopen (public http/https targets only) and write
only inside an explicitly allowed output root.
"""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kg_pipeline.httpguard import guarded_urlopen  # noqa: E402
from kg_pipeline.paths import safe_out_dir  # noqa: E402

MAX_DOWNLOAD_BYTES = 4 << 30  # 4 GiB safety cap

# Confirm and pin before first full fetch; the project page lists the
# canonical distribution: https://hadyelsahar.io/t-rex/
HF_MIRROR = "QinyuanWu/T-Rex-MC"


def _validate_members(zf: zipfile.ZipFile) -> None:
    """Reject absolute paths, '..' segments, and drive letters in zip members."""
    for info in zf.infolist():
        name = info.filename
        parts = Path(name).parts
        if name.startswith(("/", "\\")) or ".." in parts or (parts and ":" in parts[0]):
            raise ValueError(f"unsafe zip member {name!r}")


def fetch_trex_zip(url: str, out_dir, allowed_root=None) -> Path:
    """Download a T-REx zip archive and extract its per-property JSONs."""
    out = safe_out_dir(out_dir, allowed_root or Path.cwd())
    out.mkdir(parents=True, exist_ok=True)
    with guarded_urlopen(url, timeout=300) as resp:
        data = resp.read(MAX_DOWNLOAD_BYTES + 1)
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise ValueError(f"download exceeds {MAX_DOWNLOAD_BYTES} bytes; use a streaming fetch")
    archive_path = out / "trex.zip"
    archive_path.write_bytes(data)
    with zipfile.ZipFile(archive_path) as zf:
        _validate_members(zf)
        zf.extractall(out)
    return out


def fetch_trex_hf(out_dir, repo_id: str = HF_MIRROR, revision: str | None = None,
                  allowed_root=None) -> Path:
    """Pull the HF mirror via `datasets` (pip install kg-pipeline[fetch])."""
    from datasets import load_dataset  # optional dependency

    out = safe_out_dir(out_dir, allowed_root or Path.cwd())
    out.mkdir(parents=True, exist_ok=True)
    ds = load_dataset(repo_id, revision=revision, split="train", streaming=True)
    lines = [json.dumps(rec, ensure_ascii=False) for rec in ds]
    path = out / "records.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
