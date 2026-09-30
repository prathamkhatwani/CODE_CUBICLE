"""
Unit tests for PII regex detection and Mixed-PII cluster safeguarding.
"""

from types import SimpleNamespace
import pytest
from config import settings
from consolidation import ConsolidationEngine


def test_pii_regex_patterns(store, engine):
    """Test standard PII patterns: email, phone, SSN, IP address, credit card."""
    sample_texts = [
        ("Contact admin at support@cortex.ai for assistance.", True, "email"),
        ("Emergency hotline is 555-123-4567 available 24/7.", True, "phone"),
        ("Employee SSN recorded as 123-45-6789 in personnel file.", True, "SSN"),
        ("Server IP address is 192.168.1.104 on subnet.", True, "IP"),
        ("Routine conveyor belt reading: 42 PSI normal.", False, "Clean sensor reading"),
    ]

    for text, should_match, label in sample_texts:
        matches = engine._detect_pii_in_text(text)
        if should_match:
            assert len(matches) > 0, f"Failed to detect PII for {label}: '{text}'"
        else:
            assert len(matches) == 0, f"False positive PII match for {label}: '{text}'"


def test_mixed_pii_cluster_safeguard(store, engine):
    """
    CRITICAL EDGE CASE TEST:
    If 3 records are clustered together and only 1 contains personal data (e.g. email),
    the synthesized output MUST inherit local_only sync eligibility so private info
    never synchronizes to cloud storage.
    """
    import numpy as np
    dim = 384
    vecs = [np.ones(dim, dtype=np.float32) / np.sqrt(dim) for _ in range(3)]

    p_clean1 = SimpleNamespace(
        id="clean-1",
        payload={
            "text": "Conveyor motor #4 maintenance completed in zone B.",
            "sync_eligibility": "sync_eligible",
            "pii_detected": False,
            "importance": 0.7,
        }
    )
    p_clean2 = SimpleNamespace(
        id="clean-2",
        payload={
            "text": "Conveyor motor #4 inspection passed all diagnostic checks.",
            "sync_eligibility": "sync_eligible",
            "pii_detected": False,
            "importance": 0.75,
        }
    )
    p_pii = SimpleNamespace(
        id="pii-3",
        payload={
            "text": "Maintenance technician contact: John Doe, email john.doe@warehouse.com.",
            "sync_eligibility": "local_only",
            "pii_detected": True,
            "importance": 0.9,
        }
    )

    cluster_points = [p_clean1, p_clean2, p_pii]
    merged_id, merged_vec, merged_payload, detail = engine._merge_cluster(
        cluster_points, vecs, cluster_id=1, batch_id="batch-pii-test"
    )

    # Verify that the entire synthesized insight is locked to local_only
    assert merged_payload["sync_eligibility"] == "local_only", "Security leak: mixed cluster failed to inherit local_only!"
    assert merged_payload["pii_detected"] is True
    assert detail.mixed_pii_protected is True
    assert "Mixed-cluster PII safeguard" in merged_payload["sync_reason"]
