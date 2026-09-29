"""
Consolidation Engine — the "Sleep Cycle".

This is the headline feature.  During idle / on-demand trigger the engine:

1. Clusters local vectors by cosine similarity (DBSCAN) to detect near-duplicates.
2. Merges each cluster into one higher-quality record (averaged vector, best text,
   combined metadata).
3. Decays stale / low-value memories — drops them if below threshold.
4. Tags PII-containing records as local_only with an explainable reason.
5. Logs every decision so the Inspector UI can surface *why*.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from datetime import datetime, timezone

import numpy as np
from sklearn.cluster import DBSCAN

from activity_log import activity_log
from config import settings
from embedding import embedding_service
from models import (
    ActivityType,
    ConsolidationResult,
    DecayDetail,
    MergeDetail,
    SyncEligibility,
)

logger = logging.getLogger(__name__)


class ConsolidationEngine:
    """Runs the memory consolidation ("sleep cycle") pass."""

    def __init__(self, store):
        self.store = store  # QdrantStore instance
        self.history: list[ConsolidationResult] = []

    # ── Public entry point ───────────────────────────────────────────────

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
            # 1. Snapshot current state
            all_points = self.store.get_all_points(with_vectors=True)
            result.memories_before = len(all_points)

            if len(all_points) < 2:
                result.status = "completed"
                result.completed_at = datetime.now(timezone.utc).isoformat()
                result.memories_after = len(all_points)
                self.history.append(result)
                return result

            # 2. Extract vectors and run DBSCAN clustering
            vectors = np.array(
                [p.vector for p in all_points], dtype=np.float32
            )
            # cosine distance = 1 - cosine_similarity
            eps = 1.0 - settings.SIMILARITY_THRESHOLD
            clustering = DBSCAN(
                eps=eps,
                min_samples=settings.MIN_CLUSTER_SIZE,
                metric="cosine",
            )
            labels = clustering.fit_predict(vectors)

            # 3. Merge near-duplicate clusters
            cluster_ids = set(labels)
            cluster_ids.discard(-1)  # -1 = noise / singletons — keep as-is
            result.clusters_found = len(cluster_ids)

            ids_to_delete: list[str] = []

            for cid in sorted(cluster_ids):
                indices = [i for i, l in enumerate(labels) if l == cid]
                cluster_points = [all_points[i] for i in indices]
                cluster_vecs = [vectors[i] for i in indices]

                merged_id, merged_vec, merged_payload, detail = self._merge_cluster(
                    cluster_points, cluster_vecs, int(cid), run_id
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
                        "cluster_id": int(cid),
                        "original_count": int(len(cluster_points)),
                        "merged_id": merged_id,
                        "texts": [p.payload.get("text", "")[:80] for p in cluster_points],
                    },
                )

            # Delete originals that were merged away
            if ids_to_delete:
                self.store.delete_points(ids_to_delete)

            # 4. Decay stale memories
            remaining = self.store.get_all_points(with_vectors=False)
            for point in remaining:
                decay_detail = self._apply_decay(point, run_id)
                if decay_detail:
                    result.decay_details.append(decay_detail)
                    if decay_detail.action == "dropped":
                        result.records_decayed += 1

            # 5. PII tagging pass
            remaining = self.store.get_all_points(with_vectors=False)
            for point in remaining:
                tagged = self._tag_pii(point)
                if tagged:
                    result.records_tagged_local += 1

            # Finalise
            result.memories_after = self.store.count()
            result.completed_at = datetime.now(timezone.utc).isoformat()
            result.status = "completed"
            self.history.append(result)

            activity_log.log(
                ActivityType.CONSOLIDATION_COMPLETED,
                "Consolidation complete",
                (
                    f"Before: {result.memories_before} → After: {result.memories_after}. "
                    f"Merged {result.records_merged} records across {result.clusters_found} clusters, "
                    f"decayed {result.records_decayed}, tagged {result.records_tagged_local} local-only."
                ),
                {
                    "run_id": run_id,
                    "before": result.memories_before,
                    "after": result.memories_after,
                    "clusters": result.clusters_found,
                    "merged": result.records_merged,
                    "decayed": result.records_decayed,
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

    # ── Merge logic ──────────────────────────────────────────────────────

    def _merge_cluster(
        self,
        points: list,
        vecs: list[np.ndarray],
        cluster_id: int,
        batch_id: str,
    ) -> tuple[str, list[float], dict, MergeDetail]:
        """Merge a cluster of near-duplicate points into one record."""

        # Pick the "best" as the base (highest importance, then freshest)
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

        # Compute average pairwise similarity for the detail record
        from itertools import combinations
        sims = []
        for i, j in combinations(range(len(vecs)), 2):
            dot = np.dot(vecs[i], vecs[j])
            n = np.linalg.norm(vecs[i]) * np.linalg.norm(vecs[j])
            sims.append(float(dot / n) if n > 0 else 0.0)
        avg_sim = float(np.mean(sims)) if sims else 1.0

        # Build merged payload
        original_ids = [str(p.id) for p in points]
        merged_tags = list(
            {t for p in points for t in p.payload.get("tags", [])}
        )
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
            "text": base.payload.get("text", ""),  # keep best text
        }

        merged_id = str(base.id)

        detail = MergeDetail(
            cluster_id=cluster_id,
            original_ids=original_ids,
            original_texts=[p.payload.get("text", "")[:120] for p in points],
            merged_id=merged_id,
            merged_text=merged_payload["text"][:120],
            similarity=round(avg_sim, 4),
        )

        return merged_id, avg_vec_list, merged_payload, detail

    # ── Decay logic ──────────────────────────────────────────────────────

    def _apply_decay(self, point, batch_id: str) -> DecayDetail | None:
        """Reduce a memory's decay_score based on age; drop if below threshold."""
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

        # Higher importance decays slower
        effective_rate = settings.DECAY_RATE_PER_HOUR * (1.0 - 0.5 * importance)
        new_score = max(0.0, old_score - effective_rate * age_hours)
        new_score = round(new_score, 4)

        if new_score < settings.DECAY_THRESHOLD:
            # Drop it
            self.store.delete_points([str(point.id)])
            activity_log.log(
                ActivityType.MEMORY_DECAYED,
                "Memory dropped (decayed)",
                f"'{payload.get('text', '')[:60]}…' decay {old_score:.2f} → {new_score:.2f} (below {settings.DECAY_THRESHOLD}).",
                {"id": str(point.id), "old": old_score, "new": new_score},
            )
            return DecayDetail(
                memory_id=str(point.id),
                text_preview=payload.get("text", "")[:80],
                old_decay_score=old_score,
                new_decay_score=new_score,
                action="dropped",
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

    # ── PII tagging ──────────────────────────────────────────────────────

    def _tag_pii(self, point) -> bool:
        """If text contains PII patterns, tag as local_only with reason."""
        text = point.payload.get("text", "")
        current = point.payload.get("sync_eligibility", "sync_eligible")

        if current == SyncEligibility.LOCAL_ONLY.value:
            return False  # already tagged

        reasons = []
        for pattern in settings.PII_PATTERNS:
            if re.search(pattern, text):
                reasons.append(f"Matched PII pattern: {pattern}")

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
