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
    DECAY_THRESHOLD: float = 0.15           # below this → soft-delete to tombstone
    MIN_CLUSTER_SIZE: int = 2               # minimum records to form a cluster
    CLUSTERING_METHOD: str = "greedy_hnsw"  # "greedy_hnsw" or "capped_diameter"
    MAX_CLUSTER_DIAMETER: float = 0.25      # max cosine distance between any 2 items in a cluster
    NUMERIC_TOLERANCE_PCT: float = 0.15     # 15% tolerance for merging numeric sensor readings

    # ── Soft-Delete & Tombstones ──
    TOMBSTONE_UNDO_WINDOW_SECONDS: int = 300  # 5 minutes undo window before permanent purge

    # ── Anomaly Detection (Priority Sync) ──
    ANOMALY_THRESHOLD: float = 0.70         # max similarity to nearest neighbor to trigger anomaly fast-path (bge-small tuned)
    ANOMALY_K: int = 5                      # number of neighbors to query on insert

    # ── Sync & Conflict Resolution ──
    SYNC_BATCH_SIZE: int = 50
    RETRY_MAX: int = 5
    RETRY_BACKOFF_BASE: float = 2.0
    CONFLICT_STRATEGY: str = "version_vector" # "version_vector" or "server_timestamp"

    # ── Device Identity & Auth ──
    DEVICE_ID: str = "edge-001"
    DEVICE_NAME: str = "Edge Device Alpha"
    DEMO_MODE: bool = True                  # When False, /api/reset is disabled or requires auth
    AUTH_ENABLED: bool = False              # Set to True to enforce API_KEY
    API_KEY: str = "cortex-edge-secret-key-2026"

    # ── PII patterns (simple regex-based detection) ──
    PII_PATTERNS: list[str] = field(default_factory=lambda: [
        r'\b\d{3}-\d{2}-\d{4}\b',                    # SSN
        r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.\w+\b', # email
        r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b',             # phone
        r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b', # credit card
        r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b',   # IP address
    ])

    PORT: int = 8000
    DATA_DIR: str | None = None

    def __post_init__(self):
        """Override from environment variables with or without EDGE_ prefix."""
        # Mapping of aliases
        aliases = {
            "DATA_DIR": "EDGE_QDRANT_PATH",
            "EDGE_DATA_DIR": "EDGE_QDRANT_PATH",
            "CLOUD_URL": "CLOUD_QDRANT_URL",
            "EDGE_CLOUD_URL": "CLOUD_QDRANT_URL",
            "CLOUD_API_KEY": "CLOUD_QDRANT_API_KEY",
            "EDGE_CLOUD_API_KEY": "CLOUD_QDRANT_API_KEY",
        }

        for attr_name in list(vars(self)):
            # Check direct name, EDGE_ prefix, and aliases
            candidates = [
                f"EDGE_{attr_name}",
                attr_name,
            ]
            for alias_key, target in aliases.items():
                if target == attr_name or alias_key == attr_name:
                    candidates.extend([alias_key, f"EDGE_{alias_key}"])

            env_val = None
            for c in candidates:
                val = os.environ.get(c)
                if val is not None:
                    env_val = val
                    break

            if env_val is not None:
                current = getattr(self, attr_name)
                if isinstance(current, bool):
                    setattr(self, attr_name, env_val.lower() in ("true", "1", "yes"))
                elif isinstance(current, float):
                    setattr(self, attr_name, float(env_val))
                elif isinstance(current, int):
                    setattr(self, attr_name, int(env_val))
                else:
                    setattr(self, attr_name, env_val)

        if self.DATA_DIR and not os.environ.get("EDGE_QDRANT_PATH"):
            self.EDGE_QDRANT_PATH = self.DATA_DIR


settings = Settings()
