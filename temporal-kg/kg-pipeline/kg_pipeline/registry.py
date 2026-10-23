"""Adapter registry: discovers adapters/*/meta.yaml and resolves mixtures.

The registry replaces the notebook's inline ner_sources/re_sources/el_sources
lists: one place declares what sources exist, their licenses, pinned
versions, and how much of the training mixture each contributes.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("PyYAML is required for the adapter registry (pip install pyyaml)") from exc


@dataclass
class AdapterMeta:
    name: str
    tier: str
    license: str
    url: str
    weight: float
    version: str = "unpinned"
    description: str = ""

    @classmethod
    def from_yaml(cls, path: Path) -> "AdapterMeta":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls(
            name=raw["name"],
            tier=raw.get("tier", "?"),
            license=raw.get("license", "unknown"),
            url=raw.get("url", ""),
            weight=float(raw.get("weight", 1.0)),
            version=str(raw.get("version", "unpinned")),
            description=raw.get("description", ""),
        )


def discover_adapters(root) -> Dict[str, AdapterMeta]:
    adapters_dir = Path(root) / "adapters"
    metas = {}
    for meta_path in sorted(adapters_dir.glob("*/meta.yaml")):
        meta = AdapterMeta.from_yaml(meta_path)
        metas[meta.name] = meta
    return metas


def mixture_weights(metas: Dict[str, AdapterMeta]) -> Dict[str, float]:
    if not metas:
        raise ValueError("no adapters registered")
    total = sum(m.weight for m in metas.values())
    if total <= 0:
        raise ValueError("adapter weights must sum to a positive value")
    return {name: m.weight / total for name, m in metas.items()}
