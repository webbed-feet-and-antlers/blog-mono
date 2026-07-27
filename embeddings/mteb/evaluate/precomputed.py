"""Encoder that serves vectors from a precomputed ``.npz`` on disk.

Same on-disk schema as :class:`evaluate.cache.EmbeddingCache`
(``texts`` + ``vectors`` arrays), but read-only and treated as the source of
truth — missing texts are a hard error, not a cache miss.

Intended for the PPLX embedding runtime
(``perplexity-ai/pplx-embed-context-v1-0.6b``): the producer script
:mod:`mteb.scripts.precompute_pplx` encodes every task input once on a GPU
box and dumps a single ``__text__.npz`` per ``(model, max_chars, dim)``.
The loader maps every ``kind`` (``query`` / ``document`` / ``text``) to that
same file because PPLX uses no E5/BGE-style prefixes — see
``modules/runtime.py`` + ``modules/constants.py`` (no prefix logic anywhere).
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from .cache import EmbeddingCache, _sig
from .encoders import DEFAULT_MAX_CHARS, TextKind

logger = logging.getLogger(__name__)


class PrecomputedEncoder:
    """Encoder that serves vectors from a precomputed ``.npz`` on disk.

    The ``.npz`` uses the same schema as :class:`EmbeddingCache`
    (``texts`` + ``vectors`` arrays), but this class only ever calls
    :meth:`EmbeddingCache.get_many` — never ``put`` / ``flush``. Missing
    texts raise :class:`RuntimeError` because precomputed files are the
    source of truth, not a regeneratable cache.
    """

    def __init__(
        self,
        model: str,
        *,
        dim: int,
        precomputed_dir: Path,
        max_chars: int = DEFAULT_MAX_CHARS,
        max_tokens: int = 32768,
    ) -> None:
        self.model_name = model
        self.name = f"precomputed/{model}"
        self.dim = dim
        self.max_tokens = max_tokens
        self._max_chars = max_chars
        # PPLX uses no kind-specific prefixes → all kinds read the __text__ file.
        path = Path(precomputed_dir) / _sig(model, "text", max_chars, dim)
        if not path.exists():
            raise FileNotFoundError(
                f"Precomputed embeddings not found at {path}. "
                f"Run: python3 mteb/scripts/precompute_pplx.py"
            )
        self._cache = EmbeddingCache(path, dim=dim)
        logger.info("Loaded precomputed embeddings: %s", path.name)

    def encode(self, texts: list[str], *, kind: TextKind = "text") -> np.ndarray:
        if not texts:
            return np.asarray([], dtype=np.float32).reshape(0, self.dim)
        hits, misses = self._cache.get_many(texts)
        if misses:
            sample = [texts[i][:80] for i in misses[:3]]
            raise RuntimeError(
                f"Precomputed embeddings missing for {len(misses)}/{len(texts)} "
                f"inputs (kind={kind!r}). First few: {sample!r}. "
                f"Re-run mteb/scripts/precompute_pplx.py to refresh."
            )
        out = np.empty((len(texts), self.dim), dtype=np.float32)
        for i in range(len(texts)):
            out[i] = hits[i]
        return out
