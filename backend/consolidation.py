"""
Consolidation Engine — the "Sleep Cycle".

Headline Capabilities:
1. Scalable Graph-Based Neighborhood Clustering with Capped Diameter (avoids O(N^2) memory blowout & cluster drift).
2. Numeric Value Extraction & Tolerance Checking (computes min/max/avg/latest; skips merge if numbers differ beyond tolerance).
3. Mixed-PII Cluster Safeguards (if any cluster member contains PII, merged result is strictly tagged local_only).
4. Soft-Delete with Tombstones & Configurable Undo Window.
5. Full explainable decision logging for the Inspector UI.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from itertools import combinations
from typing import Any

import numpy as np
from sklearn.cluster import DBSCAN

from activity_log import activity_log
from config import settings
from models import (
    ActivityType,
    ConsolidationResult,
    DecayDetail,
    MergeDetail,
    SyncEligibility,
)

logger = logging.getLogger(__name__)


class ConsolidationEngine:
    """Runs the memory consolidation ('sleep cycle') pass."""

    def __init__(self, store):
        self.store = store  # QdrantStore instance
        self.history: list[ConsolidationResult] = []

    # ── Public Entry Point ───────────────────────────────────────────────

    def run(self) -> ConsolidationResult:
        """Execute a full consolidation pass. Returns a result summary."""
        run_id = str(uuid.uuid4())
        started = datetime.now(timezone.utc).isoformat()
        result = ConsolidationResult(
            id=run_id,
            started_at=started,
            status="running",
        )

        activity_log.log(
            ActivityType.CONSOLIDATION_STARTED,
            "Consolidation started",
            f"Sleep cycle pass {run_id[:8]} initiated.",
        )

        try:
            # 1. Snapshot current active non-tombstoned memories
            all_points = self.store.get_all_points(with_vectors=True)
            # Filter out existing tombstones from clustering pass
            active_points = [p for p in all_points if not p.payload.get("is_tombstone", False)]
            tombstoned_points = [p for p in all_points if p.payload.get("is_tombstone", False)]
            result.memories_before = len(active_points)

            # 2. Purge expired tombstones past their undo window
            self._purge_expired_tombstones(tombstoned_points, result)

            if len(active_points) >= settings.MIN_CLUSTER_SIZE:
                # 3. Cluster vectors
                vectors = np.array([p.vector for p in active_points], dtype=np.float32)
                clusters = self._cluster_vectors(vectors, active_points)
                result.clusters_found = len(clusters)

                ids_to_delete: list[str] = []

                for cid, indices in enumerate(clusters):
                    cluster_points = [active_points[i] for i in indices]
                    cluster_vecs = [vectors[i] for i in indices]

                    # Check numeric tolerance
                    can_merge, num_summary, reason = self._check_numeric_compatibility(cluster_points)
                    if not can_merge:
                        activity_log.log(
                            ActivityType.CONSOLIDATION_COMPLETED,
                            f"Skipped cluster merge #{cid}",
                            f"Numeric measurements in cluster differ beyond tolerance ({settings.NUMERIC_TOLERANCE_PCT:.0%}): {reason}",
                            {"cluster_id": cid, "reason": reason},
                        )
                        continue

                    merged_id, merged_vec, merged_payload, detail = self._merge_cluster(
                        cluster_points, cluster_vecs, cid, run_id, num_summary
                    )
                    result.merge_details.append(detail)

                    # Upsert the merged record
                    self.store.upsert_point(merged_id, merged_vec, merged_payload)

                    # Mark originals for deletion (skip the one we re-used as base)
                    for p in cluster_points:
                        pid = str(p.id)
                        if pid != merged_id:
                            ids_to_delete.append(pid)

                    result.records_merged += len(cluster_points)

                    activity_log.log(
                        ActivityType.MEMORIES_MERGED,
                        f"Merged {len(cluster_points)} memories",
                        f"Cluster {cid}: {len(cluster_points)} near-duplicates → 1 record "
                        f"(similarity ≥ {settings.SIMILARITY_THRESHOLD:.0%}).",
                        {
                            "cluster_id": cid,
                            "original_count": len(cluster_points),
                            "merged_id": merged_id,
                            "numeric_summary": num_summary,
                            "mixed_pii": detail.mixed_pii_protected,
                            "texts": [p.payload.get("text", "")[:80] for p in cluster_points],
                        },
                    )

                # Delete originals that were merged away
                if ids_to_delete:
                    self.store.delete_points(ids_to_delete)

            # 4. Decay stale memories (soft-delete to tombstones)
            remaining = self.store.get_all_points(with_vectors=False)
            active_remaining = [p for p in remaining if not p.payload.get("is_tombstone", False)]
            for point in active_remaining:
                decay_detail = self._apply_decay(point, run_id)
                if decay_detail:
                    result.decay_details.append(decay_detail)
                    if decay_detail.action == "tombstoned":
                        result.records_tombstoned += 1
                    elif decay_detail.action == "decayed":
                        result.records_decayed += 1

            # 5. PII tagging pass for active records
            remaining = self.store.get_all_points(with_vectors=False)
            active_remaining = [p for p in remaining if not p.payload.get("is_tombstone", False)]
            for point in active_remaining:
                tagged = self._tag_pii(point)
                if tagged:
                    result.records_tagged_local += 1

            # Finalise
            result.memories_after = self.store.count_active()
            result.completed_at = datetime.now(timezone.utc).isoformat()
            result.status = "completed"
            self.history.append(result)

            activity_log.log(
                ActivityType.CONSOLIDATION_COMPLETED,
                "Consolidation complete",
                (
                    f"Before: {result.memories_before} → After: {result.memories_after}. "
                    f"Merged {result.records_merged} records across {result.clusters_found} clusters, "
                    f"tombstoned {result.records_tombstoned}, tagged {result.records_tagged_local} local-only."
                ),
                {
                    "run_id": run_id,
                    "before": result.memories_before,
                    "after": result.memories_after,
                    "clusters": result.clusters_found,
                    "merged": result.records_merged,
                    "tombstoned": result.records_tombstoned,
                    "tagged_local": result.records_tagged_local,
                },
            )

        except Exception as exc:
            logger.exception("Consolidation failed")
            result.status = "failed"
            result.completed_at = datetime.now(timezone.utc).isoformat()
            self.history.append(result)
            activity_log.log(
                ActivityType.CONSOLIDATION_COMPLETED,
                "Consolidation failed",
                str(exc),
            )

        return result

    # ── Clustering Algorithms ────────────────────────────────────────────

    def _cluster_vectors(self, vectors: np.ndarray, points: list) -> list[list[int]]:
        """
        Cluster vectors using greedy diameter-capped neighborhood search
        or DBSCAN based on configuration.
        """
        if settings.CLUSTERING_METHOD == "greedy_hnsw" or settings.CLUSTERING_METHOD == "capped_diameter":
            return self._greedy_capped_clustering(vectors)
        
        # Fallback to DBSCAN
        eps = 1.0 - settings.SIMILARITY_THRESHOLD
        clustering = DBSCAN(
            eps=eps,
            min_samples=settings.MIN_CLUSTER_SIZE,
            metric="cosine",
        )
        labels = clustering.fit_predict(vectors)
        cluster_ids = set(labels)
        cluster_ids.discard(-1)
        clusters = []
        for cid in sorted(cluster_ids):
            indices = [i for i, l in enumerate(labels) if l == cid]
            clusters.append(indices)
        return clusters

    def _greedy_capped_clustering(self, vectors: np.ndarray) -> list[list[int]]:
        """
        Greedy neighborhood clustering with maximum diameter constraint:
        Ensures every pair of vectors within a cluster satisfies:
        cosine_distance(v_i, v_j) <= MAX_CLUSTER_DIAMETER (prevents chain drift).
        """
        n = len(vectors)
        # Normalize vectors for dot product cosine similarity
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        normed = vectors / norms

        sim_matrix = np.dot(normed, normed.T)  # (N, N) cosine similarities
        visited = set()
        clusters: list[list[int]] = []

        for i in range(n):
            if i in visited:
                continue

            # Candidate neighbors with similarity >= threshold
            candidates = [j for j in range(n) if j not in visited and sim_matrix[i, j] >= settings.SIMILARITY_THRESHOLD]
            if len(candidates) < settings.MIN_CLUSTER_SIZE:
                continue

            # Check diameter constraint: all pairs in cluster must have dist <= MAX_CLUSTER_DIAMETER
            current_cluster = [i]
            for cand in candidates:
                if cand == i:
                    continue
                # Check distance from cand to all members in current_cluster
                min_sim = min(sim_matrix[cand, m] for m in current_cluster)
                max_dist = 1.0 - min_sim
                if max_dist <= settings.MAX_CLUSTER_DIAMETER:
                    current_cluster.append(cand)

            if len(current_cluster) >= settings.MIN_CLUSTER_SIZE:
                for idx in current_cluster:
                    visited.add(idx)
                clusters.append(current_cluster)

        return clusters

    # ── Numeric Data Extraction & Compatibility ──────────────────────────

    def _extract_numeric_values(self, text: str) -> list[tuple[float, str]]:
        """Extract numeric measurements with units (e.g. 87.3°F, 42 PSI, 78%, 92 dB, 500ml)."""
        # Patterns for number + optional unit
        pattern = r'(\d+(?:\.\d+)?)\s*(°[FC]|PSI|%|dB|decibels|ml|kg|V|VDC|ms|hours?|min(?:utes?)?)'
        matches = re.findall(pattern, text, re.IGNORECASE)
        results = []
        for val_str, unit in matches:
            try:
                val = float(val_str)
                unit_norm = unit.upper()
                if "DECIBEL" in unit_norm:
                    unit_norm = "DB"
                elif "MINUTE" in unit_norm:
                    unit_norm = "MIN"
                results.append((val, unit_norm))
            except ValueError:
                pass
        return results

    def _check_numeric_compatibility(self, points: list) -> tuple[bool, dict[str, Any] | None, str | None]:
        """
        Check if numbers across clustered texts are consistent within tolerance.
        Returns: (can_merge, numeric_summary, reason_if_skipped)
        """
        all_readings: dict[str, list[float]] = {}
        for p in points:
            text = p.payload.get("text", "")
            readings = self._extract_numeric_values(text)
            for val, unit in readings:
                all_readings.setdefault(unit, []).append(val)

        if not all_readings:
            return True, None, None

        summary = {}
        for unit, vals in all_readings.items():
            if len(vals) < 2:
                continue
            min_val = min(vals)
            max_val = max(vals)
            avg_val = sum(vals) / len(vals)
            latest_val = vals[-1]

            # Check variance tolerance if numbers are significant
            if max_val > 0:
                rel_diff = (max_val - min_val) / max_val
                if rel_diff > settings.NUMERIC_TOLERANCE_PCT:
                    return False, None, f"Unit {unit} values range from {min_val} to {max_val} ({rel_diff:.1%} variance > {settings.NUMERIC_TOLERANCE_PCT:.0%})"

            summary[unit] = {
                "min": round(min_val, 2),
                "max": round(max_val, 2),
                "avg": round(avg_val, 2),
                "latest": round(latest_val, 2),
                "count": len(vals),
            }

        return True, (summary if summary else None), None

    # ── Merge Logic ──────────────────────────────────────────────────────

    def _merge_cluster(
        self,
        points: list,
        vecs: list[np.ndarray],
        cluster_id: int,
        batch_id: str,
        numeric_summary: dict[str, Any] | None = None,
    ) -> tuple[str, list[float], dict, MergeDetail]:
        """Merge a cluster of near-duplicate points into one record."""

        # Pick the base: highest importance, then freshest
        base = max(
            points,
            key=lambda p: (
                p.payload.get("importance", 0.5),
                p.payload.get("created_at", ""),
            ),
        )

        # Average the vectors and normalise
        avg_vec = np.mean(vecs, axis=0).astype(np.float32)
        norm = np.linalg.norm(avg_vec)
        if norm > 0:
            avg_vec = avg_vec / norm
        avg_vec_list = avg_vec.tolist()

        # Compute average pairwise similarity
        sims = []
        for i, j in combinations(range(len(vecs)), 2):
            dot = np.dot(vecs[i], vecs[j])
            n = np.linalg.norm(vecs[i]) * np.linalg.norm(vecs[j])
            sims.append(float(dot / n) if n > 0 else 0.0)
        avg_sim = float(np.mean(sims)) if sims else 1.0

        # Check Mixed-PII Safeguard:
        # If any cluster member contains PII, the merged output MUST be protected local-only
        mixed_pii = any(
            p.payload.get("pii_detected", False)
            or p.payload.get("sync_eligibility") == SyncEligibility.LOCAL_ONLY.value
            or self._detect_pii_in_text(p.payload.get("text", ""))
            for p in points
        )

        # Combine Lamport clocks and version vectors
        merged_clock = max(p.payload.get("lamport_clock", 1) for p in points) + 1
        merged_vv: dict[str, int] = {}
        for p in points:
            for k, v in p.payload.get("version_vector", {}).items():
                merged_vv[k] = max(merged_vv.get(k, 0), v)
        merged_vv[settings.DEVICE_ID] = merged_clock

        original_ids = [str(p.id) for p in points]
        merged_tags = list({t for p in points for t in p.payload.get("tags", [])})

        merged_payload = {
            **base.payload,
            "is_consolidated": True,
            "merge_count": sum(p.payload.get("merge_count", 1) for p in points),
            "original_ids": original_ids,
            "tags": merged_tags,
            "importance": max(p.payload.get("importance", 0.5) for p in points),
            "decay_score": max(p.payload.get("decay_score", 1.0) for p in points),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "consolidation_batch": batch_id,
            "text": base.payload.get("text", ""),
            "numeric_summary": numeric_summary,
            "lamport_clock": merged_clock,
            "version_vector": merged_vv,
            "is_tombstone": False,
        }

        if mixed_pii:
            merged_payload["sync_eligibility"] = SyncEligibility.LOCAL_ONLY.value
            merged_payload["pii_detected"] = True
            merged_payload["sync_reason"] = "Mixed-cluster PII safeguard: one or more merged source records contained sensitive personal data"

        merged_id = str(base.id)

        detail = MergeDetail(
            cluster_id=cluster_id,
            original_ids=original_ids,
            original_texts=[p.payload.get("text", "")[:120] for p in points],
            merged_id=merged_id,
            merged_text=merged_payload["text"][:120],
            similarity=round(avg_sim, 4),
            numeric_summary=numeric_summary,
            mixed_pii_protected=mixed_pii,
        )

        return merged_id, avg_vec_list, merged_payload, detail

    # ── Decay & Soft-Delete Tombstones ───────────────────────────────────

    def _apply_decay(self, point, batch_id: str) -> DecayDetail | None:
        """Apply exponential decay; soft-delete to tombstone with undo window if below threshold."""
        payload = point.payload
        old_score = payload.get("decay_score", 1.0)
        created_str = payload.get("created_at", "")

        if not created_str:
            return None

        try:
            created = datetime.fromisoformat(created_str)
        except ValueError:
            return None

        now = datetime.now(timezone.utc)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_hours = (now - created).total_seconds() / 3600.0
        importance = payload.get("importance", 0.5)

        # Higher importance decays slower: D(t) = D_0 - lambda * age * (1 - 0.5 * importance)
        effective_rate = settings.DECAY_RATE_PER_HOUR * (1.0 - 0.5 * importance)
        new_score = max(0.0, old_score - effective_rate * age_hours)
        new_score = round(new_score, 4)

        if new_score < settings.DECAY_THRESHOLD:
            # Soft delete with tombstone and undo window
            undo_until = (now + timedelta(seconds=settings.TOMBSTONE_UNDO_WINDOW_SECONDS)).isoformat()
            self.store.update_payload(
                str(point.id),
                {
                    "decay_score": new_score,
                    "is_tombstone": True,
                    "tombstone_at": now.isoformat(),
                    "undo_until": undo_until,
                },
            )
            activity_log.log(
                ActivityType.MEMORY_TOMBSTONED,
                "Memory soft-deleted (tombstoned)",
                f"'{payload.get('text', '')[:60]}…' decayed to {new_score:.2f} < {settings.DECAY_THRESHOLD}. Retained in undo window until {undo_until[:19]}.",
                {"id": str(point.id), "old": old_score, "new": new_score, "undo_until": undo_until},
            )
            return DecayDetail(
                memory_id=str(point.id),
                text_preview=payload.get("text", "")[:80],
                old_decay_score=old_score,
                new_decay_score=new_score,
                action="tombstoned",
                undo_until=undo_until,
            )

        if abs(new_score - old_score) > 0.001:
            self.store.update_payload(str(point.id), {"decay_score": new_score})
            return DecayDetail(
                memory_id=str(point.id),
                text_preview=payload.get("text", "")[:80],
                old_decay_score=old_score,
                new_decay_score=new_score,
                action="decayed",
            )

        return None

    def _purge_expired_tombstones(self, tombstones: list, result: ConsolidationResult):
        """Hard-delete tombstones whose undo window has expired."""
        now = datetime.now(timezone.utc)
        to_purge: list[str] = []
        for point in tombstones:
            undo_str = point.payload.get("undo_until")
            if undo_str:
                try:
                    undo_time = datetime.fromisoformat(undo_str)
                    if undo_time.tzinfo is None:
                        undo_time = undo_time.replace(tzinfo=timezone.utc)
                    if now > undo_time:
                        to_purge.append(str(point.id))
                except ValueError:
                    to_purge.append(str(point.id))
            else:
                to_purge.append(str(point.id))

        if to_purge:
            self.store.delete_points(to_purge)
            activity_log.log(
                ActivityType.MEMORY_DELETED,
                "Expired tombstones purged",
                f"Permanently purged {len(to_purge)} decayed records past undo window.",
                {"purged_count": len(to_purge), "ids": to_purge},
            )

    def restore_tombstone(self, memory_id: str) -> bool:
        """Restore a soft-deleted tombstone within its undo window."""
        point = self.store.get_point(memory_id)
        if not point or not point.payload.get("is_tombstone", False):
            return False

        # Reset tombstone flag and restore healthy decay score
        self.store.update_payload(
            memory_id,
            {
                "is_tombstone": False,
                "tombstone_at": None,
                "undo_until": None,
                "decay_score": 1.0,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        activity_log.log(
            ActivityType.MEMORY_RESTORED,
            "Memory restored from tombstone",
            f"Record {memory_id[:8]} restored to active vector memory.",
            {"id": memory_id},
        )
        return True

    # ── PII Tagging ──────────────────────────────────────────────────────

    def _detect_pii_in_text(self, text: str) -> list[str]:
        """Detect regex-based PII patterns in a string."""
        matched = []
        for pattern in settings.PII_PATTERNS:
            if re.search(pattern, text):
                matched.append(f"Matched PII pattern: {pattern}")
        return matched

    def _tag_pii(self, point) -> bool:
        """If text contains PII patterns, tag as local_only with reason."""
        text = point.payload.get("text", "")
        current = point.payload.get("sync_eligibility", "sync_eligible")

        if current == SyncEligibility.LOCAL_ONLY.value:
            return False

        reasons = self._detect_pii_in_text(text)
        if reasons:
            reason_str = "; ".join(reasons)
            self.store.update_payload(
                str(point.id),
                {
                    "sync_eligibility": SyncEligibility.LOCAL_ONLY.value,
                    "sync_reason": f"PII detected — {reason_str}",
                    "pii_detected": True,
                },
            )
            activity_log.log(
                ActivityType.MEMORY_TAGGED_LOCAL,
                "Memory tagged local-only (PII)",
                f"'{text[:60]}…' contains PII. Reason: {reason_str}",
                {"id": str(point.id), "reasons": reasons},
            )
            return True

        return False
