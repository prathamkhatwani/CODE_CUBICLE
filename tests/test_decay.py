"""
Unit tests for memory decay, soft-delete tombstones, and undo restoration.
"""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from config import settings
from consolidation import ConsolidationEngine


def test_decay_calculation_and_importance_weighting(store, engine):
    """Test that higher importance memories decay slower than low-importance ephemeral notes."""
    now = datetime.now(timezone.utc)
    old_time = (now - timedelta(hours=24)).isoformat()

    id_low = str(uuid.uuid4())
    id_high = str(uuid.uuid4())

    # Low importance memory (importance = 0.1)
    p_low = SimpleNamespace(
        id=id_low,
        payload={"text": "Routine noise", "importance": 0.1, "decay_score": 1.0, "created_at": old_time}
    )
    store.client.upsert(
        collection_name=settings.COLLECTION_NAME,
        points=[SimpleNamespace(id=id_low, vector=[0.1] * 384, payload=p_low.payload)]
    )

    # High importance memory (importance = 0.95)
    p_high = SimpleNamespace(
        id=id_high,
        payload={"text": "Critical safety hazard", "importance": 0.95, "decay_score": 1.0, "created_at": old_time}
    )
    store.client.upsert(
        collection_name=settings.COLLECTION_NAME,
        points=[SimpleNamespace(id=id_high, vector=[0.1] * 384, payload=p_high.payload)]
    )

    detail_low = engine._apply_decay(p_low, "test-batch")
    detail_high = engine._apply_decay(p_high, "test-batch")

    assert detail_low.new_decay_score < detail_high.new_decay_score


def test_soft_delete_tombstone_when_below_threshold(store, engine):
    """Test that records decaying below DECAY_THRESHOLD transition to tombstone with undo window."""
    now = datetime.now(timezone.utc)
    ancient_time = (now - timedelta(hours=200)).isoformat()
    id_stale = str(uuid.uuid4())

    p_stale = SimpleNamespace(
        id=id_stale,
        payload={"text": "Ephemeral hallway sighting", "importance": 0.1, "decay_score": 0.20, "created_at": ancient_time}
    )
    store.client.upsert(
        collection_name=settings.COLLECTION_NAME,
        points=[SimpleNamespace(id=id_stale, vector=[0.1] * 384, payload=p_stale.payload)]
    )

    detail = engine._apply_decay(p_stale, "test-batch")
    assert detail.action == "tombstoned"
    assert detail.undo_until is not None

    # Verify payload in Qdrant store has is_tombstone=True
    stored = store.get_point(id_stale)
    assert stored.payload["is_tombstone"] is True
    assert stored.payload["undo_until"] is not None


def test_restore_tombstone_within_undo_window(store, engine):
    """Test that a soft-deleted tombstone can be successfully restored."""
    now = datetime.now(timezone.utc)
    undo_until = (now + timedelta(seconds=300)).isoformat()
    id_restore = str(uuid.uuid4())

    store.client.upsert(
        collection_name=settings.COLLECTION_NAME,
        points=[SimpleNamespace(
            id=id_restore,
            vector=[0.1] * 384,
            payload={
                "text": "Accidentally decayed note",
                "is_tombstone": True,
                "tombstone_at": now.isoformat(),
                "undo_until": undo_until,
                "decay_score": 0.05,
            }
        )]
    )

    assert store.count_active() == 0

    # Restore memory
    restored = engine.restore_tombstone(id_restore)
    assert restored is True

    # Active count should now be 1 and is_tombstone False
    assert store.count_active() == 1
    pt = store.get_point(id_restore)
    assert pt.payload["is_tombstone"] is False
    assert pt.payload["decay_score"] == 1.0
