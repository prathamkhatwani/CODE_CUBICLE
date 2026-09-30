"""
Unit tests for clustering algorithms in ConsolidationEngine:
- Graph-based Greedy HNSW Neighborhood Clustering
- Capped Cluster Diameter (preventing chain drift)
- Scaling over synthetic clusters
"""

import numpy as np
import pytest
from config import settings
from consolidation import ConsolidationEngine


def test_greedy_capped_clustering_basic(store, engine):
    """Test clustering identical and near-identical vectors."""
    # Create 3 vectors near [1, 0, 0, ...] and 2 vectors near [0, 1, 0, ...]
    dim = settings.VECTOR_SIZE
    
    v1 = np.zeros(dim, dtype=np.float32)
    v1[0] = 1.0
    
    v2 = np.zeros(dim, dtype=np.float32)
    v2[0] = 0.95
    v2[1] = 0.05
    
    v3 = np.zeros(dim, dtype=np.float32)
    v3[0] = 0.92
    v3[2] = 0.08
    
    v4 = np.zeros(dim, dtype=np.float32)
    v4[10] = 1.0
    
    v5 = np.zeros(dim, dtype=np.float32)
    v5[10] = 0.96
    v5[11] = 0.04
    
    vectors = np.array([v1, v2, v3, v4, v5])
    clusters = engine._greedy_capped_clustering(vectors)
    
    assert len(clusters) == 2
    # First cluster contains indices [0, 1, 2]
    assert set(clusters[0]) == {0, 1, 2}
    # Second cluster contains indices [3, 4]
    assert set(clusters[1]) == {3, 4}


def test_cluster_diameter_capping_prevents_chain_drift(store, engine):
    """
    Test that transitive chaining (A ~ B and B ~ C, but A !~ C)
    is bounded by MAX_CLUSTER_DIAMETER and does not form one stretched cluster.
    """
    dim = settings.VECTOR_SIZE
    
    # Vector A: [1.0, 0, ...]
    vA = np.zeros(dim, dtype=np.float32)
    vA[0] = 1.0
    
    # Vector B: [0.86, 0.50, ...] (sim to A ~ 0.86)
    vB = np.zeros(dim, dtype=np.float32)
    vB[0] = 0.86
    vB[1] = 0.50
    vB /= np.linalg.norm(vB)
    
    # Vector C: [0.55, 0.83, ...] (sim to B ~ 0.88, but sim to A ~ 0.55 < threshold)
    vC = np.zeros(dim, dtype=np.float32)
    vC[0] = 0.55
    vC[1] = 0.83
    vC /= np.linalg.norm(vC)
    
    vectors = np.array([vA, vB, vC])
    clusters = engine._greedy_capped_clustering(vectors)
    
    # C should NOT be included in cluster with A because dist(A, C) exceeds MAX_CLUSTER_DIAMETER
    for c in clusters:
        assert not (0 in c and 2 in c), "Chain drift occurred: A and C were incorrectly clustered together!"


def test_clustering_with_noise_points(store, engine):
    """Test that isolated outlier vectors (noise) are left as singletons and not clustered."""
    dim = settings.VECTOR_SIZE
    
    v1 = np.zeros(dim, dtype=np.float32); v1[0] = 1.0
    v2 = np.zeros(dim, dtype=np.float32); v2[0] = 0.98; v2[1] = 0.02
    
    # Isolated outliers
    v_outlier1 = np.zeros(dim, dtype=np.float32); v_outlier1[50] = 1.0
    v_outlier2 = np.zeros(dim, dtype=np.float32); v_outlier2[100] = 1.0
    
    vectors = np.array([v1, v2, v_outlier1, v_outlier2])
    clusters = engine._greedy_capped_clustering(vectors)
    
    assert len(clusters) == 1
    assert set(clusters[0]) == {0, 1}
