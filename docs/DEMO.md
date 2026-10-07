# CORTEX 3-Minute Live Demo Script

Follow this step-by-step walkthrough during judge evaluation or video presentation to showcase all core capabilities of CORTEX.

---

## 🎬 3-Minute Live Demo Sequence

### Step 1: Noisy Ingest & Baseline Search (0:00 – 0:30)
1. Open the Inspector Dashboard at `http://localhost:8000`.
2. Click **"Seed Sample Data"** on the Overview tab (or `POST /api/seed`). Notice 39 raw sensory logs loaded (e.g. repeated temperature spikes, slip hazards, motor errors).
3. Switch to the **Search** tab.
4. Click the demo query chip `ERR-4021` and switch between **Hybrid (RRF)**, **Dense Vector**, and **Sparse BM25**:
   - Notice that **Hybrid Mode** achieves exact matching for the alphanumeric error code while preserving semantic context, displaying sub-2ms search latency.

---

### Step 2: Brain-Inspired Sleep Cycle Memory Consolidation (0:30 – 1:10)
1. Switch to the **Sleep Cycle** tab.
2. Click the large button: **"Run Sleep Cycle"** (triggers graph neighborhood clustering, numeric variance checks, and importance-weighted decay).
3. Watch the animated progress bar complete.
4. Observe the results:
   - **Compaction**: Memories reduced from 39 to condensed canonical insights.
   - **Numeric Safeguard**: Near-duplicate readings with consistent temperatures/pressures merged into statistical summaries (`min`, `max`, `avg`, `latest`), while conflicting measurements stayed separate.
   - **Tombstones & Undo**: Low-importance decayed logs transition to soft-delete tombstones with a 5-minute undo window.
5. Go back to the **Search** tab and re-run your search: note how the result count is cleaner and free of noisy redundancy.

---

### Step 3: Anomaly Fast-Path & PII Protection (1:10 – 1:50)
1. Switch to the **Cloud Sync** tab.
2. Click **"Simulate Anomaly"** (or `POST /api/demo/inject-anomaly`).
3. Notice the live notification: Because nearest cosine similarity is below 0.70 ($k=5$), the engine identifies an out-of-distribution event and **immediately fast-tracks it to Cloud Qdrant** across the fiber channel, bypassing the scheduled sleep cycle.
4. Check the **Memory Explorer** tab: notice that user notes containing email addresses, phones, or SSNs are automatically tagged `local_only` (Coral badge) and never leave the device.

---

### Step 4: Real Air-Gapped Offline Resilience & Multi-Device Sync (1:50 – 2:30)
1. Stop the central cloud container in your terminal:
   ```bash
   docker stop cortex-cloud-qdrant
   ```
2. Watch the Dashboard UI instantly flip to **AIR-GAPPED (OFFLINE)** with animated disconnected fiber-optic shield.
3. Perform searches, add new memories, and inject anomalies while disconnected:
   - All queries and embeddings run locally at 100% speed.
   - Outgoing records are queued into the disk-backed offline journal.
4. Restart the central cloud container:
   ```bash
   docker start cortex-cloud-qdrant
   ```
5. Watch the dashboard automatically detect the connection, restore the fiber bridge, and **flush the offline queue in priority order** (high-priority anomalies dispatched first).

---

### Step 5: Distributed Multi-Device Version Vector Conflict Resolution (2:30 – 3:00)
1. Click **"Conflict Demo"** on the Sync tab (or `POST /api/demo/conflict`).
2. Inspect the **Version Vector Resolver Tree**:
   - Shows concurrent edits on the same memory from `edge-001` and `edge-002`.
   - Demonstrates component-wise vector comparison, max clock advancement (`{"edge-001": 2, "edge-002": 2, "edge-001": 3}`), tag union, and vector centroid averaging.
3. Switch to the **2D Simulation Arena** tab to watch the autonomous robot explore the warehouse grid, dodge dynamic obstacles, and log sensory events in real-time.
4. Conclude by highlighting the **Empirical Benchmark Numbers** (sub-2ms search latency across 10k records, over 60% bandwidth savings).
