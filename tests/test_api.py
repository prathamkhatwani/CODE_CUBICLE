"""
End-to-end integration tests for FastAPI REST API using TestClient.
"""

import pytest
from config import settings


def test_api_status(client):
    """Test device status endpoint."""
    res = client.get("/api/status")
    assert res.status_code == 200
    data = res.json()
    assert "device_id" in data
    assert "is_online" in data
    assert "total_memories" in data


def test_api_seed_and_search(client):
    """Test seeding demo data and performing semantic vector search."""
    # Reset
    client.post("/api/reset")

    # Seed
    seed_res = client.post("/api/seed")
    assert seed_res.status_code == 200
    total_seeded = seed_res.json()["total_records"]
    assert total_seeded >= 30

    # Search with hybrid mode
    search_res = client.post("/api/search", json={"query": "ERR-4021 motor fault", "limit": 5, "mode": "hybrid"})
    assert search_res.status_code == 200
    search_data = search_res.json()
    assert "latency_ms" in search_data
    assert search_data["mode"] == "hybrid"
    results = search_data["results"]
    assert len(results) > 0
    assert "ERR-4021" in results[0]["text"] or "motor" in results[0]["text"].lower()


def test_api_consolidation_flow(client):
    """Test triggering consolidation and inspecting results."""
    # Reset and seed
    client.post("/api/reset")
    seed_res = client.post("/api/seed")
    total_seeded = seed_res.json()["total_records"]

    # Before consolidation
    status_before = client.get("/api/status").json()
    assert status_before["total_memories"] == total_seeded

    # Consolidate
    cons_res = client.post("/api/consolidate")
    assert cons_res.status_code == 200
    data = cons_res.json()
    assert data["status"] == "completed"
    assert data["clusters_found"] > 0
    assert data["records_merged"] > 0

    # After consolidation
    status_after = client.get("/api/status").json()
    assert status_after["total_memories"] < total_seeded


def test_api_anomaly_injection(client):
    """Test demo anomaly injection fast-path endpoint."""
    res = client.post("/api/demo/inject-anomaly")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "anomaly_injected"
    assert data["memory"]["priority"] == "high"


def test_api_reset_protection_when_production(client, monkeypatch):
    """Test that /api/reset is protected when DEMO_MODE is False unless valid API key is supplied."""
    from config import settings
    monkeypatch.setattr(settings, "DEMO_MODE", False)

    # Without API key -> 403 Forbidden
    res_unauth = client.post("/api/reset")
    assert res_unauth.status_code == 403

    # With valid API key -> 200 OK
    res_auth = client.post("/api/reset", headers={"X-API-Key": settings.API_KEY})
    assert res_auth.status_code == 200
