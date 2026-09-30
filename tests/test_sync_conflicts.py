"""
Unit tests for Version Vector comparison, Lamport Logical Clock resolution,
and Anomaly Priority Fast-Path sync.
"""

from types import SimpleNamespace
import pytest
from config import settings
from sync_agent import SyncAgent


def test_version_vector_comparisons():
    """Test version vector partial ordering (dominance, identical, concurrent)."""
    # Case 1: Identical
    vv1 = {"edge-001": 2, "server": 1}
    vv2 = {"edge-001": 2, "server": 1}
    assert SyncAgent._compare_version_vectors(vv1, vv2) == "IDENTICAL"

    # Case 2: A strictly dominates B
    vv_a = {"edge-001": 3, "server": 1}
    vv_b = {"edge-001": 2, "server": 1}
    assert SyncAgent._compare_version_vectors(vv_a, vv_b) == "A_DOMINATES"

    # Case 3: B strictly dominates A
    vv_a = {"edge-001": 2, "server": 1}
    vv_b = {"edge-001": 2, "server": 3}
    assert SyncAgent._compare_version_vectors(vv_a, vv_b) == "B_DOMINATES"

    # Case 4: Concurrent split-brain edits
    vv_local = {"edge-001": 3, "server": 1}
    vv_remote = {"edge-001": 2, "server": 2}
    assert SyncAgent._compare_version_vectors(vv_local, vv_remote) == "CONCURRENT"


def test_concurrent_conflict_resolution():
    """Test that concurrent edits merge metadata and increment version vectors."""
    p_local = SimpleNamespace(
        id="mem-sync-1",
        payload={
            "text": "Zone A valve inspection updated locally",
            "importance": 0.8,
            "tags": ["zone-a", "valve"],
            "version_vector": {"edge-001": 2, "server": 1},
            "lamport_clock": 2,
        }
    )
    p_cloud = SimpleNamespace(
        id="mem-sync-1",
        payload={
            "text": "Zone A valve maintenance performed remotely",
            "importance": 0.7,
            "tags": ["zone-a", "maintenance", "remote"],
            "version_vector": {"edge-001": 1, "server": 2},
            "lamport_clock": 2,
        }
    )

    merged, resolution_type = SyncAgent._resolve_version_vector_conflict(p_local, p_cloud)
    assert resolution_type == "concurrent_merged_version_vectors"
    assert merged is not None
    assert set(merged["tags"]) == {"zone-a", "valve", "maintenance", "remote"}
    assert merged["version_vector"]["edge-001"] == 3
    assert merged["version_vector"]["server"] == 2
    assert merged["importance"] == 0.8


def test_priority_fast_path_anomaly_push(store, sync_agent_fixture):
    """Test that a high-priority anomaly is pushed immediately via fast-path."""
    pid, is_anomaly, sim = store.add_memory(
        text="CRITICAL NOVEL HAZARD: Unknown toxic gas detected in sector 9.",
        source="sensor",
        importance=0.99,
        check_for_anomaly=False, # manually simulate priority tag
    )
    store.update_payload(pid, {"priority": "high", "priority_reason": "anomaly"})

    # Ensure online
    sync_agent_fixture.is_online = True
    sync_agent_fixture._simulated_offline = False

    pushed = sync_agent_fixture.sync_priority_fast_path(pid)
    assert pushed is True

    # Cloud store should now contain this point
    cloud_pt = sync_agent_fixture.cloud.get_point(pid)
    assert cloud_pt is not None
    assert cloud_pt.payload["sync_path"] == "priority_fast_path"
