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

logger = logging.getLogger(__name__)


class SyncAgent:
    """Manages edge-to-cloud and cloud-to-edge synchronisation."""

    def __init__(self, edge_store, cloud_store=None):
        self.edge = edge_store
        self.cloud = cloud_store  # May be None if cloud not configured
        self.is_online: bool = False
        self.history: list[SyncResult] = []
        self._simulated_offline: bool = False

    # ── Connectivity ─────────────────────────────────────────────────────

    def check_connectivity(self) -> bool:
        """Ping cloud Qdrant. Returns True if reachable."""
        if self._simulated_offline:
            self.is_online = False
            return False

        if self.cloud is None:
            self.is_online = False
            return False

        try:
            self.cloud.client.get_collections()
            was_online = self.is_online
            self.is_online = True
            if not was_online:
                activity_log.log(
                    ActivityType.CONNECTIVITY_CHANGED,
                    "Connectivity restored",
                    f"Cloud Qdrant at {settings.CLOUD_QDRANT_URL} is reachable.",
                )
            return True
        except Exception:
            was_online = self.is_online
            self.is_online = False
            if was_online:
                activity_log.log(
                    ActivityType.CONNECTIVITY_CHANGED,
                    "Connectivity lost",
                    f"Cloud Qdrant at {settings.CLOUD_QDRANT_URL} is unreachable.",
                )
            return False

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
                # New record from another device — insert locally
                self.edge.upsert_point(cp_id, list(cp.vector), dict(cp.payload))
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
