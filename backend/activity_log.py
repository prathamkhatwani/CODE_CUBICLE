"""
Thread-safe, in-memory activity log for the Edge Memory Platform.
Every significant event (add, search, consolidate, sync, conflict) is recorded
here so the Inspector UI can surface a full, explainable audit trail.
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

from models import ActivityEntry, ActivityType


_ICON_MAP: dict[str, str] = {
    ActivityType.MEMORY_ADDED: "➕",
    ActivityType.MEMORY_DELETED: "🗑️",
    ActivityType.SEARCH_PERFORMED: "🔍",
    ActivityType.CONSOLIDATION_STARTED: "🧬",
    ActivityType.CONSOLIDATION_COMPLETED: "✅",
    ActivityType.MEMORIES_MERGED: "🔗",
    ActivityType.MEMORY_DECAYED: "📉",
    ActivityType.MEMORY_TAGGED_LOCAL: "🔒",
    ActivityType.ANOMALY_DETECTED: "🚨",
    ActivityType.PRIORITY_SYNC: "⚡",
    ActivityType.SYNC_STARTED: "🔄",
    ActivityType.SYNC_COMPLETED: "☁️",
    ActivityType.SYNC_FAILED: "❌",
    ActivityType.CONFLICT_RESOLVED: "⚖️",
    ActivityType.CONNECTIVITY_CHANGED: "📡",
    ActivityType.SEED_DATA_LOADED: "🌱",
}

MAX_LOG_SIZE = 500


def _sanitize_val(val: Any) -> Any:
    """Recursively convert numpy types and sets to native Python primitives."""
    if hasattr(val, "item"):  # numpy scalar
        return val.item()
    elif isinstance(val, dict):
        return {str(k): _sanitize_val(v) for k, v in val.items()}
    elif isinstance(val, (list, tuple, set)):
        return [_sanitize_val(x) for x in val]
    return val


class ActivityLog:
    """Append-only, bounded activity log."""

    def __init__(self, max_size: int = MAX_LOG_SIZE) -> None:
        self._entries: deque[ActivityEntry] = deque(maxlen=max_size)
        self._lock = threading.Lock()

    # ── Public API ───────────────────────────────────────────────────────

    def log(
        self,
        activity_type: str,
        title: str,
        description: str,
        details: dict[str, Any] | None = None,
    ) -> ActivityEntry:
        clean_details = _sanitize_val(details or {})
        entry = ActivityEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            type=activity_type,
            title=title,
            description=description,
            details=clean_details,
            icon=_ICON_MAP.get(activity_type, "●"),
        )
        with self._lock:
            self._entries.appendleft(entry)
        return entry

    def get_entries(
        self,
        limit: int = 50,
        activity_type: str | None = None,
    ) -> list[ActivityEntry]:
        with self._lock:
            entries = list(self._entries)
        if activity_type:
            entries = [e for e in entries if e.type == activity_type]
        return entries[:limit]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    @property
    def count(self) -> int:
        return len(self._entries)


# Module-level singleton
activity_log = ActivityLog()
