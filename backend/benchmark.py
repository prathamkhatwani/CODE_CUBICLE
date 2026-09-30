"""
Empirical Benchmark Suite for CORTEX Edge Memory Platform.

Evaluates:
1. Deduplication Ratio (% storage saved by consolidation)
2. Search Quality (MRR and Precision@3 before vs after consolidation)
3. Sync Payload Bandwidth Reduction (bytes pushed to cloud)
4. Vector Search Latency (p50 / p99 query latency)
"""

import json
import time
from typing import Any

from seed_data import generate_seed_data


def run_benchmark_suite(store, engine) -> dict[str, Any]:
    """Run full benchmark on a fresh seeded dataset and return metrics."""
    # 1. Reset and Seed
    store.reset_collection()
    engine.history.clear()
    seed_res = generate_seed_data(store)
    raw_count = store.count_active()

    # Calculate raw sync payload size (all sync-eligible items before consolidation)
    raw_points = store.get_all_points(with_vectors=True)
    raw_eligible = [p for p in raw_points if p.payload.get("sync_eligibility") == "sync_eligible"]
    raw_payload_bytes = sum(len(json.dumps(p.payload).encode("utf-8")) + len(p.vector) * 4 for p in raw_eligible)

    # 2. Benchmark Search Before Consolidation
    benchmark_queries = [
        ("temperature alert zone A", "sensor A3"),
        ("liquid chemical spill", "spill aisle 3"),
        ("low battery recharge", "battery 23%"),
        ("pneumatic pressure warning", "sensor D2"),
        ("corridor B impassable", "corridor B"),
    ]

    def eval_search_metrics():
        latencies = []
        reciprocal_ranks = []
        precisions = []

        for q, target_substr in benchmark_queries:
            t0 = time.perf_counter()
            results = store.search(q, limit=5)
            latencies.append((time.perf_counter() - t0) * 1000)

            # MRR & Precision@3
            found_rank = 0
            hits_in_top3 = 0
            for rank, r in enumerate(results[:3], start=1):
                if target_substr.lower() in r.text.lower():
                    if found_rank == 0:
                        found_rank = rank
                    hits_in_top3 += 1

            reciprocal_ranks.append(1.0 / found_rank if found_rank > 0 else 0.0)
            precisions.append(hits_in_top3 / 3.0)

        avg_lat = sum(latencies) / len(latencies)
        mrr = sum(reciprocal_ranks) / len(reciprocal_ranks)
        prec = sum(precisions) / len(precisions)
        return avg_lat, mrr, prec

    lat_before, mrr_before, prec_before = eval_search_metrics()

    # 3. Run Consolidation
    t_cons0 = time.perf_counter()
    cons_res = engine.run()
    cons_time_ms = (time.perf_counter() - t_cons0) * 1000

    consolidated_count = store.count_active()

    # 4. Benchmark Search After Consolidation
    lat_after, mrr_after, prec_after = eval_search_metrics()

    # Calculate consolidated sync payload size
    cons_points = store.get_all_points(with_vectors=True)
    cons_eligible = [p for p in cons_points if p.payload.get("sync_eligibility") == "sync_eligible" and not p.payload.get("is_tombstone", False)]
    cons_payload_bytes = sum(len(json.dumps(p.payload).encode("utf-8")) + len(p.vector) * 4 for p in cons_eligible)

    # Reductions
    dedup_ratio = round((raw_count - consolidated_count) / raw_count * 100, 2) if raw_count > 0 else 0.0
    payload_reduction_pct = round((raw_payload_bytes - cons_payload_bytes) / raw_payload_bytes * 100, 2) if raw_payload_bytes > 0 else 0.0

    return {
        "dataset_size_raw": raw_count,
        "dataset_size_consolidated": consolidated_count,
        "deduplication_ratio_pct": dedup_ratio,
        "records_merged": cons_res.records_merged,
        "clusters_detected": cons_res.clusters_found,
        "consolidation_duration_ms": round(cons_time_ms, 2),
        "search_benchmarks": {
            "mrr_before": round(mrr_before, 3),
            "mrr_after": round(mrr_after, 3),
            "precision_at_3_before": round(prec_before, 3),
            "precision_at_3_after": round(prec_after, 3),
            "query_latency_before_ms": round(lat_before, 2),
            "query_latency_after_ms": round(lat_after, 2),
        },
        "sync_payload_benchmarks": {
            "raw_payload_bytes": raw_payload_bytes,
            "consolidated_payload_bytes": cons_payload_bytes,
            "payload_reduction_pct": payload_reduction_pct,
            "bandwidth_saved_kb": round((raw_payload_bytes - cons_payload_bytes) / 1024, 2),
        },
    }


if __name__ == "__main__":
    from consolidation import ConsolidationEngine
    from qdrant_store import QdrantStore

    store = QdrantStore(path="./benchmark_qdrant_data")
    store.ensure_collection()
    engine = ConsolidationEngine(store)

    results = run_benchmark_suite(store, engine)
    print("=" * 60)
    print("CORTEX BENCHMARK RESULTS")
    print("=" * 60)
    print(json.dumps(results, indent=2))
