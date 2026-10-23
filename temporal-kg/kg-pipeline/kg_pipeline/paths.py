"""Path guards for pipeline outputs: normalize, forbid '..', stay in bounds."""
from __future__ import annotations

import re
from pathlib import Path

_NAME_RE = re.compile(r"^[a-z0-9_]+$")


def safe_out_dir(path, allowed_root) -> Path:
    """Resolve an output directory under an explicit allowed root.

    Rejects any ``..`` component outright, then requires the resolved path to
    equal or live inside the resolved ``allowed_root``.
    """
    p = Path(path)
    if ".." in p.parts:
        raise ValueError(f"output path must not contain '..': {path!r}")
    resolved = p.resolve()
    root = Path(allowed_root).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"output path {resolved} escapes allowed root {root}")
    return resolved


def safe_name(name: str) -> str:
    """Validate a path segment (adapter/source name): [a-z0-9_]+ only."""
    if not _NAME_RE.match(name):
        raise ValueError(f"unsafe path segment {name!r}: must match [a-z0-9_]+")
    return name
