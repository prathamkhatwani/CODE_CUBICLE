"""
Sync Agent — bi-directional edge ↔ cloud synchronisation.

Capabilities:
• Detects connectivity by pinging the cloud Qdrant instance with auto fallback.
• Fast-path priority sync for novel anomalies (Sim < 0.70) without waiting for sleep cycle.
• Version Vector & Lamport Logical Clock conflict resolution (eliminates device wall-clock skew).
• Exponential backoff retry on network drops.
• Idempotent payload tagging and bi-directional vector reconciliation.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import numpy as np

from activity_log import activity_log
from config import settings
from embedding import embedding_service
from models import ActivityType, SyncEligibility, SyncResult

import json
from pathlib import Path

logger = logging.getLogger(__name__)


class SyncAgent:
    """Manages edge-to-cloud and cloud-to-edge synchronisation with disk-persisted offline queue."""

    def __init__(self, edge_store, cloud_store=None):
        self.edge = edge_store
        self.cloud = cloud_store  # May be None if cloud not configured
        self.is_online: bool = False
        self.history: list[SyncResult] = []
        self._simulated_offline: bool = False
        self._queue_file = Path(settings.EDGE_QDRANT_PATH) / "offline_sync_queue.json"
        self._ensure_queue_file()

    def _ensure_queue_file(self) -> None:
        """Ensure the offline queue file directory exists."""
        try:
            self._queue_file.parent.mkdir(parents=True, exist_ok=True)
            if not self._queue_file.exists():
                self._save_queue([])
        except Exception:
            pass

    def _load_queue(self) -> list[dict[str, Any]]:
        """Load queued record descriptors from disk."""
        try:
            if self._queue_file.exists():
                with open(self._queue_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as exc:
            logger.debug("Failed to read offline queue from disk: %s", exc)
        return []

    def _save_queue(self, queue: list[dict[str, Any]]) -> None:
        """Persist offline queue descriptors to disk."""
        try:
            self._queue_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._queue_file, "w", encoding="utf-8") as f:
                json.dump(queue, f, indent=2)
        except Exception as exc:
            logger.debug("Failed to save offline queue to disk: %s", exc)

    def enqueue_offline_record(self, point_id: str, priority: str = "normal", reason: str | None = None) -> None:
        """Enqueue an unsynced record to disk queue while offline."""
        queue = self._load_queue()
        # Avoid duplicates
        if not any(item.get("id") == point_id for item in queue):
            queue.append({
                "id": point_id,
                "priority": priority,
                "reason": reason,
                "enqueued_at": datetime.now(timezone.utc).isoformat(),
            })
            self._save_queue(queue)
            logger.info("Enqueued record %s to disk offline queue (priority=%s)", point_id[:8], priority)

    def get_queue_status(self) -> dict[str, Any]:
        """Return offline queue metrics."""
        disk_queue = self._load_queue()
        all_pts = self.edge.get_all_points(with_vectors=False)
        unsynced = [
            p for p in all_pts
            if not p.payload.get("is_tombstone", False)
            and p.payload.get("sync_eligibility") == SyncEligibility.SYNC_ELIGIBLE.value
            and not p.payload.get("synced", False)
            and p.payload.get("origin") != "cloud"
        ]
        anomalies = [p for p in unsynced if p.payload.get("priority") == "high"]
        return {
            "queue_length": len(unsynced),
            "anomalies_queued": len(anomalies),
            "disk_journal_count": len(disk_queue),
            "is_online": self.is_online,
            "items": [
                {
                    "id": str(p.id),
                    "text": p.payload.get("text", "")[:70],
                    "priority": p.payload.get("priority", "normal"),
                    "category": p.payload.get("category", "general"),
                }
                for p in unsynced[:20]
            ],
        }

    # ── Connectivity ─────────────────────────────────────────────────────

    def check_connectivity(self) -> bool:
        """Ping cloud Qdrant. Returns True if reachable. Auto-flushes queue on reconnect."""
        if self._simulated_offline:
            self.is_online = False
            return False

        if self.cloud is None:
            self.is_online = False
            return False

        try:
            if self.cloud.client is not None:
                self.cloud.client.get_collections()
            elif self.cloud.shard is not None:
                self.cloud.count()
            was_online = self.is_online
            self.is_online = True
            if not was_online:
                activity_log.log(
                    ActivityType.CONNECTIVITY_CHANGED,
                    "Connectivity restored",
                    f"Cloud Qdrant at {settings.CLOUD_QDRANT_URL} is reachable. Flushing offline queue by priority.",
                )
                # Auto-flush offline queue upon reconnection
                self.flush_offline_queue()
            return True
        except Exception:
            was_online = self.is_online
            self.is_online = False
            if was_online:
                activity_log.log(
                    ActivityType.CONNECTIVITY_CHANGED,
                    "Connectivity lost",
                    f"Cloud Qdrant at {settings.CLOUD_QDRANT_URL} is unreachable. Entering air-gapped offline queue mode.",
                )
            return False

    def flush_offline_queue(self) -> int:
        """Flush queued offline records to cloud in priority order (anomalies first)."""
        if not self.is_online or self.cloud is None:
            return 0

        all_points = self.edge.get_all_points(with_vectors=True)
        eligible = [
            p for p in all_points
            if not p.payload.get("is_tombstone", False)
            and p.payload.get("sync_eligibility") == SyncEligibility.SYNC_ELIGIBLE.value
            and not p.payload.get("synced", False)
            and p.payload.get("origin") != "cloud"
        ]
        if not eligible:
            self._save_queue([])
            return 0

        # Sort priority high (anomalies) first
        eligible.sort(
            key=lambda p: (
                0 if p.payload.get("priority") == "high" else 1,
                p.payload.get("created_at", ""),
            )
        )

        try:
            self.cloud.ensure_collection()
            points_data = []
            for p in eligible:
                pl = dict(p.payload)
                pl["source_device"] = settings.DEVICE_ID
                pl["synced_at"] = datetime.now(timezone.utc).isoformat()
                points_data.append((str(p.id), list(p.vector), pl))

            self.cloud.upsert_points_batch(points_data)

            for p in eligible:
                self.edge.update_payload(
                    str(p.id),
                    {
                        "synced": True,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                )

            anomalies_count = sum(1 for p in eligible if p.payload.get("priority") == "high")
            self._save_queue([])

            activity_log.log(
                ActivityType.SYNC_COMPLETED,
                "Offline Queue Flushed",
                f"Flushed {len(eligible)} offline records ({anomalies_count} high-priority anomalies dispatched first).",
                {"total_flushed": len(eligible), "anomalies_flushed": anomalies_count},
            )
            return len(eligible)
        except Exception as exc:
            logger.warning("Failed to flush offline queue: %s", exc)
            return 0

    def toggle_simulated_connectivity(self) -> bool:
        """For demo: toggle simulated offline/online."""
        self._simulated_offline = not self._simulated_offline
        online = not self._simulated_offline
        self.is_online = online
        state = "online" if online else "offline"
        activity_log.log(
            ActivityType.CONNECTIVITY_CHANGED,
            f"Simulated {state}",
            f"Connectivity simulation toggled to {state}.",
        )
        return online

    # ── Sync entry point ─────────────────────────────────────────────────

    def sync(self) -> SyncResult:
        """Run a full push-pull sync cycle."""
        result = SyncResult(
            started_at=datetime.now(timezone.utc).isoformat(),
            status="running",
        )

        activity_log.log(
            ActivityType.SYNC_STARTED,
            "Sync started",
            "Beginning edge → cloud sync cycle.",
        )

        # Check connectivity
        if not self.check_connectivity():
            result.status = "failed"
            result.errors.append("Cloud Qdrant is unreachable.")
            result.completed_at = datetime.now(timezone.utc).isoformat()
            self.history.append(result)
            activity_log.log(
                ActivityType.SYNC_FAILED,
                "Sync failed — offline",
                "Cannot reach cloud Qdrant. Records queued for next sync.",
            )
            return result

        try:
            # Ensure cloud collection exists
            self.cloud.ensure_collection()

            # ── Push: edge → cloud ───────────────────────────────────
            pushed = self._push_to_cloud(result)
            result.records_pushed = pushed

            # ── Pull: cloud → edge ───────────────────────────────────
            pulled, conflicts = self._pull_from_cloud(result)
            result.records_pulled = pulled
            result.conflicts_resolved = conflicts

            result.status = "completed"
            result.completed_at = datetime.now(timezone.utc).isoformat()
            self.history.append(result)

            activity_log.log(
                ActivityType.SYNC_COMPLETED,
                "Sync completed",
                (
                    f"Pushed {pushed} records to cloud, pulled {pulled} from cloud. "
                    f"Resolved {conflicts} conflicts."
                ),
                {
                    "pushed": pushed,
                    "pulled": pulled,
                    "conflicts": conflicts,
                    "conflict_details": result.conflict_details,
                },
            )

        except Exception as exc:
            logger.exception("Sync failed")
            result.status = "failed"
            result.errors.append(str(exc))
            result.completed_at = datetime.now(timezone.utc).isoformat()
            self.history.append(result)
            activity_log.log(
                ActivityType.SYNC_FAILED,
                "Sync failed",
                str(exc),
            )

        return result

    # ── Fast-path Priority Sync (for Anomalies) ──────────────────────────

    def sync_priority_fast_path(self, point_id: str | None = None) -> bool:
        """
        Fast-path sync for high-priority / anomalous memories.
        Immediately pushes the item to central cloud without waiting
        for the scheduled consolidation/sleep cycle.
        """
        if not self.check_connectivity():
            logger.info("Cannot run priority fast-path sync: device is offline or cloud unreachable.")
            result = SyncResult(
                started_at=datetime.now(timezone.utc).isoformat(),
                completed_at=datetime.now(timezone.utc).isoformat(),
                records_pushed=0,
                records_pulled=0,
                conflicts_resolved=0,
                status="failed",
                errors=["Cloud unreachable — high-priority anomaly placed in priority queue for instant push upon reconnection."],
            )
            self.history.append(result)
            return False

        try:
            points_to_sync = []
            if point_id:
                p = self.edge.get_point(point_id)
                if (
                    p
                    and not p.payload.get("is_tombstone", False)
                    and p.payload.get("sync_eligibility") == SyncEligibility.SYNC_ELIGIBLE.value
                    and not p.payload.get("synced", False)
                ):
                    points_to_sync.append(p)
            else:
                all_points = self.edge.get_all_points(with_vectors=True)
                points_to_sync = [
                    p for p in all_points
                    if not p.payload.get("is_tombstone", False)
                    and p.payload.get("priority") == "high"
                    and p.payload.get("sync_eligibility") == SyncEligibility.SYNC_ELIGIBLE.value
                    and not p.payload.get("synced", False)
                ]

            if not points_to_sync:
                return False

            self.cloud.ensure_collection()

            points_data = []
            for p in points_to_sync:
                payload = dict(p.payload)
                payload["source_device"] = settings.DEVICE_ID
                payload["synced_at"] = datetime.now(timezone.utc).isoformat()
                payload["sync_path"] = "priority_fast_path"
                # Update version vector
                vv = payload.get("version_vector", {})
                vv[settings.DEVICE_ID] = vv.get(settings.DEVICE_ID, 0) + 1
                payload["version_vector"] = vv
                payload["lamport_clock"] = payload.get("lamport_clock", 1) + 1
                points_data.append((str(p.id), list(p.vector), payload))

            self.cloud.upsert_points_batch(points_data)

            # Mark as synced on edge
            for p in points_to_sync:
                self.edge.update_payload(
                    str(p.id),
                    {
                        "synced": True,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                )

            result = SyncResult(
                started_at=datetime.now(timezone.utc).isoformat(),
                completed_at=datetime.now(timezone.utc).isoformat(),
                records_pushed=len(points_to_sync),
                records_pulled=0,
                conflicts_resolved=0,
                status="completed",
                errors=[],
            )
            self.history.append(result)

            activity_log.log(
                ActivityType.PRIORITY_SYNC,
                "Priority sync triggered (Fast-path)",
                f"Anomaly memory '{points_to_sync[0].payload.get('text', '')[:65]}…' pushed immediately to cloud.",
                {
                    "count": len(points_to_sync),
                    "point_ids": [str(p.id) for p in points_to_sync],
                    "reason": "anomaly_fast_path",
                },
            )
            return True

        except Exception as exc:
            logger.warning("Priority fast-path sync failed: %s", exc)
            result = SyncResult(
                started_at=datetime.now(timezone.utc).isoformat(),
                completed_at=datetime.now(timezone.utc).isoformat(),
                records_pushed=0,
                records_pulled=0,
                conflicts_resolved=0,
                status="failed",
                errors=[f"Priority fast-path failed: {exc}"],
            )
            self.history.append(result)
            return False

    # ── Push (edge → cloud) ──────────────────────────────────────────────

    def _push_to_cloud(self, result: SyncResult) -> int:
        """Push sync-eligible, unsynced active records from edge to cloud."""
        all_points = self.edge.get_all_points(with_vectors=True)

        eligible = [
            p for p in all_points
            if not p.payload.get("is_tombstone", False)
            and p.payload.get("sync_eligibility") == SyncEligibility.SYNC_ELIGIBLE.value
            and not p.payload.get("synced", False)
            and p.payload.get("origin") != "cloud"
        ]

        if not eligible:
            return 0

        # High priority items first
        eligible.sort(
            key=lambda p: (
                0 if p.payload.get("priority") == "high" else 1,
                p.payload.get("created_at", ""),
            )
        )

        pushed = 0
        for i in range(0, len(eligible), settings.SYNC_BATCH_SIZE):
            batch = eligible[i : i + settings.SYNC_BATCH_SIZE]
            retries = 0
            while retries < settings.RETRY_MAX:
                try:
                    points_data = []
                    for p in batch:
                        payload = dict(p.payload)
                        payload["source_device"] = settings.DEVICE_ID
                        payload["synced_at"] = datetime.now(timezone.utc).isoformat()
                        points_data.append((str(p.id), list(p.vector), payload))

                    self.cloud.upsert_points_batch(points_data)

                    # Mark as synced on edge
                    for p in batch:
                        self.edge.update_payload(
                            str(p.id),
                            {
                                "synced": True,
                                "updated_at": datetime.now(timezone.utc).isoformat(),
                            },
                        )
                    pushed += len(batch)
                    break
                except Exception as exc:
                    retries += 1
                    if retries >= settings.RETRY_MAX:
                        result.errors.append(
                            f"Batch {i // settings.SYNC_BATCH_SIZE} failed after {retries} retries: {exc}"
                        )
                        break
                    backoff = settings.RETRY_BACKOFF_BASE ** retries
                    logger.warning("Push retry %d, backoff %.1fs: %s", retries, backoff, exc)
                    time.sleep(min(backoff, 30))

        return pushed

    # ── Pull (cloud → edge) ──────────────────────────────────────────────

    def _pull_from_cloud(self, result: SyncResult) -> tuple[int, int]:
        """Pull records from cloud and merge into edge store using Version Vector conflict resolution."""
        cloud_points = self.cloud.get_all_points(with_vectors=True)
        if not cloud_points:
            return 0, 0

        pulled = 0
        conflicts = 0

        for cp in cloud_points:
            cp_id = str(cp.id)
            cp_device = cp.payload.get("source_device", "")

            # Skip records that originated from this device and have no external edits
            if cp_device == settings.DEVICE_ID and not cp.payload.get("has_remote_updates", False):
                continue

            local = self.edge.get_point(cp_id)

            if local is None:
                # New record from another device — insert locally with origin="cloud"
                cp_payload = dict(cp.payload)
                cp_payload["origin"] = "cloud"
                cp_payload["synced"] = True
                self.edge.upsert_point(cp_id, list(cp.vector), cp_payload)
                pulled += 1
            else:
                # Same record exists both locally and in cloud — check Version Vectors
                resolved_payload, resolution_info = self._resolve_version_vector_conflict(local, cp)
                if resolved_payload:
                    # Average vectors for semantic convergence
                    avg_vec = embedding_service.average_vectors(
                        [list(local.vector), list(cp.vector)]
                    )
                    self.edge.upsert_point(cp_id, avg_vec, resolved_payload)
                    conflicts += 1
                    result.conflict_details.append({
                        "record_id": cp_id,
                        "local_device": settings.DEVICE_ID,
                        "remote_device": cp_device,
                        "resolution": resolution_info,
                    })
                    activity_log.log(
                        ActivityType.CONFLICT_RESOLVED,
                        "Conflict resolved (Version Vector)",
                        (
                            f"Record {cp_id[:8]} edited concurrently. Resolved via {resolution_info}."
                        ),
                        {
                            "record_id": cp_id,
                            "local_device": settings.DEVICE_ID,
                            "remote_device": cp_device,
                            "resolution": resolution_info,
                        },
                    )

        return pulled, conflicts

    def pull_fleet_knowledge(self, query: str | None = None, limit: int = 10) -> int:
        """
        Pull relevant fleet knowledge from cloud Qdrant using recent context query.
        Cached locally with origin='cloud' and marked synced=True so it is never re-pushed.
        """
        if not self.check_connectivity() or not self.cloud:
            return 0

        if not query:
            recent_local = self.edge.get_all_points(with_vectors=False)
            if recent_local:
                recent_local.sort(key=lambda p: p.payload.get("created_at", ""), reverse=True)
                query = recent_local[0].payload.get("text", "warehouse maintenance incident")
            else:
                query = "warehouse equipment safety diagnostics"

        cloud_hits = self.cloud.search(query=query, limit=limit)
        pulled = 0
        for hit in cloud_hits:
            hit_id = str(hit.id)
            local_existing = self.edge.get_point(hit_id)
            if local_existing is None:
                pl = hit.model_dump()
                pl["origin"] = "cloud"
                pl["synced"] = True
                d_vec = embedding_service.embed_text(hit.text)
                self.edge.upsert_point(hit_id, d_vec, pl)
                pulled += 1

        if pulled > 0:
            activity_log.log(
                ActivityType.SYNC_COMPLETED,
                "Fleet knowledge pulled",
                f"Pulled {pulled} relevant fleet records from central cloud knowledge base (query: '{query[:40]}…'). Cached with origin='cloud'.",
                {"pulled_count": pulled, "query": query},
            )
        return pulled

    # ── Conflict Resolution: Version Vectors & Lamport Clocks ────────────

    @staticmethod
    def _compare_version_vectors(vv_a: dict[str, int], vv_b: dict[str, int]) -> str:
        """
        Compare two version vectors:
        Returns:
          - "A_DOMINATES" if A >= B for all keys and A > B for at least one
          - "B_DOMINATES" if B >= A for all keys and B > A for at least one
          - "IDENTICAL" if A == B
          - "CONCURRENT" if neither dominates (split-brain concurrent edits)
        """
        all_keys = set(vv_a.keys()) | set(vv_b.keys())
        a_greater = False
        b_greater = False

        for k in all_keys:
            v_a = vv_a.get(k, 0)
            v_b = vv_b.get(k, 0)
            if v_a > v_b:
                a_greater = True
            elif v_b > v_a:
                b_greater = True

        if a_greater and not b_greater:
            return "A_DOMINATES"
        elif b_greater and not a_greater:
            return "B_DOMINATES"
        elif not a_greater and not b_greater:
            return "IDENTICAL"
        else:
            return "CONCURRENT"

    @classmethod
    def _resolve_version_vector_conflict(
        cls, local_point, cloud_point
    ) -> tuple[dict[str, Any] | None, str]:
        """
        Resolve concurrency using Version Vectors + Lamport Logical Clocks.
        """
        lp = local_point.payload or {}
        cp = cloud_point.payload or {}

        vv_local = lp.get("version_vector", {settings.DEVICE_ID: lp.get("lamport_clock", 1)})
        vv_cloud = cp.get("version_vector", {cp.get("source_device", "server"): cp.get("lamport_clock", 1)})

        relation = cls._compare_version_vectors(vv_local, vv_cloud)

        if relation == "IDENTICAL":
            return None, "identical_version_vectors"

        if relation == "A_DOMINATES":
            # Local is strictly newer; keep local
            return None, "local_dominates_version_vector"

        if relation == "B_DOMINATES":
            # Cloud is strictly newer; adopt cloud payload
            adopted = dict(cp)
            adopted["synced"] = True
            return adopted, "cloud_dominates_version_vector"

        # CONCURRENT (split-brain): Merge metadata and choose best text
        merged_vv = {}
        all_keys = set(vv_local.keys()) | set(vv_cloud.keys())
        for k in all_keys:
            merged_vv[k] = max(vv_local.get(k, 0), vv_cloud.get(k, 0))

        merged_clock = max(lp.get("lamport_clock", 1), cp.get("lamport_clock", 1)) + 1
        merged_vv[settings.DEVICE_ID] = merged_clock

        # Deterministic winner: highest importance, then consolidated, then longest text
        score_local = (lp.get("importance", 0.5) * 2.0) + (1.0 if lp.get("is_consolidated") else 0.0)
        score_cloud = (cp.get("importance", 0.5) * 2.0) + (1.0 if cp.get("is_consolidated") else 0.0)

        base = lp if score_local >= score_cloud else cp

        merged = dict(base)
        merged["tags"] = list(set(lp.get("tags", []) + cp.get("tags", [])))
        merged["importance"] = max(lp.get("importance", 0.5), cp.get("importance", 0.5))
        merged["merge_count"] = lp.get("merge_count", 1) + cp.get("merge_count", 1)
        merged["version_vector"] = merged_vv
        merged["lamport_clock"] = merged_clock
        merged["is_consolidated"] = True
        merged["synced"] = True
        merged["updated_at"] = datetime.now(timezone.utc).isoformat()

        # Mixed PII check
        if lp.get("pii_detected") or cp.get("pii_detected"):
            merged["pii_detected"] = True
            merged["sync_eligibility"] = SyncEligibility.LOCAL_ONLY.value

        return merged, "concurrent_merged_version_vectors"

    # ── Stats helpers ────────────────────────────────────────────────────

    def get_pending_count(self) -> int:
        """How many records are waiting to sync."""
        all_pts = self.edge.get_all_points(with_vectors=False)
        return sum(
            1 for p in all_pts
            if not p.payload.get("is_tombstone", False)
            and p.payload.get("sync_eligibility") == SyncEligibility.SYNC_ELIGIBLE.value
            and not p.payload.get("synced", False)
        )

    def get_synced_count(self) -> int:
        all_pts = self.edge.get_all_points(with_vectors=False)
        return sum(1 for p in all_pts if not p.payload.get("is_tombstone", False) and p.payload.get("synced", False))
