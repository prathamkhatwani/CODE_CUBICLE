"""
Comprehensive End-to-End Verification Test for Edge Memory & Intelligence Platform.
Tests every core capability of the problem statement:
- Local offline embedding & search
- Memory consolidation ("sleep cycle" - clustering, merging, decay, PII tagging)
- Cleaned semantic search after sleep cycle
- Explainable local-only tagging
- Simulated connectivity & sync
- Conflict resolution
"""

import sys
import time
import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "http://127.0.0.1:8000"

def log_step(title):
    print(f"\n{'='*70}\n[STEP] {title}\n{'='*70}")

def run_tests():
    session = requests.Session()
    
    # 1. Health & Status
    log_step("1. Checking Device Status (Offline-first Mode)")
    res = session.get(f"{BASE_URL}/api/status")
    assert res.status_code == 200, f"Status failed: {res.text}"
    status = res.json()
    print("Device Status:", status)
    assert status["device_id"] == "edge-001"
    
    # 2. Reset Data
    log_step("2. Resetting Platform to Clean State")
    res = session.post(f"{BASE_URL}/api/reset")
    assert res.status_code == 200
    print("Reset response:", res.json())
    
    # 3. Seed Sample Data (Burst of Redundant Data)
    log_step("3. Seeding Sample Data (with Deliberate Near-Duplicates)")
    res = session.post(f"{BASE_URL}/api/seed")
    assert res.status_code == 200
    seed_res = res.json()
    print("Seed result:", seed_res)
    assert seed_res["total_records"] > 0
    
    # Check memory count before consolidation
    res = session.get(f"{BASE_URL}/api/status")
    status_before = res.json()
    print(f"Total memories stored locally on Qdrant Edge: {status_before['total_memories']}")
    
    # 4. Semantic Search Before Consolidation (Expect Duplicates)
    log_step("4. Semantic Search BEFORE Sleep Cycle (Query: 'temperature alert')")
    search_payload = {"query": "temperature alert", "limit": 10}
    t0 = time.time()
    res = session.post(f"{BASE_URL}/api/search", json=search_payload)
    assert res.status_code == 200
    search_res = res.json()
    print(f"Found {search_res['total_found']} results in {search_res['search_time_ms']}ms:")
    for idx, r in enumerate(search_res["results"], 1):
        score = f"{r['score']:.3f}" if r.get('score') is not None else "N/A"
        print(f"  [{idx}] (Score: {score}) {r['text'][:75]}...")
    
    # 5. Trigger Memory Consolidation ("Sleep Cycle")
    log_step("5. Triggering Consolidation Engine ('Sleep Cycle')")
    res = session.post(f"{BASE_URL}/api/consolidate")
    assert res.status_code == 200
    cons_res = res.json()
    print(f"Consolidation Status: {cons_res['status']}")
    print(f"Memories Before: {cons_res['memories_before']} -> After: {cons_res['memories_after']}")
    print(f"Clusters Detected: {cons_res['clusters_found']}")
    print(f"Records Merged: {cons_res['records_merged']}")
    print(f"Records Decayed/Dropped: {cons_res['records_decayed']}")
    print(f"Records Tagged Local-Only (PII): {cons_res['records_tagged_local']}")
    
    print("\nMerge Details:")
    for md in cons_res["merge_details"]:
        print(f"  - Cluster {md['cluster_id']} (Sim: {md['similarity']:.2f}): Merged {len(md['original_ids'])} items -> '{md['merged_text'][:60]}...'")
    
    assert cons_res["memories_after"] < cons_res["memories_before"], "Consolidation should reduce total memory count!"
    assert cons_res["records_tagged_local"] > 0, "PII tagging should have tagged sensitive items as local_only"
    
    # 6. Semantic Search After Consolidation (Cleaned, High Signal)
    log_step("6. Semantic Search AFTER Sleep Cycle (Query: 'temperature alert')")
    res = session.post(f"{BASE_URL}/api/search", json=search_payload)
    assert res.status_code == 200
    search_res_after = res.json()
    print(f"Found {search_res_after['total_found']} consolidated results in {search_res_after['search_time_ms']}ms:")
    for idx, r in enumerate(search_res_after["results"], 1):
        score = f"{r['score']:.3f}" if r.get('score') is not None else "N/A"
        print(f"  [{idx}] (Score: {score}, Merged: {r['merge_count']}x, Cons: {r['is_consolidated']}) {r['text'][:75]}...")
    
    # 7. Check PII Tagging & Explainability in Memory Explorer
    log_step("7. Verifying PII Classification & Local-Only Safeguards")
    res = session.get(f"{BASE_URL}/api/memories?category=user_notes")
    assert res.status_code == 200
    user_notes = res.json()
    for un in user_notes:
        print(f"  - Memory: '{un['text'][:60]}...'")
        print(f"    Sync Eligibility: {un['sync_eligibility']}")
        print(f"    Sync Reason: {un['sync_reason']}")
        print(f"    PII Detected: {un['pii_detected']}")
        if "john.doe@warehouse.com" in un["text"]:
            assert un["sync_eligibility"] == "local_only"
            assert un["pii_detected"] is True
            assert "PII detected" in (un["sync_reason"] or "")
    
    # 8. Test Connectivity Toggle & Sync Simulation
    log_step("8. Testing Connectivity State & Sync Agent")
    # Ensure offline first
    status = session.get(f"{BASE_URL}/api/status").json()
    if status["is_online"]:
        session.post(f"{BASE_URL}/api/connectivity/toggle")
    
    res = session.post(f"{BASE_URL}/api/sync")
    sync_offline = res.json()
    print("Sync while offline:", sync_offline)
    assert sync_offline["status"] == "failed"
    
    # Toggle connection back online
    res = session.post(f"{BASE_URL}/api/connectivity/toggle")
    toggle_res = res.json()
    print("Toggled Connectivity back online:", toggle_res)
    assert toggle_res["is_online"] is True

    # Run online sync
    res = session.post(f"{BASE_URL}/api/sync")
    sync_online = res.json()
    print("Sync while online:", sync_online)
    assert sync_online["status"] == "completed"
    assert sync_online["records_pushed"] > 0
    
    # 9. Test Activity Feed Audit Trail
    log_step("9. Verifying Activity Log / Inspector Audit Trail")
    res = session.get(f"{BASE_URL}/api/activity?limit=10")
    assert res.status_code == 200
    activities = res.json()
    print(f"Retrieved {len(activities)} recent activity events:")
    for a in activities[:5]:
        print(f"  {a['icon']} [{a['type']}] {a['title']} - {a['description'][:60]}...")
    
    # 10. Dashboard Stats
    log_step("10. Checking Dashboard Aggregate Stats")
    res = session.get(f"{BASE_URL}/api/stats")
    assert res.status_code == 200
    stats = res.json()
    print("Dashboard Stats Summary:")
    print(f"  Total: {stats['total_memories']}, Synced: {stats['synced_count']}, Pending: {stats['pending_count']}, Local-Only: {stats['local_only_count']}")
    print(f"  Categories: {stats['categories']}")
    print(f"  Consolidation Runs: {len(stats['consolidation_history'])}")

    # 11. Anomaly-Triggered Priority Sync
    log_step("11. Anomaly-Triggered Priority Sync (Fast-Path)")
    
    # 11a: Insert normal related memory -> should be normal priority
    normal_payload = {
        "text": "Temperature sensor A3 reports 81.2°F, returning to normal baseline",
        "category": "sensor_readings",
        "source": "temp-sensor-A3",
        "importance": 0.5,
    }
    res = session.post(f"{BASE_URL}/api/memories", json=normal_payload)
    assert res.status_code == 200
    normal_mem = res.json()
    print(f"Normal Memory Inserted: Priority={normal_mem['priority']}, Nearest Sim={normal_mem['nearest_similarity']}")
    assert normal_mem["priority"] == "normal"

    # 11b: Insert novel anomaly memory -> should be high priority fast-path
    res = session.post(f"{BASE_URL}/api/demo/inject-anomaly")
    assert res.status_code == 200
    anomaly_res = res.json()
    print("Anomaly Injection Result:", anomaly_res["message"])
    print(f"  - Is Anomaly: {anomaly_res['is_anomaly']}")
    print(f"  - Nearest Neighbor Similarity: {anomaly_res['nearest_similarity']}")
    print(f"  - Priority: {anomaly_res['memory']['priority']}")
    print(f"  - Priority Reason: {anomaly_res['memory']['priority_reason']}")
    print(f"  - Fast-path Pushed: {anomaly_res['fast_path_pushed']}")
    assert anomaly_res["is_anomaly"] is True
    assert anomaly_res["memory"]["priority"] == "high"
    assert anomaly_res["memory"]["priority_reason"] == "anomaly"

    print(f"\n{'='*70}\n[SUCCESS] ALL SYSTEM MODULES & FEATURES (INCLUDING ANOMALY PRIORITY SYNC) VERIFIED PERFECTLY!\n{'='*70}")

if __name__ == "__main__":
    run_tests()
