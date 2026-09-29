"""
Local embedding pipeline — runs entirely on-device with zero network dependency.
Uses fastembed (BAAI/bge-small-en-v1.5) to produce 384-dim vectors.
"""

from __future__ import annotations

import logging
import time
from typing import Sequence

import numpy as np
from fastembed import TextEmbedding

from config import settings

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Singleton wrapper around fastembed for on-device text embedding."""

    _instance: EmbeddingService | None = None
    _model: TextEmbedding | None = None

    def __new__(cls) -> EmbeddingService:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _ensure_model(self) -> TextEmbedding:
        if self._model is None:
            logger.info(
                "Loading embedding model '%s' (first call — may download)…",
                settings.EMBEDDING_MODEL,
            )
            t0 = time.perf_counter()
            self._model = TextEmbedding(model_name=settings.EMBEDDING_MODEL)
            elapsed = time.perf_counter() - t0
            logger.info("Embedding model ready in %.2fs", elapsed)
        return self._model

    # ── Public API ───────────────────────────────────────────────────────

    def embed_text(self, text: str) -> list[float]:
        """Embed a single string → list[float] of dimension VECTOR_SIZE."""
        model = self._ensure_model()
        embeddings = list(model.embed([text]))
        return embeddings[0].tolist()

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed multiple strings at once (more efficient for bulk ingest)."""
        model = self._ensure_model()
        return [e.tolist() for e in model.embed(list(texts))]

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        va = np.array(a, dtype=np.float32)
        vb = np.array(b, dtype=np.float32)
        dot = np.dot(va, vb)
        norm = np.linalg.norm(va) * np.linalg.norm(vb)
        if norm == 0:
            return 0.0
        return float(dot / norm)

    @staticmethod
    def average_vectors(vectors: list[list[float]]) -> list[float]:
        """Compute the L2-normalised mean of a set of vectors (for merging)."""
        arr = np.array(vectors, dtype=np.float32)
        mean = arr.mean(axis=0)
        norm = np.linalg.norm(mean)
        if norm > 0:
            mean = mean / norm
        return mean.tolist()


# Module-level convenience singleton
embedding_service = EmbeddingService()
