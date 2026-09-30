"""
Data models for Edge Memory & Intelligence Platform.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────────────────

class SyncEligibility(str, Enum):
    LOCAL_ONLY = "local_only"
    SYNC_ELIGIBLE = "sync_eligible"


class ActivityType(str, Enum):
    MEMORY_ADDED = "memory_added"
    MEMORY_DELETED = "memory_deleted"
    MEMORY_RESTORED = "memory_restored"
    SEARCH_PERFORMED = "search_performed"
    CONSOLIDATION_STARTED = "consolidation_started"
    CONSOLIDATION_COMPLETED = "consolidation_completed"
    MEMORIES_MERGED = "memories_merged"
    MEMORY_DECAYED = "memory_decayed"
    MEMORY_TOMBSTONED = "memory_tombstoned"
    MEMORY_TAGGED_LOCAL = "memory_tagged_local"
    ANOMALY_DETECTED = "anomaly_detected"
    PRIORITY_SYNC = "priority_sync"
    SYNC_STARTED = "sync_started"
    SYNC_COMPLETED = "sync_completed"
    SYNC_FAILED = "sync_failed"
    CONFLICT_RESOLVED = "conflict_resolved"
    CONNECTIVITY_CHANGED = "connectivity_changed"
    SEED_DATA_LOADED = "seed_data_loaded"


# ── Request/Response Models ──────────────────────────────────────────────────

class MemoryCreate(BaseModel):
    text: str
    source: str = "manual"
    importance: float = Field(default=0.7, ge=0.0, le=1.0)
    tags: list[str] = Field(default_factory=list)
    category: str = "general"
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryResponse(BaseModel):
    id: str
    text: str
    source: str
    importance: float
    sync_eligibility: str
    sync_reason: str | None = None
    created_at: str
    updated_at: str
    is_consolidated: bool
    decay_score: float
    device_id: str
    tags: list[str]
    category: str
    synced: bool
    merge_count: int
    pii_detected: bool
    priority: str = "normal"  # "normal" | "high"
    priority_reason: str | None = None  # e.g. "anomaly"
    nearest_similarity: float | None = None
    is_tombstone: bool = False
    tombstone_at: str | None = None
    undo_until: str | None = None
    version_vector: dict[str, int] = Field(default_factory=dict)
    lamport_clock: int = 1
    numeric_summary: dict[str, Any] | None = None
    score: float | None = None  # search relevance score


class SearchRequest(BaseModel):
    query: str
    limit: int = Field(default=10, ge=1, le=100)
    category: str | None = None
    min_importance: float | None = None
    include_local_only: bool = True
    include_tombstones: bool = False


class SearchResponse(BaseModel):
    query: str
    results: list[MemoryResponse]
    total_found: int
    search_time_ms: float


class ConsolidationResult(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    started_at: str
    completed_at: str | None = None
    memories_before: int = 0
    memories_after: int = 0
    clusters_found: int = 0
    records_merged: int = 0
    records_decayed: int = 0
    records_tombstoned: int = 0
    records_tagged_local: int = 0
    merge_details: list[MergeDetail] = Field(default_factory=list)
    decay_details: list[DecayDetail] = Field(default_factory=list)
    status: str = "running"


class MergeDetail(BaseModel):
    cluster_id: int
    original_ids: list[str]
    original_texts: list[str]
    merged_id: str
    merged_text: str
    similarity: float
    numeric_summary: dict[str, Any] | None = None
    mixed_pii_protected: bool = False


class DecayDetail(BaseModel):
    memory_id: str
    text_preview: str
    old_decay_score: float
    new_decay_score: float
    action: str  # "decayed", "tombstoned", or "purged"
    undo_until: str | None = None


# Forward ref resolution
ConsolidationResult.model_rebuild()


class SyncResult(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    started_at: str
    completed_at: str | None = None
    records_pushed: int = 0
    records_pulled: int = 0
    conflicts_resolved: int = 0
    conflict_details: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    status: str = "running"


class ActivityEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    type: str
    title: str
    description: str
    details: dict[str, Any] = Field(default_factory=dict)
    icon: str = "●"


class DeviceStatus(BaseModel):
    device_id: str
    device_name: str
    is_online: bool
    total_memories: int
    synced_memories: int
    pending_sync: int
    local_only_memories: int
    tombstone_count: int = 0
    avg_decay_score: float
    last_consolidation: str | None = None
    last_sync: str | None = None
    uptime_seconds: float
    embedding_model: str
    vector_dimensions: int
    demo_mode: bool = True
    auth_enabled: bool = False


class DashboardStats(BaseModel):
    total_memories: int
    synced_count: int
    pending_count: int
    local_only_count: int
    tombstone_count: int = 0
    avg_decay_score: float
    avg_importance: float
    categories: dict[str, int]
    sources: dict[str, int]
    memory_timeline: list[dict[str, Any]]
    consolidation_history: list[ConsolidationResult]
    sync_history: list[SyncResult]


def build_payload(
    text: str,
    source: str = "manual",
    importance: float = 0.7,
    tags: list[str] | None = None,
    category: str = "general",
    device_id: str = "edge-001",
    metadata: dict[str, Any] | None = None,
    sync_eligibility: str = SyncEligibility.SYNC_ELIGIBLE.value,
    sync_reason: str | None = None,
    is_consolidated: bool = False,
    decay_score: float = 1.0,
    synced: bool = False,
    merge_count: int = 1,
    original_ids: list[str] | None = None,
    pii_detected: bool = False,
    consolidation_batch: str | None = None,
    priority: str = "normal",
    priority_reason: str | None = None,
    nearest_similarity: float | None = None,
    is_tombstone: bool = False,
    tombstone_at: str | None = None,
    undo_until: str | None = None,
    version_vector: dict[str, int] | None = None,
    lamport_clock: int = 1,
    numeric_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a Qdrant-compatible payload dict for a memory record."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "text": text,
        "source": source,
        "importance": importance,
        "sync_eligibility": sync_eligibility,
        "sync_reason": sync_reason,
        "created_at": now,
        "updated_at": now,
        "is_consolidated": is_consolidated,
        "decay_score": decay_score,
        "device_id": device_id,
        "tags": tags or [],
        "category": category,
        "synced": synced,
        "merge_count": merge_count,
        "original_ids": original_ids or [],
        "pii_detected": pii_detected,
        "consolidation_batch": consolidation_batch,
        "priority": priority,
        "priority_reason": priority_reason,
        "nearest_similarity": nearest_similarity,
        "is_tombstone": is_tombstone,
        "tombstone_at": tombstone_at,
        "undo_until": undo_until,
        "version_vector": version_vector or {device_id: 1},
        "lamport_clock": lamport_clock,
        "numeric_summary": numeric_summary,
        "metadata": metadata or {},
    }
