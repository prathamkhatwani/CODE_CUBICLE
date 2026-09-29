# CORTEX — Edge Memory & Intelligence Platform

> AI-powered offline-first vector memory with brain-inspired consolidation and intelligent sync.  
> Built on **Qdrant Edge** · Problem Statement 03

![Architecture](https://img.shields.io/badge/Qdrant-Edge%20%2B%20Server-orange)
![Python](https://img.shields.io/badge/Python-3.11%2B-blue)
![License](https://img.shields.io/badge/License-MIT-green)

---

## 🧠 What Is This?

CORTEX is an **offline-first application** where each edge device runs Qdrant Edge as a local vector store. The device can embed, store, and semantically search data with **zero network dependency**, and intelligently syncs with a central Qdrant Server whenever connectivity returns.

### The Headline Innovation: Memory Consolidation ("Sleep Cycle")

Unlike typical "local vector store + push when online" solutions, CORTEX's edge device **periodically consolidates its own memory**, the way a brain consolidates during sleep:

| Step | What Happens |
|------|-------------|
| 🔍 **Detect near-duplicates** | DBSCAN clusters local vectors by cosine similarity to find memories describing the same event |
| 🔗 **Merge, don't append** | Collapses near-duplicate clusters into one higher-quality record via vector averaging |
| 📉 **Decay stale memories** | Low-value or outdated records fade out based on age and importance weighting |
| ☁️ **Sync the summary** | Pushes only the consolidated, high-signal set to the cloud — not the raw firehose |
| 🔒 **PII tagging** | Tags sensitive data as local-only with explainable reasons in the Inspector UI |

---

## 🏗️ Architecture

```
┌─────────────────────────────┐         ┌─────────────────────────────┐
│        EDGE DEVICE          │◄───────►│       CLOUD / SERVER        │
│                             │         │                             │
│  ┌───────────────────────┐  │         │  ┌───────────────────────┐  │
│  │ App / Sensors         │  │         │  │ Qdrant Server         │  │
│  └──────────┬────────────┘  │         │  │ (source of truth)     │  │
│  ┌──────────▼────────────┐  │         │  └──────────┬────────────┘  │
│  │ fastembed (local)     │  │         │  ┌──────────▼────────────┐  │
│  └──────────┬────────────┘  │         │  │ Conflict Resolver     │  │
│  ┌──────────▼────────────┐  │         │  └───────────────────────┘  │
│  │ Qdrant Edge (local)   │  │         │  ┌───────────────────────┐  │
│  └──────────┬────────────┘  │         │  │ Dashboard / Admin     │  │
│  ┌──────────▼────────────┐  │         │  └───────────────────────┘  │
│  │ Consolidation Engine  │  │         │                             │
│  │ "sleep cycle"         │  │         │                             │
│  └──────────┬────────────┘  │         │                             │
│  ┌──────────▼────────────┐  │         │                             │
│  │ Sync Agent            │  │         │                             │
│  └──────────┬────────────┘  │         │                             │
│  ┌──────────▼────────────┐  │         │                             │
│  │ Inspector Dashboard   │  │         │                             │
│  └───────────────────────┘  │         │                             │
└─────────────────────────────┘         └─────────────────────────────┘
```

---

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Docker (optional, for cloud Qdrant)

### 1. Install Dependencies

```bash
cd edge-memory-platform
pip install -r requirements.txt
```

### 2. Start Cloud Qdrant (optional — platform works offline without it)

```bash
docker-compose up -d
```

### 3. Run the Platform

```bash
cd backend
python main.py
```

### 4. Open the Dashboard

Navigate to **http://localhost:8000** in your browser.

---

## 📋 Demo Script (What Judges Should See)

| Step | Action | What to Look For |
|------|--------|-----------------|
| 1 | Click **Seed Data** | 35+ memories loaded, including deliberate near-duplicates |
| 2 | Run a search (e.g. "temperature alert") | Instant results with noisy/duplicate-heavy hits |
| 3 | Click **Run Sleep Cycle** | Dashboard shows memory count dropping, merge details appear |
| 4 | Re-run the same search | Cleaner results — merged records, higher quality |
| 5 | Toggle connectivity to **Online** | Sync Agent pushes consolidated summary (not raw firehose) |
| 6 | Check the **Activity Log** | Full audit trail: what merged, what decayed, what synced, and why |
| 7 | Toggle back to **Offline** | Everything keeps working — leaner, smarter memory |

---

## 🔧 Tech Stack

| Component | Technology |
|-----------|-----------|
| Vector Store (Edge) | Qdrant Edge (embedded via qdrant-client) |
| Vector Store (Cloud) | Qdrant Server (Docker) |
| Embeddings | fastembed — BAAI/bge-small-en-v1.5 (384 dims) |
| Consolidation | DBSCAN clustering (scikit-learn) + cosine similarity |
| Backend | FastAPI + uvicorn |
| Frontend | Vanilla HTML/CSS/JS + Chart.js |
| Connectivity | Simulated toggle + real ping detection |

---

## 📡 API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/status` | Device status (memory count, connectivity, etc.) |
| `GET` | `/api/memories` | List all memories |
| `POST` | `/api/memories` | Add a new memory |
| `POST` | `/api/search` | Semantic search |
| `POST` | `/api/consolidate` | Trigger consolidation ("sleep cycle") |
| `GET` | `/api/consolidation/history` | Past consolidation runs |
| `POST` | `/api/sync` | Trigger edge↔cloud sync |
| `GET` | `/api/sync/status` | Sync queue and connectivity |
| `POST` | `/api/connectivity/toggle` | Simulate online/offline |
| `GET` | `/api/activity` | Activity log (audit trail) |
| `GET` | `/api/stats` | Full dashboard statistics |
| `POST` | `/api/seed` | Load sample data |
| `POST` | `/api/reset` | Reset all data and history |

---

## 🧬 How Consolidation Works

1. **Snapshot** — Read all vectors from Qdrant Edge
2. **Cluster** — DBSCAN with `eps = 1 - similarity_threshold` and cosine metric
3. **Merge** — For each cluster: average vectors, keep best text, combine metadata
4. **Decay** — Reduce `decay_score` based on age (importance-weighted); drop below threshold
5. **Tag PII** — Regex scan for SSN, email, phone, CC, IP → mark `local_only` with reason
6. **Log** — Every decision recorded in the activity log for Inspector UI

---

## 📁 Project Structure

```
edge-memory-platform/
├── backend/
│   ├── main.py              # FastAPI application
│   ├── config.py            # Platform configuration
│   ├── models.py            # Pydantic data models
│   ├── embedding.py         # Local embedding pipeline (fastembed)
│   ├── qdrant_store.py      # Qdrant Edge/Cloud wrapper
│   ├── consolidation.py     # "Sleep cycle" consolidation engine
│   ├── sync_agent.py        # Edge↔cloud sync with conflict resolution
│   ├── seed_data.py         # Sample data generator
│   └── activity_log.py      # Audit trail logger
├── frontend/
│   ├── index.html           # Inspector Dashboard
│   ├── styles.css           # Design system
│   └── app.js               # Dashboard logic + API integration
├── docker-compose.yml       # Cloud Qdrant Server
├── requirements.txt         # Python dependencies
└── README.md                # This file
```

---

## ⚙️ Configuration

All settings can be overridden via environment variables with the `EDGE_` prefix:

```bash
export EDGE_DEVICE_ID="robot-42"
export EDGE_CLOUD_QDRANT_URL="https://my-qdrant.cloud:6334"
export EDGE_SIMILARITY_THRESHOLD="0.90"
```

---

## 📜 License

MIT
