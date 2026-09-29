"""
Configuration for Edge Memory & Intelligence Platform.
All settings can be overridden via environment variables with EDGE_ prefix.
"""

import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    # ── Edge Qdrant (local, offline-first) ──
    EDGE_QDRANT_PATH: str = "./qdrant_edge_data"
    COLLECTION_NAME: str = "edge_memories"
    VECTOR_SIZE: int = 384

    # ── Cloud Qdrant (sync target) ──
    CLOUD_QDRANT_URL: str = "http://localhost:6334"
    CLOUD_QDRANT_API_KEY: str | None = None

    # ── Embedding ──
    EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"

    # ── Consolidation ("sleep cycle") ──
    SIMILARITY_THRESHOLD: float = 0.85      # cosine sim to count as near-duplicate
    DECAY_RATE_PER_HOUR: float = 0.02       # how fast memories age
    DECAY_THRESHOLD: float = 0.15           # below this → drop the memory
    MIN_CLUSTER_SIZE: int = 2               # minimum records to form a cluster

    # ── Anomaly Detection (Priority Sync) ──
    ANOMALY_THRESHOLD: float = 0.70         # max similarity to nearest neighbor to trigger anomaly fast-path (tuned for bge-small)
    ANOMALY_K: int = 5                      # number of neighbors to query on insert

    # ── Sync ──
    SYNC_BATCH_SIZE: int = 50
    RETRY_MAX: int = 5
    RETRY_BACKOFF_BASE: float = 2.0

    # ── Device identity ──
    DEVICE_ID: str = "edge-001"
    DEVICE_NAME: str = "Edge Device Alpha"

    # ── PII patterns (simple regex-based detection) ──
    PII_PATTERNS: list[str] = field(default_factory=lambda: [
        r'\b\d{3}-\d{2}-\d{4}\b',                    # SSN
        r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.\w+\b', # email
        r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b',             # phone
        r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b', # credit card
        r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b',   # IP address
    ])

    def __post_init__(self):
        """Override from environment variables."""
        for attr_name in vars(self):
            env_key = f"EDGE_{attr_name}"
            env_val = os.environ.get(env_key)
            if env_val is not None:
                current = getattr(self, attr_name)
                if isinstance(current, float):
                    setattr(self, attr_name, float(env_val))
                elif isinstance(current, int):
                    setattr(self, attr_name, int(env_val))
                else:
                    setattr(self, attr_name, env_val)


settings = Settings()
