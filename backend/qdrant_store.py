"""
Qdrant Edge vector store wrapper.

Provides a clean API over qdrant-client for:
  • Creating / resetting collections
  • Upserting memories (text → embed → store) with version vectors & anomaly detection
  • Semantic + filtered search (with tombstone soft-delete awareness)
  • Scrolling / retrieving active & tombstoned records
  • Deleting / restoring records
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Sequence

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    Range,
    VectorParams,
)

from config import settings
from embedding import embedding_service
from models import MemoryResponse, build_payload

logger = logging.getLogger(__name__)


class QdrantStore:
    """Thin facade over a local (edge) or remote Qdrant instance."""

    def __init__(self, path: str | None = None, url: str | None = None):
        if url:
            self.client = QdrantClient(url=url, api_key=settings.CLOUD_QDRANT_API_KEY)
            self._mode = "cloud"
        elif path == ":memory:":
            self.client = QdrantClient(":memory:")
            self._mode = "memory"
        else:
            self.client = QdrantClient(path=path or settings.EDGE_QDRANT_PATH)
            self._mode = "edge"
        logger.info("QdrantStore initialised in %s mode", self._mode)

    def close(self) -> None:
        """Close Qdrant client connection."""
        try:
            self.client.close()
        except Exception:
            pass

    # ── Collection management ────────────────────────────────────────────

    def ensure_collection(self) -> None:
        """Create the memories collection if it doesn't exist."""
        collections = [c.name for c in self.client.get_collections().collections]
        if settings.COLLECTION_NAME not in collections:
            self.client.create_collection(
                collection_name=settings.COLLECTION_NAME,
                vectors_config=VectorParams(
                    size=settings.VECTOR_SIZE,
                    distance=Distance.COSINE,
                ),
            )
            logger.info("Created collection '%s'", settings.COLLECTION_NAME)

    def reset_collection(self) -> None:
        """Purge all points from the collection for a clean slate."""
        try:
            all_pts = self.get_all_points(with_vectors=False)
            if all_pts:
                self.delete_points([str(p.id) for p in all_pts])
        except Exception as exc:
            logger.warning("Failed to delete points during reset: %s", exc)
        try:
            self.ensure_collection()
        except Exception:
            pass

    # ── Upsert & Anomaly Detection ───────────────────────────────────────

    def check_anomaly(self, vector: list[float], k: int = settings.ANOMALY_K) -> tuple[bool, float | None]:
        """
        Check if a vector is an anomaly by querying top-k nearest neighbors.
        If collection has items and nearest cosine similarity < ANOMALY_THRESHOLD,
        it is flagged as an anomaly.
        """
        total = self.count_active()
        if total == 0:
            return False, None

        try:
            # Query non-tombstone points
            query_filter = Filter(
                must=[FieldCondition(key="is_tombstone", match=MatchValue(value=False))]
            )
            response = self.client.query_points(
                collection_name=settings.COLLECTION_NAME,
                query=vector,
                query_filter=query_filter,
                limit=min(k, total),
                with_payload=False,
                with_vectors=False,
            )
            if not response.points:
                # If filter didn't match (e.g. legacy records without is_tombstone), fallback without filter
                response = self.client.query_points(
                    collection_name=settings.COLLECTION_NAME,
                    query=vector,
                    limit=min(k, total),
                    with_payload=False,
                    with_vectors=False,
                )
            if not response.points:
                return False, None

            nearest_score = float(response.points[0].score)
            is_anomaly = nearest_score < settings.ANOMALY_THRESHOLD
            return is_anomaly, round(nearest_score, 4)
        except Exception:
            return False, None

    def add_memory(
        self,
        text: str,
        source: str = "manual",
        importance: float = 0.7,
        tags: list[str] | None = None,
        category: str = "general",
        metadata: dict[str, Any] | None = None,
        point_id: str | None = None,
        vector: list[float] | None = None,
        payload_overrides: dict[str, Any] | None = None,
        check_for_anomaly: bool = True,
        lamport_clock: int = 1,
        version_vector: dict[str, int] | None = None,
    ) -> tuple[str, bool, float | None]:
        """
        Embed text, check for anomaly against existing local vectors, and upsert.
        Returns tuple of (point_id, is_anomaly, nearest_similarity).
        """
        pid = point_id or str(uuid.uuid4())
        vec = vector or embedding_service.embed_text(text)

        is_anomaly = False
        nearest_sim = None

        if check_for_anomaly:
            is_anomaly, nearest_sim = self.check_anomaly(vec)

        priority = "high" if is_anomaly else "normal"
        priority_reason = "anomaly" if is_anomaly else None

        payload = build_payload(
            text=text,
            source=source,
            importance=importance,
            tags=tags,
            category=category,
            device_id=settings.DEVICE_ID,
            metadata=metadata,
            priority=priority,
            priority_reason=priority_reason,
            nearest_similarity=nearest_sim,
            lamport_clock=lamport_clock,
            version_vector=version_vector or {settings.DEVICE_ID: lamport_clock},
            is_tombstone=False,
        )
        if payload_overrides:
            payload.update(payload_overrides)

        self.client.upsert(
            collection_name=settings.COLLECTION_NAME,
            points=[PointStruct(id=pid, vector=vec, payload=payload)],
        )
        return pid, is_anomaly, nearest_sim

    def upsert_point(self, point_id: str, vector: list[float], payload: dict[str, Any]) -> None:
        """Raw upsert — used by consolidation and sync."""
        self.client.upsert(
            collection_name=settings.COLLECTION_NAME,
            points=[PointStruct(id=point_id, vector=vector, payload=payload)],
        )

    def upsert_points_batch(
        self, points: list[tuple[str, list[float], dict[str, Any]]]
    ) -> None:
        """Batch upsert a list of (id, vector, payload) tuples."""
        structs = [PointStruct(id=pid, vector=vec, payload=pl) for pid, vec, pl in points]
        self.client.upsert(
            collection_name=settings.COLLECTION_NAME,
            points=structs,
        )

    # ── Search ───────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        limit: int = 10,
        category: str | None = None,
        min_importance: float | None = None,
        include_local_only: bool = True,
        include_tombstones: bool = False,
    ) -> list[MemoryResponse]:
        """Semantic search with optional metadata filters and soft-delete filtering."""
        query_vec = embedding_service.embed_text(query)

        conditions = []
        if not include_tombstones:
            conditions.append(
                FieldCondition(key="is_tombstone", match=MatchValue(value=False))
            )
        if category:
            conditions.append(
                FieldCondition(key="category", match=MatchValue(value=category))
            )
        if min_importance is not None:
            conditions.append(
                FieldCondition(key="importance", range=Range(gte=min_importance))
            )
        if not include_local_only:
            conditions.append(
                FieldCondition(
                    key="sync_eligibility",
                    match=MatchValue(value="sync_eligible"),
                )
            )

        search_filter = Filter(must=conditions) if conditions else None

        try:
            response = self.client.query_points(
                collection_name=settings.COLLECTION_NAME,
                query=query_vec,
                query_filter=search_filter,
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
            hits = response.points
        except Exception:
            # Fallback if filter on non-indexed field failed
            response = self.client.query_points(
                collection_name=settings.COLLECTION_NAME,
                query=query_vec,
                limit=limit * 2,
                with_payload=True,
                with_vectors=False,
            )
            hits = [h for h in response.points if not (h.payload.get("is_tombstone", False) and not include_tombstones)][:limit]

        return [self._hit_to_response(h) for h in hits]

    # ── Retrieve / scroll ────────────────────────────────────────────────

    def get_all_points(self, with_vectors: bool = True) -> list:
        """Scroll through every point in the collection."""
        all_points = []
        offset = None
        while True:
            result = self.client.scroll(
                collection_name=settings.COLLECTION_NAME,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=with_vectors,
            )
            points, next_offset = result
            all_points.extend(points)
            if next_offset is None:
                break
            offset = next_offset
        return all_points

    def get_point(self, point_id: str):
        """Retrieve a single point by ID."""
        results = self.client.retrieve(
            collection_name=settings.COLLECTION_NAME,
            ids=[point_id],
            with_payload=True,
            with_vectors=True,
        )
        return results[0] if results else None

    def count(self) -> int:
        """Total points in the collection (including soft tombstones)."""
        info = self.client.get_collection(settings.COLLECTION_NAME)
        return info.points_count or 0

    def count_active(self) -> int:
        """Count active, non-tombstoned memories."""
        all_pts = self.get_all_points(with_vectors=False)
        return sum(1 for p in all_pts if not p.payload.get("is_tombstone", False))

    # ── Delete ───────────────────────────────────────────────────────────

    def delete_points(self, point_ids: Sequence[str]) -> None:
        """Delete specific points by ID."""
        if not point_ids:
            return
        from qdrant_client.models import PointIdsList
        self.client.delete(
            collection_name=settings.COLLECTION_NAME,
            points_selector=PointIdsList(points=list(point_ids)),
        )

    def update_payload(self, point_id: str, payload_updates: dict[str, Any]) -> None:
        """Partial payload update for a single point."""
        self.client.set_payload(
            collection_name=settings.COLLECTION_NAME,
            payload=payload_updates,
            points=[point_id],
        )

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _hit_to_response(hit) -> MemoryResponse:
        pl = hit.payload or {}
        return MemoryResponse(
            id=str(hit.id),
            text=pl.get("text", ""),
            source=pl.get("source", "unknown"),
            importance=pl.get("importance", 0.5),
            sync_eligibility=pl.get("sync_eligibility", "sync_eligible"),
            sync_reason=pl.get("sync_reason"),
            created_at=pl.get("created_at", ""),
            updated_at=pl.get("updated_at", ""),
            is_consolidated=pl.get("is_consolidated", False),
            decay_score=pl.get("decay_score", 1.0),
            device_id=pl.get("device_id", ""),
            tags=pl.get("tags", []),
            category=pl.get("category", "general"),
            synced=pl.get("synced", False),
            merge_count=pl.get("merge_count", 1),
            pii_detected=pl.get("pii_detected", False),
            priority=pl.get("priority", "normal"),
            priority_reason=pl.get("priority_reason"),
            nearest_similarity=pl.get("nearest_similarity"),
            is_tombstone=pl.get("is_tombstone", False),
            tombstone_at=pl.get("tombstone_at"),
            undo_until=pl.get("undo_until"),
            version_vector=pl.get("version_vector", {}),
            lamport_clock=pl.get("lamport_clock", 1),
            numeric_summary=pl.get("numeric_summary"),
            score=getattr(hit, "score", None),
        )

    @staticmethod
    def point_to_response(point) -> MemoryResponse:
        pl = point.payload or {}
        return MemoryResponse(
            id=str(point.id),
            text=pl.get("text", ""),
            source=pl.get("source", "unknown"),
            importance=pl.get("importance", 0.5),
            sync_eligibility=pl.get("sync_eligibility", "sync_eligible"),
            sync_reason=pl.get("sync_reason"),
            created_at=pl.get("created_at", ""),
            updated_at=pl.get("updated_at", ""),
            is_consolidated=pl.get("is_consolidated", False),
            decay_score=pl.get("decay_score", 1.0),
            device_id=pl.get("device_id", ""),
            tags=pl.get("tags", []),
            category=pl.get("category", "general"),
            synced=pl.get("synced", False),
            merge_count=pl.get("merge_count", 1),
            pii_detected=pl.get("pii_detected", False),
            priority=pl.get("priority", "normal"),
            priority_reason=pl.get("priority_reason"),
            nearest_similarity=pl.get("nearest_similarity"),
            is_tombstone=pl.get("is_tombstone", False),
            tombstone_at=pl.get("tombstone_at"),
            undo_until=pl.get("undo_until"),
            version_vector=pl.get("version_vector", {}),
            lamport_clock=pl.get("lamport_clock", 1),
            numeric_summary=pl.get("numeric_summary"),
        )
