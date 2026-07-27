"""On-disk LLM response cache.

One JSON file per cached response, keyed by sha256 of the request parameters
(model, messages, temperature, max_tokens). Files are inspectable and
resumable: a partial run picks up where it left off.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def key_for(
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
) -> str:
    """Compute the deterministic cache key for a request.

    Including ``model`` in the key means switching ``--model`` cleanly
    invalidates cache entries.
    """
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class DiskCache:
    """SHA-256-keyed JSON file cache under ``<root>/<key-prefix>.json``.

    Files are written atomically via ``tmp + os.replace``.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Use the first 16 chars of the hash for the on-disk filename to keep
        # directory listings manageable; the full key is stored inside.
        return self.root / f"{key[:16]}.json"

    def has(self, key: str) -> bool:
        return self._path(key).is_file()

    def get(self, key: str) -> dict[str, Any] | None:
        p = self._path(key)
        if not p.is_file():
            return None
        try:
            with p.open("r", encoding="utf-8") as f:
                entry = json.load(f)
            # Belt-and-suspenders: confirm stored key matches requested key.
            if entry.get("key") != key:
                return None
            return entry
        except (OSError, json.JSONDecodeError):
            return None

    def put(
        self,
        *,
        key: str,
        model: str,
        temperature: float,
        request_messages: list[dict[str, str]],
        prompt_tokens: int,
        completion_tokens: int,
        raw_content: str,
        parsed: Any,
    ) -> None:
        """Write a cache entry atomically. Silently overwrites on key collision."""
        p = self._path(key)
        entry = {
            "key": key,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "temperature": temperature,
            "request_messages": request_messages,
            "request_messages_hash": key_for(
                model=model,
                messages=request_messages,
                temperature=temperature,
                max_tokens=0,  # hash for debug only; not used for lookup
            ),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "raw_content": raw_content,
            "parsed": parsed,
        }
        # Atomic write: tmp in same dir, then os.replace.
        fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=str(self.root))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(entry, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, p)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def __len__(self) -> int:
        return sum(1 for _ in self.root.glob("*.json") if not _.name.startswith(".tmp_"))
