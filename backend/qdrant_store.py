"""
Qdrant Vector Store wrapper.

Provides a unified facade for:
  • Edge side: Embedded real Qdrant Edge (`qdrant-edge-py` / `EdgeShard`) with dense + sparse BM25 vectors.
  • Cloud side: Remote Qdrant Server (`qdrant-client` / `QdrantClient`).
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import uuid
from typing import Any, Sequence

import qdrant_edge as qe
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition as CloudFieldCondition,
    Filter as CloudFilter,
    MatchValue as CloudMatchValue,
    PointStruct,
    Range as CloudRange,
    VectorParams,
)

from config import settings
from embedding import embedding_service
from models import MemoryResponse, build_payload

logger = logging.getLogger(__name__)


def ensure_uuid(val: Any) -> str:
    """Ensure a point ID is a valid canonical UUID string."""
    if isinstance(val, uuid.UUID):
        return str(val)
    val_str = str(val)
    try:
        return str(uuid.UUID(val_str))
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, val_str))


class PointRecord:
    """Lightweight point record adapter providing .id, .vector, .payload, and .score."""

    def __init__(self, point_id: Any, vector: Any, payload: dict[str, Any] | None, score: float | None = None):
        self.id = str(point_id)
        if isinstance(vector, dict):
            self.vector = vector.get("dense", [])
            self.vectors = vector
        else:
            self.vector = vector
            self.vectors = {"dense": vector} if vector is not None else {}
        self.payload = dict(payload) if payload else {}
        # Ensure is_tombstone and state are synchronized
        if "is_tombstone" not in self.payload:
            self.payload["is_tombstone"] = (self.payload.get("state") == "tombstone")
        if "state" not in self.payload:
            self.payload["state"] = "tombstone" if self.payload.get("is_tombstone") else "active"
        self.score = score

    def __repr__(self) -> str:
        return f"PointRecord(id={self.id!r}, score={self.score}, payload={self.payload.get('text', '')[:30]!r})"


class QdrantStore:
    """Facade over local Qdrant Edge (EdgeShard) or remote Qdrant Server (QdrantClient)."""

    def __init__(self, path: str | None = None, url: str | None = None):
        self._url = url
        self._path = path
        self._temp_dir: str | None = None
        self._bm25 = qe.Bm25()

        if url:
            self.client = QdrantClient(url=url, api_key=settings.CLOUD_QDRANT_API_KEY)
            self.shard: qe.EdgeShard | None = None
            self._mode = "cloud"
            logger.info("QdrantStore initialised in cloud mode (url=%s)", url)
        else:
            self.client = None
            self._mode = "edge"
            target_path = path or settings.EDGE_QDRANT_PATH

            if target_path == ":memory:":
                self._temp_dir = tempfile.mkdtemp(prefix="cortex_edge_shard_")
                target_path = self._temp_dir
                logger.info("QdrantStore using temporary in-memory directory %s", target_path)

            self.shard_path = target_path
            self.shard = self._init_edge_shard(target_path)
            logger.info("QdrantStore initialised in Qdrant Edge mode at %s", target_path)

    def _init_edge_shard(self, path: str) -> qe.EdgeShard:
        """Create or load the Qdrant Edge shard with dense + sparse BM25 vectors."""
        os.makedirs(path, exist_ok=True)
        config = qe.EdgeConfig(
            vectors={"dense": qe.EdgeVectorParams(size=settings.VECTOR_SIZE, distance=qe.Distance.Cosine)},
            sparse_vectors={"bm25": qe.EdgeSparseVectorParams()},
        )
        try:
            return qe.EdgeShard.load(path, config)
        except Exception:
            try:
                return qe.EdgeShard.create(path, config)
            except Exception:
                # If create fails because segment files exist or format mismatch, reload without overwriting
                return qe.EdgeShard.load(path)

    def close(self) -> None:
        """Close storage connections and flush edge data to disk."""
        if self._mode == "cloud" and self.client:
            try:
                self.client.close()
            except Exception:
                pass
        elif self._mode == "edge" and self.shard:
            try:
                self.shard.flush()
                self.shard.close()
            except Exception as exc:
                logger.debug("EdgeShard close exception: %s", exc)
            if self._temp_dir and os.path.exists(self._temp_dir):
                try:
                    shutil.rmtree(self._temp_dir, ignore_errors=True)
                except Exception:
                    pass

    def optimize(self) -> None:
        """Trigger edge shard index optimization."""
        if self._mode == "edge" and self.shard:
            try:
                self.shard.optimize()
                logger.info("EdgeShard optimization complete.")
            except Exception as exc:
                logger.warning("EdgeShard optimization failed: %s", exc)

    # ── Collection management ────────────────────────────────────────────

    def ensure_collection(self) -> None:
        """Ensure collection/shard is ready."""
        if self._mode == "cloud" and self.client:
            collections = [c.name for c in self.client.get_collections().collections]
            if settings.COLLECTION_NAME not in collections:
                self.client.create_collection(
                    collection_name=settings.COLLECTION_NAME,
                    vectors_config=VectorParams(
                        size=settings.VECTOR_SIZE,
                        distance=Distance.COSINE,
                    ),
                )
                logger.info("Created cloud collection '%s'", settings.COLLECTION_NAME)

    def reset_collection(self) -> None:
        """Purge all points from the collection/shard."""
        if self._mode == "cloud" and self.client:
            try:
                all_pts = self.get_all_points(with_vectors=False)
                if all_pts:
                    self.delete_points([str(p.id) for p in all_pts])
            except Exception as exc:
                logger.warning("Failed to reset cloud collection: %s", exc)
            self.ensure_collection()
        elif self._mode == "edge" and self.shard:
            try:
                all_pts = self.get_all_points(with_vectors=False)
                if all_pts:
                    self.delete_points([str(p.id) for p in all_pts])
                self.optimize()
            except Exception as exc:
                logger.warning("Failed to reset edge shard: %s", exc)

    # ── Upsert & Anomaly Detection ───────────────────────────────────────

    def check_anomaly(self, vector: list[float], k: int = settings.ANOMALY_K) -> tuple[bool, float | None]:
        """
        Check if a vector is an anomaly by querying top-k nearest active neighbors.
        Uses fast index search (no full collection scan).
        If nearest cosine similarity < ANOMALY_THRESHOLD, flags as anomaly.
        """
        if self._mode == "cloud" and self.client:
            try:
                query_filter = CloudFilter(
                    must=[CloudFieldCondition(key="state", match=CloudMatchValue(value="active"))]
                )
                response = self.client.query_points(
                    collection_name=settings.COLLECTION_NAME,
                    query=vector,
                    query_filter=query_filter,
                    limit=k,
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

        elif self._mode == "edge" and self.shard:
            try:
                flt = qe.Filter(
                    must=[qe.FieldCondition(key="state", match=qe.MatchValue("active"))]
                )
                qr = qe.QueryRequest(
                    limit=k,
                    query=qe.Query.Nearest(vector, using="dense"),
                    filter=flt,
                    with_payload=False,
                    with_vector=False,
                )
                results = self.shard.query(qr)
                if not results:
                    return False, None
                nearest_score = float(results[0].score)
                is_anomaly = nearest_score < settings.ANOMALY_THRESHOLD
                return is_anomaly, round(nearest_score, 4)
            except Exception as exc:
                logger.debug("Edge check_anomaly lookup: %s", exc)
                return False, None

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
        Embed text (dense + sparse BM25), check for anomaly, and store memory.
        Returns (point_id, is_anomaly, nearest_similarity).
        """
        pid = ensure_uuid(point_id or str(uuid.uuid4()))
        dense_vec = vector or embedding_service.embed_text(text)

        is_anomaly = False
        nearest_sim = None
        if check_for_anomaly:
            is_anomaly, nearest_sim = self.check_anomaly(dense_vec)

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
        payload["state"] = "active"
        if payload_overrides:
            payload.update(payload_overrides)
            if "is_tombstone" in payload_overrides:
                payload["state"] = "tombstone" if payload_overrides["is_tombstone"] else "active"

        self.upsert_point(pid, dense_vec, payload)
        return pid, is_anomaly, nearest_sim

    def upsert_point(
        self,
        point_id: str,
        vector: list[float] | dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        """Upsert a single point into edge or cloud store."""
        pid = ensure_uuid(point_id)
        # Ensure state field is synchronized
        if "state" not in payload:
            payload["state"] = "tombstone" if payload.get("is_tombstone") else "active"

        if self._mode == "cloud" and self.client:
            vec = vector.get("dense") if isinstance(vector, dict) else vector
            self.client.upsert(
                collection_name=settings.COLLECTION_NAME,
                points=[PointStruct(id=pid, vector=vec, payload=payload)],
            )
        elif self._mode == "edge" and self.shard:
            text = payload.get("text", "")
            if isinstance(vector, dict):
                vectors_dict = dict(vector)
                if "bm25" not in vectors_dict and text:
                    vectors_dict["bm25"] = self._bm25.embed_document(text)
            else:
                vectors_dict = {
                    "dense": vector,
                    "bm25": self._bm25.embed_document(text) if text else self._bm25.embed_document(" "),
                }
            point = qe.Point(id=pid, vector=vectors_dict, payload=payload)
            self.shard.update(qe.UpdateOperation.upsert_points([point]))

    def upsert_points_batch(
        self,
        points: list[tuple[str, list[float] | dict[str, Any], dict[str, Any]]],
    ) -> None:
        """Batch upsert a list of (id, vector, payload) tuples."""
        if not points:
            return

        if self._mode == "cloud" and self.client:
            structs = []
            for pid, vec, pl in points:
                clean_id = ensure_uuid(pid)
                d_vec = vec.get("dense") if isinstance(vec, dict) else vec
                structs.append(PointStruct(id=clean_id, vector=d_vec, payload=pl))
            self.client.upsert(
                collection_name=settings.COLLECTION_NAME,
                points=structs,
            )
        elif self._mode == "edge" and self.shard:
            edge_points = []
            for pid, vec, pl in points:
                clean_id = ensure_uuid(pid)
                if "state" not in pl:
                    pl["state"] = "tombstone" if pl.get("is_tombstone") else "active"
                text = pl.get("text", "")
                if isinstance(vec, dict):
                    v_dict = dict(vec)
                    if "bm25" not in v_dict and text:
                        v_dict["bm25"] = self._bm25.embed_document(text)
                else:
                    v_dict = {
                        "dense": vec,
                        "bm25": self._bm25.embed_document(text) if text else self._bm25.embed_document(" "),
                    }
                edge_points.append(qe.Point(id=clean_id, vector=v_dict, payload=pl))
            self.shard.update(qe.UpdateOperation.upsert_points(edge_points))

    # ── Search ───────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        limit: int = 10,
        category: str | None = None,
        min_importance: float | None = None,
        include_local_only: bool = True,
        include_tombstones: bool = False,
        mode: str = "hybrid",
    ) -> list[MemoryResponse]:
        """
        Search memories using dense, sparse (BM25), or hybrid (RRF) mode.
        """
        dense_vec = embedding_service.embed_text(query)

        if self._mode == "cloud" and self.client:
            conditions = []
            if not include_tombstones:
                conditions.append(CloudFieldCondition(key="state", match=CloudMatchValue(value="active")))
            if category:
                conditions.append(CloudFieldCondition(key="category", match=CloudMatchValue(value=category)))
            if min_importance is not None:
                conditions.append(CloudFieldCondition(key="importance", range=CloudRange(gte=min_importance)))
            if not include_local_only:
                conditions.append(CloudFieldCondition(key="sync_eligibility", match=CloudMatchValue(value="sync_eligible")))

            search_filter = CloudFilter(must=conditions) if conditions else None
            try:
                response = self.client.query_points(
                    collection_name=settings.COLLECTION_NAME,
                    query=dense_vec,
                    query_filter=search_filter,
                    limit=limit,
                    with_payload=True,
                    with_vectors=False,
                )
                hits = response.points
            except Exception:
                hits = []
            return [self._hit_to_response(h) for h in hits]

        elif self._mode == "edge" and self.shard:
            must_conditions = []
            if not include_tombstones:
                must_conditions.append(qe.FieldCondition(key="state", match=qe.MatchValue("active")))
            if category:
                must_conditions.append(qe.FieldCondition(key="category", match=qe.MatchValue(category)))
            if not include_local_only:
                must_conditions.append(qe.FieldCondition(key="sync_eligibility", match=qe.MatchValue("sync_eligible")))

            edge_filter = qe.Filter(must=must_conditions) if must_conditions else None
            sparse_vec = self._bm25.embed_query(query)

            normalized_mode = mode.lower().strip()
            if normalized_mode == "sparse":
                qr = qe.QueryRequest(
                    limit=limit,
                    query=qe.Query.Nearest(sparse_vec, using="bm25"),
                    filter=edge_filter,
                    with_payload=True,
                    with_vector=False,
                )
            elif normalized_mode == "dense":
                qr = qe.QueryRequest(
                    limit=limit,
                    query=qe.Query.Nearest(dense_vec, using="dense"),
                    filter=edge_filter,
                    with_payload=True,
                    with_vector=False,
                )
            else:  # hybrid (RRF)
                qr = qe.QueryRequest(
                    limit=limit,
                    query=qe.Fusion.Rrf(k=60),
                    prefetches=[
                        qe.Prefetch(limit=limit * 2, query=qe.Query.Nearest(dense_vec, using="dense")),
                        qe.Prefetch(limit=limit * 2, query=qe.Query.Nearest(sparse_vec, using="bm25")),
                    ],
                    filter=edge_filter,
                    with_payload=True,
                    with_vector=False,
                )

            try:
                hits = self.shard.query(qr)
            except Exception as exc:
                logger.warning("Edge query failed (%s), falling back to dense: %s", normalized_mode, exc)
                qr_fallback = qe.QueryRequest(
                    limit=limit,
                    query=qe.Query.Nearest(dense_vec, using="dense"),
                    filter=edge_filter,
                    with_payload=True,
                    with_vector=False,
                )
                hits = self.shard.query(qr_fallback)

            if min_importance is not None:
                hits = [h for h in hits if (h.payload or {}).get("importance", 0.0) >= min_importance]

            return [self._hit_to_response(h) for h in hits[:limit]]

        return []

    # ── Retrieve / scroll ────────────────────────────────────────────────

    def get_all_points(self, with_vectors: bool = True) -> list[PointRecord]:
        """Scroll through every point in the collection or shard."""
        if self._mode == "cloud" and self.client:
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
                for p in points:
                    all_points.append(PointRecord(p.id, p.vector, p.payload))
                if next_offset is None:
                    break
                offset = next_offset
            return all_points

        elif self._mode == "edge" and self.shard:
            all_records: list[PointRecord] = []
            offset = None
            while True:
                req = qe.ScrollRequest(
                    offset=offset,
                    limit=256,
                    with_payload=True,
                    with_vector=with_vectors,
                )
                records, next_offset = self.shard.scroll(req)
                for r in records:
                    all_records.append(PointRecord(r.id, r.vector, r.payload))
                if next_offset is None or not records:
                    break
                offset = next_offset
            return all_records

        return []

    def get_point(self, point_id: str) -> PointRecord | None:
        """Retrieve a single point by ID."""
        clean_id = ensure_uuid(point_id)
        if self._mode == "cloud" and self.client:
            results = self.client.retrieve(
                collection_name=settings.COLLECTION_NAME,
                ids=[clean_id],
                with_payload=True,
                with_vectors=True,
            )
            if results:
                p = results[0]
                return PointRecord(p.id, p.vector, p.payload)
            return None

        elif self._mode == "edge" and self.shard:
            results = self.shard.retrieve(
                point_ids=[clean_id],
                with_payload=True,
                with_vector=True,
            )
            if results:
                r = results[0]
                return PointRecord(r.id, r.vector, r.payload)
            return None

        return None

    def count(self) -> int:
        """Total points in the collection or shard."""
        if self._mode == "cloud" and self.client:
            try:
                info = self.client.get_collection(settings.COLLECTION_NAME)
                return info.points_count or 0
            except Exception:
                return 0
        elif self._mode == "edge" and self.shard:
            try:
                return self.shard.count(qe.CountRequest(exact=True))
            except Exception:
                return 0
        return 0

    def count_active(self) -> int:
        """Count active (non-tombstoned) memories."""
        if self._mode == "cloud" and self.client:
            try:
                flt = CloudFilter(
                    must=[CloudFieldCondition(key="state", match=CloudMatchValue(value="active"))]
                )
                return self.client.count(settings.COLLECTION_NAME, count_filter=flt, exact=True).count
            except Exception:
                all_pts = self.get_all_points(with_vectors=False)
                return sum(1 for p in all_pts if p.payload.get("state") == "active" or not p.payload.get("is_tombstone", False))
        elif self._mode == "edge" and self.shard:
            try:
                flt = qe.Filter(must=[qe.FieldCondition(key="state", match=qe.MatchValue("active"))])
                return self.shard.count(qe.CountRequest(filter=flt, exact=True))
            except Exception:
                all_pts = self.get_all_points(with_vectors=False)
                return sum(1 for p in all_pts if p.payload.get("state") == "active" or not p.payload.get("is_tombstone", False))
        return 0

    # ── Delete & Update ───────────────────────────────────────────────────

    def delete_points(self, point_ids: Sequence[str]) -> None:
        """Delete specific points by ID."""
        if not point_ids:
            return
        clean_ids = [ensure_uuid(pid) for pid in point_ids]

        if self._mode == "cloud" and self.client:
            from qdrant_client.models import PointIdsList
            self.client.delete(
                collection_name=settings.COLLECTION_NAME,
                points_selector=PointIdsList(points=clean_ids),
            )
        elif self._mode == "edge" and self.shard:
            self.shard.update(qe.UpdateOperation.delete_points(clean_ids))

    def update_payload(self, point_id: str, payload_updates: dict[str, Any]) -> None:
        """Partial payload update for a single point."""
        clean_id = ensure_uuid(point_id)
        updates = dict(payload_updates)
        if "is_tombstone" in updates and "state" not in updates:
            updates["state"] = "tombstone" if updates["is_tombstone"] else "active"

        if self._mode == "cloud" and self.client:
            self.client.set_payload(
                collection_name=settings.COLLECTION_NAME,
                payload=updates,
                points=[clean_id],
            )
        elif self._mode == "edge" and self.shard:
            self.shard.update(qe.UpdateOperation.set_payload(point_ids=[clean_id], payload=updates))

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _hit_to_response(hit: Any) -> MemoryResponse:
        pl = getattr(hit, "payload", None) or {}
        score = getattr(hit, "score", None)
        return MemoryResponse(
            id=str(getattr(hit, "id", "")),
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
            is_tombstone=pl.get("is_tombstone", (pl.get("state") == "tombstone")),
            tombstone_at=pl.get("tombstone_at"),
            undo_until=pl.get("undo_until"),
            version_vector=pl.get("version_vector", {}),
            lamport_clock=pl.get("lamport_clock", 1),
            numeric_summary=pl.get("numeric_summary"),
            score=score,
        )

    @staticmethod
    def point_to_response(point: Any) -> MemoryResponse:
        if point is None:
            raise ValueError("Cannot convert None point to response")
        pl = getattr(point, "payload", None) or {}
        return MemoryResponse(
            id=str(getattr(point, "id", "")),
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
            is_tombstone=pl.get("is_tombstone", (pl.get("state") == "tombstone")),
            tombstone_at=pl.get("tombstone_at"),
            undo_until=pl.get("undo_until"),
            version_vector=pl.get("version_vector", {}),
            lamport_clock=pl.get("lamport_clock", 1),
            numeric_summary=pl.get("numeric_summary"),
        )
