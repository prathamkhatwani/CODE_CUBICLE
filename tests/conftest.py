"""
Pytest configuration and shared fixtures for CORTEX.
"""

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Add backend directory to sys.path
backend_dir = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(backend_dir))

# Configure test environment
os.environ["EDGE_DEMO_MODE"] = "True"
os.environ["EDGE_AUTH_ENABLED"] = "False"

from config import settings
from consolidation import ConsolidationEngine
from main import app
from qdrant_store import QdrantStore
from sync_agent import SyncAgent


@pytest.fixture
def store():
    """Create a fresh isolated in-memory QdrantStore instance for unit tests."""
    s = QdrantStore(path=":memory:")
    s.ensure_collection()
    yield s
    s.close()


@pytest.fixture
def engine(store):
    """Create ConsolidationEngine on fresh in-memory store."""
    return ConsolidationEngine(store)


@pytest.fixture
def sync_agent_fixture(store):
    """Create SyncAgent with local in-memory edge store and isolated in-memory cloud store."""
    cloud = QdrantStore(path=":memory:")
    cloud.ensure_collection()
    agent = SyncAgent(store, cloud)
    yield agent
    cloud.close()


@pytest.fixture(scope="module")
def client():
    """FastAPI TestClient instance."""
    with TestClient(app) as c:
        yield c
