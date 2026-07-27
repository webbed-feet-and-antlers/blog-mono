"""On-disk embedding cache for the evaluation runner.

Wraps any :class:`Encoder` so that re-runs don't re-pay API / GPU costs for
inputs already encoded. Transparent to callers — ``CachedEncoder`` implements
the same :class:`Encoder` protocol.

Design
------

One ``.npz`` file per ``(model, kind, max_chars, dim)`` combo at
``<cache_root>/<safe_model>__<kind>__mc<max_chars>_d<dim>.npz``. Each file
holds two arrays:

    - ``texts``   — object-dtype ``str`` array (the cache keys, pre-truncation)
    - ``vectors`` — ``float32`` ``(N, D)`` matrix (the cached embeddings)

Lifecycle: ``EmbeddingCache`` lazily loads its ``.npz`` on first access and
maintains an in-memory ``{text: row_idx}`` dict plus the row arrays. ``flush()``
atomically writes the in-memory state back to disk (``tmp`` + ``os.replace``).

``CachedEncoder`` opens one ``EmbeddingCache`` per ``(model, kind)`` on demand,
serves hits, batches misses into a single ``inner.encode()`` call, and flushes
everything in ``close()``.

Scope: single-process CLI. Concurrent writers race (last writer wins) —
documented, not handled.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path

import numpy as np

from .encoders import DEFAULT_MAX_CHARS, Encoder, TextKind

logger = logging.getLogger(__name__)


# ----- Filename helpers -----------------------------------------------------


_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe(s: str) -> str:
    """Make *s* safe to embed in a filename (collapse runs of bad chars)."""
    return _SAFE_RE.sub("_", s).strip("._-") or "_"


def _sig(
    model_name: str, kind: TextKind, max_chars: int, dim: int
) -> str:
    """Build the cache filename for a (model, kind, max_chars, dim) tuple.

    >>> _sig("openai/text-embedding-3-small", "query", 8000, 1536)
    'openai_text-embedding-3-small__query__mc8000_d1536.npz'
    """
    return f"{_safe(model_name)}__{kind}__mc{max_chars}_d{dim}.npz"


# ----- Per-(model, kind) store ---------------------------------------------


class EmbeddingCache:
    """Per-(model, kind, max_chars, dim) ``.npz``-backed text→vector store.

    Each instance owns exactly one ``.npz`` file. The file is loaded lazily on
    first access (constructor just records the path) and flushed explicitly
    via :meth:`flush`.

    A corrupt or dim-mismatched ``.npz`` is treated as empty (warn + continue)
    so a single bad file never blocks the whole run.
    """

    def __init__(self, path: Path, dim: int) -> None:
        self._path = Path(path)
        self._dim = int(dim)
        self._texts: list[str] = []
        self._vecs: list[np.ndarray] = []
        self._index: dict[str, int] = {}
        self._loaded = False
        self._dirty = False

    # ----- internal -----

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self._path.exists():
            return
        try:
            # ``texts`` is an object-dtype array (variable-length str); loading
            # it requires pickle. We write this file ourselves and never load
            # untrusted caches, so allow_pickle is safe here.
            data = np.load(self._path, allow_pickle=True)
            texts = np.asarray(data["texts"], dtype=object)
            vecs = np.asarray(data["vectors"], dtype=np.float32)
        except Exception as e:  # corrupt .npz / unreadable
            logger.warning(
                "Embedding cache %s unreadable (%s) — starting empty",
                self._path.name, e,
            )
            return
        if vecs.ndim != 2 or vecs.shape[0] != texts.shape[0]:
            logger.warning(
                "Embedding cache %s has mismatched shapes (texts=%s, vectors=%s) "
                "— starting empty",
                self._path.name, texts.shape, vecs.shape,
            )
            return
        if vecs.shape[1] != self._dim:
            logger.warning(
                "Embedding cache %s dim mismatch (file=%d, expected=%d) — "
                "starting empty",
                self._path.name, vecs.shape[1], self._dim,
            )
            return
        # Rebuild in-memory state.
        for i, t in enumerate(texts):
            self._texts.append(str(t))
            self._vecs.append(vecs[i])
            self._index[str(t)] = i
        logger.debug(
            "Loaded %d cached embeddings from %s",
            len(self._index), self._path.name,
        )

    def flush(self) -> None:
        """Atomically write the in-memory state back to the ``.npz``.

        No-op if nothing changed since load. Writes ``<path>.tmp`` then
        ``os.replace`` (atomic on POSIX and Windows).
        """
        if not self._dirty:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._texts:
            texts_arr = np.asarray(self._texts, dtype=object)
            vecs_arr = np.asarray(self._vecs, dtype=np.float32)
        else:
            texts_arr = np.asarray([], dtype=object)
            vecs_arr = np.asarray([], dtype=np.float32).reshape(0, self._dim)
        fd, tmp_path = tempfile.mkstemp(
            prefix=".tmp_", suffix=".npz", dir=str(self._path.parent)
        )
        try:
            with os.fdopen(fd, "wb") as f:
                np.savez(f, texts=texts_arr, vectors=vecs_arr)
            os.replace(tmp_path, self._path)
            self._dirty = False
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # ----- public API -----

    def get(self, text: str) -> np.ndarray | None:
        self._load()
        idx = self._index.get(text)
        if idx is None:
            return None
        return self._vecs[idx]

    def get_many(
        self, texts: list[str]
    ) -> tuple[dict[int, np.ndarray], list[int]]:
        """Return ``(hits_by_index, miss_indices)`` for *texts*.

        ``hits_by_index`` maps an index into *texts* to the cached vector.
        ``miss_indices`` is the ordered list of indices needing encoding.
        """
        self._load()
        hits: dict[int, np.ndarray] = {}
        misses: list[int] = []
        for i, t in enumerate(texts):
            idx = self._index.get(t)
            if idx is None:
                misses.append(i)
            else:
                hits[i] = self._vecs[idx]
        return hits, misses

    def put(self, text: str, vec: np.ndarray) -> None:
        vec = np.asarray(vec, dtype=np.float32)
        existing = self._index.get(text)
        if existing is not None:
            # Overwrite in place (shouldn't normally happen — same (model,
            # kind) + same text implies same vector — but be safe).
            self._vecs[existing] = vec
            self._dirty = True
            return
        self._index[text] = len(self._texts)
        self._texts.append(text)
        self._vecs.append(vec)
        self._dirty = True

    def put_many(self, texts: list[str], vecs: np.ndarray) -> None:
        vecs = np.asarray(vecs, dtype=np.float32)
        if vecs.ndim != 2:
            raise ValueError(f"vecs must be 2-D, got shape {vecs.shape}")
        if vecs.shape[0] != len(texts):
            raise ValueError(
                f"vecs rows ({vecs.shape[0]}) != texts ({len(texts)})"
            )
        if vecs.shape[1] != self._dim:
            raise ValueError(
                f"vecs dim ({vecs.shape[1]}) != cache dim ({self._dim})"
            )
        for t, v in zip(texts, vecs):
            self.put(t, v)

    def __len__(self) -> int:
        self._load()
        return len(self._index)


# ----- Encoder wrapper ------------------------------------------------------


class CachedEncoder:
    """Wraps an :class:`Encoder`; serves hits from disk, batches misses.

    Same protocol as the inner encoder — callers (``tasks.py`` etc.) are
    unaware caching is happening. One ``EmbeddingCache`` per ``(model, kind)``
    is opened lazily on first :meth:`encode` for that kind; all are flushed
    in :meth:`close`.
    """

    def __init__(self, inner: Encoder, cache_root: Path) -> None:
        self._inner = inner
        self._root = Path(cache_root)
        # Caches indexed by kind. The (model_name, max_chars, dim) triplet is
        # fixed for the lifetime of this wrapper, so it lives in the filename.
        self._caches: dict[str, EmbeddingCache] = {}
        self._dirty: bool = False

    # ----- Encoder protocol (delegates to inner) -----

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def model_name(self) -> str:
        return self._inner.model_name

    @property
    def dim(self) -> int:
        return self._inner.dim

    @property
    def max_tokens(self) -> int:
        return self._inner.max_tokens

    @property
    def _max_chars(self) -> int:
        # All real encoders expose _max_chars; fall back to the module default.
        return getattr(self._inner, "_max_chars", DEFAULT_MAX_CHARS)

    # ----- internal -----

    def _cache_for(self, kind: TextKind) -> EmbeddingCache:
        c = self._caches.get(kind)
        if c is not None:
            return c
        path = self._root / _sig(
            self._inner.model_name, kind, self._max_chars, self._inner.dim
        )
        c = EmbeddingCache(path, dim=self._inner.dim)
        self._caches[kind] = c
        return c

    # ----- Encoder.encode -----

    def encode(self, texts: list[str], *, kind: TextKind = "text") -> np.ndarray:
        if not texts:
            return np.asarray([], dtype=np.float32).reshape(0, self._inner.dim)

        cache = self._cache_for(kind)
        hits, miss_indices = cache.get_many(texts)

        if miss_indices:
            miss_texts = [texts[i] for i in miss_indices]
            logger.info(
                "Embedding cache %s: %d hits, %d misses (encoding)",
                cache._path.name, len(hits), len(miss_indices),
            )
            new_vecs = self._inner.encode(miss_texts, kind=kind)
            new_vecs = np.asarray(new_vecs, dtype=np.float32)
            if new_vecs.shape[0] != len(miss_indices):
                raise RuntimeError(
                    f"Inner encoder returned {new_vecs.shape[0]} vectors for "
                    f"{len(miss_indices)} inputs"
                )
            cache.put_many(miss_texts, new_vecs)
            for idx, vec in zip(miss_indices, new_vecs):
                hits[idx] = vec
        else:
            logger.info(
                "Embedding cache %s: %d hits, 0 misses",
                cache._path.name, len(hits),
            )

        # Assemble (N, D) in input order.
        dim = self._inner.dim
        out = np.empty((len(texts), dim), dtype=np.float32)
        for i in range(len(texts)):
            v = hits.get(i)
            if v is None:
                raise RuntimeError(
                    f"Missing vector for index {i} after encode (this is a bug)"
                )
            out[i] = v
        return out

    # ----- lifecycle -----

    def close(self) -> None:
        """Flush every cache opened during this run. Safe to call multiple times."""
        for kind, cache in self._caches.items():
            try:
                cache.flush()
            except Exception as e:  # pragma: no cover — defensive
                logger.exception(
                    "Failed to flush cache %s: %s", cache._path.name, e
                )
        self._caches.clear()
