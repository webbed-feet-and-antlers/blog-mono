"""Filesystem layer for storing uploaded files."""

from __future__ import annotations

import uuid
from pathlib import Path

from .config import settings


def ensure_storage_dir() -> Path:
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    return settings.storage_dir


async def save_upload(filename: str, data: bytes) -> tuple[str, Path]:
    """Persist an uploaded file under a uuid-prefixed name. Returns (id, path)."""
    ensure_storage_dir()
    file_id = uuid.uuid4().hex[:12]
    # Keep the original extension so parsers can sniff by suffix.
    suffix = Path(filename).suffix
    dest = settings.storage_dir / f"{file_id}{suffix}"
    dest.write_bytes(data)
    return file_id, dest


async def delete_upload(file_path: str) -> None:
    p = Path(file_path)
    if p.exists():
        p.unlink()
