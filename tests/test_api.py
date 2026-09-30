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
    assert seed_res.json()["total_records"] == 32

    # Search
    search_res = client.post("/api/search", json={"query": "temperature alert in zone A", "limit": 5})
    assert search_res.status_code == 200
    results = search_res.json()["results"]
    assert len(results) > 0
    assert "temp" in results[0]["text"].lower() or "sensor" in results[0]["text"].lower()


def test_api_consolidation_flow(client):
    """Test triggering consolidation and inspecting results."""
    # Reset and seed
    client.post("/api/reset")
    client.post("/api/seed")

    # Before consolidation
    status_before = client.get("/api/status").json()
    assert status_before["total_memories"] == 32

    # Consolidate
    cons_res = client.post("/api/consolidate")
    assert cons_res.status_code == 200
    data = cons_res.json()
    assert data["status"] == "completed"
    assert data["clusters_found"] > 0
    assert data["records_merged"] > 0

    # After consolidation
    status_after = client.get("/api/status").json()
    assert status_after["total_memories"] < 32


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
