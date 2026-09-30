"""
Unit tests for numeric extraction, tolerance-aware merging, and metadata synthesis.
"""

from types import SimpleNamespace
import pytest
from consolidation import ConsolidationEngine


def test_numeric_extraction_units(store, engine):
    """Test extracting numbers with different unit types."""
    text = "Sensor A3 reported temp 87.3°F and pneumatic pressure 42 PSI with 78% humidity."
    readings = engine._extract_numeric_values(text)
    
    assert (87.3, "°F") in readings
    assert (42.0, "PSI") in readings
    assert (78.0, "%") in readings


def test_numeric_merge_within_tolerance(store, engine):
    """Test that numbers within 15% tolerance compute min, max, avg, latest."""
    p1 = SimpleNamespace(id="1", payload={"text": "Sensor A3 temperature 87.1°F detected in zone A"})
    p2 = SimpleNamespace(id="2", payload={"text": "Sensor A3 temp reads 87.5°F in zone A"})
    p3 = SimpleNamespace(id="3", payload={"text": "Temperature sensor A3 reading 87.3°F"})

    can_merge, summary, reason = engine._check_numeric_compatibility([p1, p2, p3])
    
    assert can_merge is True
    assert summary is not None
    assert "°F" in summary
    stats = summary["°F"]
    assert stats["min"] == 87.1
    assert stats["max"] == 87.5
    assert round(stats["avg"], 2) == 87.3
    assert stats["latest"] == 87.3
    assert stats["count"] == 3


def test_numeric_merge_skips_when_exceeding_tolerance(store, engine):
    """Test that contradictory sensor readings (> 15% variance) are NOT merged."""
    p1 = SimpleNamespace(id="1", payload={"text": "Pressure line A reading 40 PSI nominal"})
    p2 = SimpleNamespace(id="2", payload={"text": "Pressure line A reading 120 PSI dangerous spike"})

    can_merge, summary, reason = engine._check_numeric_compatibility([p1, p2])
    
    assert can_merge is False
    assert "variance >" in reason
    assert summary is None


def test_merge_metadata_and_tags(store, engine):
    """Test that tag union, importance maximization, and version vector reconciliation work during merge."""
    import numpy as np
    dim = 384
    v1 = np.ones(dim, dtype=np.float32) / np.sqrt(dim)
    v2 = np.ones(dim, dtype=np.float32) / np.sqrt(dim)

    p1 = SimpleNamespace(
        id="mem-1",
        payload={
            "text": "Warehouse zone A temp 87.1°F",
            "importance": 0.6,
            "tags": ["sensor", "zone-a"],
            "version_vector": {"edge-001": 2},
            "lamport_clock": 2,
            "created_at": "2026-09-30T10:00:00Z",
        }
    )
    p2 = SimpleNamespace(
        id="mem-2",
        payload={
            "text": "Warehouse zone A temp alert 87.4°F",
            "importance": 0.85,
            "tags": ["sensor", "alert", "temperature"],
            "version_vector": {"edge-001": 3, "edge-002": 1},
            "lamport_clock": 3,
            "created_at": "2026-09-30T10:05:00Z",
        }
    )

    merged_id, merged_vec, merged_payload, detail = engine._merge_cluster(
        [p1, p2], [v1, v2], cluster_id=0, batch_id="batch-test"
    )

    assert merged_payload["importance"] == 0.85
    assert set(merged_payload["tags"]) == {"sensor", "zone-a", "alert", "temperature"}
    assert merged_payload["is_consolidated"] is True
    assert merged_payload["merge_count"] == 2
    assert "mem-1" in merged_payload["original_ids"]
    assert "mem-2" in merged_payload["original_ids"]
    assert merged_payload["version_vector"]["edge-001"] == 4  # incremented clock
