"""
Unit tests for Dense, Sparse (BM25), and Hybrid (RRF) search modes in Qdrant Edge.
"""

import pytest
from qdrant_store import QdrantStore


def test_hybrid_search_ranks_exact_error_codes(store: QdrantStore):
    """Test that Hybrid search with BM25 matches specific alphanumeric part numbers and error codes."""
    # Seed distinct items
    store.add_memory(
        text="Routine thermal alert in general conveyor zone 1",
        source="sensor",
        category="sensor_readings",
        importance=0.5,
        check_for_anomaly=False,
    )
    store.add_memory(
        text="Actuator motor inverter error code ERR-4021 logged on CAN bus",
        source="can-bus",
        category="maintenance",
        importance=0.9,
        check_for_anomaly=False,
    )
    store.add_memory(
        text="Battery pack module SN-A3-7781 voltage imbalance in cell 4",
        source="bms",
        category="sensor_readings",
        importance=0.85,
        check_for_anomaly=False,
    )

    # 1. Search for specific error code "ERR-4021" in Hybrid mode
    results_hybrid = store.search(query="ERR-4021", limit=3, mode="hybrid")
    assert len(results_hybrid) > 0
    assert "ERR-4021" in results_hybrid[0].text

    # 2. Search in Sparse mode
    results_sparse = store.search(query="ERR-4021", limit=3, mode="sparse")
    assert len(results_sparse) > 0
    assert "ERR-4021" in results_sparse[0].text

    # 3. Search for serial code "SN-A3-7781"
    results_sn = store.search(query="SN-A3-7781", limit=3, mode="hybrid")
    assert len(results_sn) > 0
    assert "SN-A3-7781" in results_sn[0].text


def test_search_modes_execution_and_scores(store: QdrantStore):
    """Test that all three search modes (dense, sparse, hybrid) execute successfully."""
    store.add_memory(
        text="Critical pressure drop in hydraulic pump 2",
        source="diagnostics",
        importance=0.8,
        check_for_anomaly=False,
    )

    for mode in ["dense", "sparse", "hybrid"]:
        res = store.search(query="hydraulic pressure", limit=5, mode=mode)
        assert len(res) > 0
        assert res[0].score is not None
