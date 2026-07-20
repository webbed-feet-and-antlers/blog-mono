"""Encoder protocol + provider wrappers (OpenAI / Gemini / sentence-transformers).

All wrappers implement the :class:`Encoder` protocol so the evaluator can treat
them uniformly. Each wrapper:

    - Reads its API key from env (override via ``api_key`` ctor arg).
    - Truncates inputs to ``max_chars`` (approximates token count via 4 chars/token).
    - Batches API calls internally to stay under per-request input limits.

The :data:`MODEL_MATRIX` lists every (provider, model) pair that ``--all`` runs.
Keep it in sync with the Taskfile.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Literal, Protocol, runtime_checkable

import numpy as np

logger = logging.getLogger(__name__)


TextKind = Literal["query", "document", "text"]
"""How an embedding will be consumed. Some models (E5/BGE/Gemini) take a
task-specific prefix or task type; ``"text"`` is the neutral default."""


# Roughly 2000 tokens ≈ 8000 chars. Encoders truncate input strings at this
# length to stay under model context limits (8192 for OpenAI/Gemini).
DEFAULT_MAX_CHARS = 8000


# ----- E5/BGE prefix detection ----------------------------------------------

# Models that expect the E5-style "query:" / "passage:" prefix convention.
# BGE follows the same convention.
_E5_FAMILY_HINTS = ("e5-", "bge-", "gte-", "e6-")


def _looks_like_e5(model_name: str) -> bool:
    name = model_name.lower()
    return any(h in name for h in _E5_FAMILY_HINTS)


def _apply_prefix(text: str, kind: TextKind, *, model_name: str) -> str:
    """Apply the E5/BGE prefix for ``query`` / ``document`` kinds.

    Plain MiniLM-style models and ``kind == "text"`` get no prefix.
    """
    if kind == "text":
        return text
    if not _looks_like_e5(model_name):
        return text
    prefix = "query: " if kind == "query" else "passage: "
    return prefix + text


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


# ----- Encoder protocol -----------------------------------------------------


@runtime_checkable
class Encoder(Protocol):
    """All providers implement this. Synchronous; batches internally."""

    name: str
    dim: int
    max_tokens: int

    def encode(self, texts: list[str], *, kind: TextKind = "text") -> np.ndarray: ...


# ----- OpenAI ---------------------------------------------------------------


class OpenAIEncoder:
    """OpenAI ``text-embedding-3-*`` family.

    Uses the v1 SDK (``openai>=1.0``). Reads ``OPENAI_API_KEY``.
    """

    # SDK limits: 2048 inputs per request, 300K total tokens per request.
    _BATCH_SIZE = 2048

    def __init__(
        self,
        model: str,
        *,
        dim: int,
        max_tokens: int = 8191,
        max_chars: int = DEFAULT_MAX_CHARS,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        try:
            from openai import OpenAI  # lazy: openai is an extra dep
        except ImportError as e:
            raise ImportError(
                "openai is required for OpenAIEncoder — install with "
                "`uv pip install -e .[evaluate-api]`"
            ) from e

        key = api_key or os.environ.get("OPENAI_API_KEY", "").strip()
        if not key:
            sys.exit(
                "Set OPENAI_API_KEY (see mteb/.env.example) "
                "or pass --api-key on the command line."
            )
        kwargs: dict = {"api_key": key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)
        self._model = model
        self.model_name = model
        self.name = f"openai/{model}"
        self.dim = dim
        self.max_tokens = max_tokens
        self._max_chars = max_chars

    def encode(self, texts: list[str], *, kind: TextKind = "text") -> np.ndarray:
        out: list[list[float]] = []
        # OpenAI embeddings don't take a prefix; kind is ignored.
        truncated = [_truncate(t, self._max_chars) for t in texts]
        # Drop empty strings (API rejects them).
        empty_count = sum(1 for t in truncated if not t)
        if empty_count:
            logger.warning("OpenAI encode: %d empty inputs replaced with a space", empty_count)
            truncated = [t if t else " " for t in truncated]

        for i in range(0, len(truncated), self._BATCH_SIZE):
            batch = truncated[i : i + self._BATCH_SIZE]
            resp = self._client.embeddings.create(input=batch, model=self._model)
            # SDK returns objects sorted by index; sort defensively.
            data = sorted(resp.data, key=lambda d: d.index)
            for d in data:
                out.append(list(d.embedding))
        return np.asarray(out, dtype=np.float32)


# ----- Google Gemini --------------------------------------------------------


class GeminiEncoder:
    """Google Gemini ``text-embedding-004`` / ``gemini-embedding-001``.

    Uses the unified ``google-genai`` SDK. Reads ``GOOGLE_API_KEY`` (or the
    legacy ``GEMINI_API_KEY`` alias).
    """

    # The SDK accepts up to 100 inputs per request for embed_contents.
    _BATCH_SIZE = 100

    def __init__(
        self,
        model: str,
        *,
        dim: int,
        max_tokens: int = 2048,
        max_chars: int = DEFAULT_MAX_CHARS,
        api_key: str | None = None,
    ) -> None:
        try:
            from google import genai  # type: ignore
            from google.genai.types import TaskType  # type: ignore
        except ImportError as e:
            raise ImportError(
                "google-genai is required for GeminiEncoder — install with "
                "`uv pip install -e .[evaluate-api]`"
            ) from e

        key = (
            api_key
            or os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("GEMINI_API_KEY", "")
        ).strip()
        if not key:
            sys.exit(
                "Set GOOGLE_API_KEY (or GEMINI_API_KEY) — see mteb/.env.example."
            )
        self._genai = genai
        self._TaskType = TaskType
        self._client = genai.Client(api_key=key)
        self._model = model
        self.model_name = model
        self.name = f"gemini/{model}"
        self.dim = dim
        self.max_tokens = max_tokens
        self._max_chars = max_chars

    def _task_type_for(self, kind: TextKind):
        TT = self._TaskType
        if kind == "query":
            return TT.RETRIEVAL_QUERY
        if kind == "document":
            return TT.RETRIEVAL_DOCUMENT
        return TT.SEMANTIC_SIMILARITY

    def encode(self, texts: list[str], *, kind: TextKind = "text") -> np.ndarray:
        truncated = [_truncate(t, self._max_chars) for t in texts]
        empty_count = sum(1 for t in truncated if not t)
        if empty_count:
            logger.warning("Gemini encode: %d empty inputs replaced with a space", empty_count)
            truncated = [t if t else " " for t in truncated]

        task_type = self._task_type_for(kind)
        out: list[list[float]] = []
        for i in range(0, len(truncated), self._BATCH_SIZE):
            batch = truncated[i : i + self._BATCH_SIZE]
            resp = self._client.models.embed_contents(
                model=self._model,
                contents=batch,
                config={"task_type": task_type, "output_dimensionality": self.dim},
            )
            for emb in resp.embeddings:
                out.append(list(emb.values))
        return np.asarray(out, dtype=np.float32)


# ----- Sentence-Transformers (local) ----------------------------------------


class SentenceTransformerEncoder:
    """Local sentence-transformers model.

    Applies E5/BGE-style ``"query: "`` / ``"passage: "`` prefixes when the
    model name suggests it; MiniLM-family models get no prefix.
    """

    def __init__(
        self,
        model: str,
        *,
        dim: int,
        device: str = "cpu",
        max_tokens: int = 512,
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as e:
            raise ImportError(
                "sentence-transformers is required for SentenceTransformerEncoder — "
                "install with `uv pip install -e .[evaluate-local]`"
            ) from e

        resolved_device = self._resolve_device(device)
        self._model = SentenceTransformer(model, device=resolved_device)
        self._max_seq_length = min(max_tokens, 512)
        self._model.max_seq_length = self._max_seq_length
        self.model_name = model
        self.name = f"sentence-transformers/{model}"
        self.dim = dim
        self.max_tokens = max_tokens
        self._max_chars = max_chars
        self._device = resolved_device

    @staticmethod
    def _resolve_device(requested: str) -> str:
        """Auto-fallback to CPU if requested device is unavailable."""
        if requested == "cpu":
            return "cpu"
        try:
            import torch  # type: ignore
        except ImportError:
            logger.warning("torch not available — falling back to CPU")
            return "cpu"
        if requested == "cuda":
            if not torch.cuda.is_available():
                logger.warning("CUDA not available — falling back to CPU")
                return "cpu"
            return "cuda"
        if requested == "mps":
            if not torch.backends.mps.is_available():
                logger.warning("MPS not available — falling back to CPU")
                return "cpu"
            return "mps"
        logger.warning("Unknown device %r — falling back to CPU", requested)
        return "cpu"

    def encode(self, texts: list[str], *, kind: TextKind = "text") -> np.ndarray:
        prefixed = [
            _apply_prefix(_truncate(t, self._max_chars), kind, model_name=self.model_name)
            for t in texts
        ]
        empty_count = sum(1 for t in prefixed if not t)
        if empty_count:
            logger.warning(
                "sentence-transformers encode: %d empty inputs replaced with a space",
                empty_count,
            )
            prefixed = [t if t else " " for t in prefixed]
        vecs = self._model.encode(
            prefixed,
            batch_size=32,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=False,
        )
        return np.asarray(vecs, dtype=np.float32)


# ----- Model matrix ---------------------------------------------------------

# (provider, model_id, known_dim). Keep in sync with embeddings/Taskfile.yml.
# Dims come from the official model cards:
#   - text-embedding-3-small: 1536
#   - text-embedding-3-large: 3072
#   - text-embedding-004:     768
#   - gemini-embedding-001:  3072 (downscale via output_dimensionality)
#   - all-MiniLM-L6-v2:       384
#   - bge-base-en-v1.5:       768
MODEL_MATRIX: list[tuple[str, str, int]] = [
    ("openai", "text-embedding-3-small", 1536),
    ("openai", "text-embedding-3-large", 3072),
    ("gemini", "text-embedding-004", 768),
    ("gemini", "gemini-embedding-001", 3072),
    ("sentence-transformers", "all-MiniLM-L6-v2", 384),
    ("sentence-transformers", "bge-base-en-v1.5", 768),
]


def build_encoder(
    provider: str,
    model: str,
    *,
    dim: int | None = None,
    device: str = "cpu",
    api_key: str | None = None,
) -> Encoder:
    """Factory: build an Encoder by provider name.

    If ``dim`` is None, look it up in :data:`MODEL_MATRIX`.
    """
    if dim is None:
        for p, m, d in MODEL_MATRIX:
            if p == provider and m == model:
                dim = d
                break
        if dim is None:
            sys.exit(
                f"Unknown {provider}/{model} and no --dim given. "
                f"Add it to MODEL_MATRIX or pass --dim."
            )

    if provider == "openai":
        return OpenAIEncoder(model, dim=dim, api_key=api_key)  # type: ignore[return-value]
    if provider == "gemini":
        return GeminiEncoder(model, dim=dim, api_key=api_key)  # type: ignore[return-value]
    if provider == "sentence-transformers":
        return SentenceTransformerEncoder(model, dim=dim, device=device)  # type: ignore[return-value]
    sys.exit(
        f"Unknown provider {provider!r}. "
        f"Expected: openai | gemini | sentence-transformers"
    )
