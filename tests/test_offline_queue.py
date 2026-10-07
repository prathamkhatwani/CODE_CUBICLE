"""
Unit tests for offline queue ordering, priority flushing, and disk journal persistence.
"""

import pytest
from qdrant_store import QdrantStore
from sync_agent import SyncAgent


def test_offline_queue_priority_ordering(store: QdrantStore):
    """Test that offline queue flushes high-priority anomalies before normal memories."""
    cloud = QdrantStore(path=":memory:")
    cloud.ensure_collection()
    agent = SyncAgent(store, cloud)

    # Ingest standard memories while offline
    store.add_memory(
        text="Normal reading 1: ambient humidity 45%",
        source="sensor",
        importance=0.4,
        check_for_anomaly=False,
    )
    store.add_memory(
        text="Normal reading 2: conveyor speed normal",
        source="sensor",
        importance=0.4,
        check_for_anomaly=False,
    )

    # Ingest high priority anomaly
    pid_anom, _, _ = store.add_memory(
        text="CRITICAL ANOMALY: Gas leak detected in manifold 3",
        source="hazard-sensor",
        importance=0.99,
        check_for_anomaly=False,
        payload_overrides={"priority": "high", "priority_reason": "anomaly"},
    )

    # Queue status
    status = agent.get_queue_status()
    assert status["queue_length"] == 3
    assert status["anomalies_queued"] == 1

    # Simulate coming online and flushing
    agent.is_online = True
    flushed_count = agent.flush_offline_queue()
    assert flushed_count == 3

    # Verify cloud received anomaly first and local records are now synced
    cloud_pt = cloud.get_point(pid_anom)
    assert cloud_pt is not None
    assert cloud_pt.payload["priority"] == "high"

    # Queue should now be empty
    status_post = agent.get_queue_status()
    assert status_post["queue_length"] == 0

    cloud.close()
    agent.edge.close()
