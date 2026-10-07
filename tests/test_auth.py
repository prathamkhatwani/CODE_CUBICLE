"""
Unit tests for API Authentication enforcement when AUTH_ENABLED is toggled.
"""

import pytest
from fastapi.testclient import TestClient
from config import settings
from main import app


def test_auth_disabled_allows_requests(monkeypatch):
    """When AUTH_ENABLED=False, all API requests should succeed without API key header."""
    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    with TestClient(app) as client:
        res = client.get("/api/status")
        assert res.status_code == 200

        res_post = client.post(
            "/api/memories",
            json={"text": "Test memory under open access", "category": "general", "importance": 0.5}
        )
        assert res_post.status_code == 200


def test_auth_enabled_rejects_unauthorized_requests(monkeypatch):
    """When AUTH_ENABLED=True, write/destructive endpoints must return 401 Unauthorized without header."""
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "API_KEY", "test-secret-key-12345")

    with TestClient(app) as client:
        # Protected write endpoint without header -> 401
        res_unauth = client.post(
            "/api/memories",
            json={"text": "Unauthorized attempt", "category": "general", "importance": 0.5}
        )
        assert res_unauth.status_code == 401

        # Protected write endpoint with wrong key -> 401
        res_wrong = client.post(
            "/api/memories",
            json={"text": "Wrong key attempt", "category": "general", "importance": 0.5},
            headers={"X-API-Key": "invalid-key"}
        )
        assert res_wrong.status_code == 401

        # Protected write endpoint with valid key -> 200
        res_auth = client.post(
            "/api/memories",
            json={"text": "Authorized write memory", "category": "general", "importance": 0.5},
            headers={"X-API-Key": "test-secret-key-12345"}
        )
        assert res_auth.status_code == 200

    # Reset
    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
