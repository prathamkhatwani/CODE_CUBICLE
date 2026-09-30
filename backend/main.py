"""
Edge Memory & Intelligence Platform — FastAPI Application.

Serves both the REST API and the Inspector Dashboard UI.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Security, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles

from activity_log import activity_log
from config import settings
from consolidation import ConsolidationEngine
from models import (
    ActivityType,
    DashboardStats,
    DeviceStatus,
    MemoryCreate,
    MemoryResponse,
    SearchRequest,
    SearchResponse,
)
from qdrant_store import QdrantStore
from seed_data import generate_seed_data
from sync_agent import SyncAgent

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(name)-20s │ %(levelname)-7s │ %(message)s",
)
logger = logging.getLogger("cortex")

# ── Global state ─────────────────────────────────────────────────────────────
_start_time = time.time()
edge_store: QdrantStore | None = None
cloud_store: QdrantStore | None = None
consolidation_engine: ConsolidationEngine | None = None
sync_agent: SyncAgent | None = None

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_auth(x_api_key: str | None = Security(api_key_header)):
    """Authenticate API requests when AUTH_ENABLED is True."""
    if not settings.AUTH_ENABLED:
        return True
    if not x_api_key or x_api_key != settings.API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key. Provide a valid 'X-API-Key' header.",
        )
    return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise stores and engines on startup."""
    global edge_store, cloud_store, consolidation_engine, sync_agent

    # Edge store (local, always available)
    edge_store = QdrantStore(path=settings.EDGE_QDRANT_PATH)
    edge_store.ensure_collection()
    logger.info("Edge Qdrant ready at %s", settings.EDGE_QDRANT_PATH)

    # Cloud store (may be unavailable)
    try:
        cloud_store = QdrantStore(url=settings.CLOUD_QDRANT_URL)
        cloud_store.ensure_collection()
        logger.info("Cloud Qdrant connected at %s", settings.CLOUD_QDRANT_URL)
    except Exception as exc:
        logger.info("External cloud Qdrant not active at %s (%s). Using simulated local Cloud Qdrant store for full sync demo.", settings.CLOUD_QDRANT_URL, exc)
        cloud_store = QdrantStore(path="./qdrant_cloud_data")
        cloud_store.ensure_collection()

    # Engines
    consolidation_engine = ConsolidationEngine(edge_store)
    sync_agent = SyncAgent(edge_store, cloud_store)

    # Initial connectivity check
    sync_agent.check_connectivity()

    activity_log.log(
        ActivityType.CONNECTIVITY_CHANGED,
        "Platform started",
        f"Device {settings.DEVICE_ID} ({settings.DEVICE_NAME}) initialised. "
        f"{'Online' if sync_agent.is_online else 'Offline'} mode.",
    )

    yield

    logger.info("Shutting down Edge Memory Platform.")


# ── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Edge Memory & Intelligence Platform",
    description="AI-powered offline-first vector memory with consolidation and sync.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend static files
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


# ══════════════════════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════════════════════


# ── Dashboard UI ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Serve the Inspector Dashboard."""
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    return HTMLResponse("<h1>Frontend not found</h1><p>Place index.html in /frontend</p>")


@app.get("/styles.css")
async def serve_styles():
    css = FRONTEND_DIR / "styles.css"
    if css.exists():
        return FileResponse(str(css), media_type="text/css", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    return HTMLResponse("", status_code=404)


@app.get("/app.js")
async def serve_app_js():
    js = FRONTEND_DIR / "app.js"
    if js.exists():
        return FileResponse(str(js), media_type="application/javascript", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    return HTMLResponse("", status_code=404)


# ── Device Status ────────────────────────────────────────────────────────────

@app.get("/api/status", response_model=DeviceStatus)
async def get_status():
    all_pts = edge_store.get_all_points(with_vectors=False)
    active_pts = [p for p in all_pts if not p.payload.get("is_tombstone", False)]
    tombstones = [p for p in all_pts if p.payload.get("is_tombstone", False)]
    total = len(active_pts)
    synced = sum(1 for p in active_pts if p.payload.get("synced", False))
    pending = sum(
        1 for p in active_pts
        if p.payload.get("sync_eligibility") == "sync_eligible"
        and not p.payload.get("synced", False)
    )
    local_only = sum(
        1 for p in active_pts
        if p.payload.get("sync_eligibility") == "local_only"
    )
    avg_decay = (
        sum(p.payload.get("decay_score", 1.0) for p in active_pts) / total
        if total > 0
        else 0.0
    )

    last_consol = (
        consolidation_engine.history[-1].completed_at
        if consolidation_engine.history
        else None
    )
    last_sync = (
        sync_agent.history[-1].completed_at
        if sync_agent.history
        else None
    )

    return DeviceStatus(
        device_id=settings.DEVICE_ID,
        device_name=settings.DEVICE_NAME,
        is_online=sync_agent.is_online,
        total_memories=total,
        synced_memories=synced,
        pending_sync=pending,
        local_only_memories=local_only,
        tombstone_count=len(tombstones),
        avg_decay_score=round(avg_decay, 3),
        last_consolidation=last_consol,
        last_sync=last_sync,
        uptime_seconds=round(time.time() - _start_time, 1),
        embedding_model=settings.EMBEDDING_MODEL,
        vector_dimensions=settings.VECTOR_SIZE,
        demo_mode=settings.DEMO_MODE,
        auth_enabled=settings.AUTH_ENABLED,
    )


# ── Memories CRUD ────────────────────────────────────────────────────────────

@app.get("/api/memories", response_model=list[MemoryResponse])
async def list_memories(
    limit: int = 100,
    category: str | None = None,
    include_tombstones: bool = False,
):
    all_pts = edge_store.get_all_points(with_vectors=False)
    if not include_tombstones:
        all_pts = [p for p in all_pts if not p.payload.get("is_tombstone", False)]
    if category:
        all_pts = [p for p in all_pts if p.payload.get("category") == category]
    # Sort by created_at descending
    all_pts.sort(key=lambda p: p.payload.get("created_at", ""), reverse=True)
    return [edge_store.point_to_response(p) for p in all_pts[:limit]]


@app.post("/api/memories", response_model=MemoryResponse)
async def add_memory(mem: MemoryCreate, _: bool = Depends(verify_auth)):
    pid, is_anomaly, nearest_sim = edge_store.add_memory(
        text=mem.text,
        source=mem.source,
        importance=mem.importance,
        tags=mem.tags,
        category=mem.category,
        metadata=mem.metadata,
        check_for_anomaly=True,
    )
    if is_anomaly:
        activity_log.log(
            ActivityType.ANOMALY_DETECTED,
            "Anomaly detected (Priority Tag)",
            f"Novel memory detected (nearest similarity: {nearest_sim:.2f} < {settings.ANOMALY_THRESHOLD}). Tagged high-priority.",
            {"id": pid, "similarity": nearest_sim, "threshold": settings.ANOMALY_THRESHOLD},
        )
        # Trigger immediate fast-path priority sync
        sync_agent.sync_priority_fast_path(pid)
    else:
        activity_log.log(
            ActivityType.MEMORY_ADDED,
            "Memory added",
            f"'{mem.text[:60]}…' from {mem.source}",
            {"id": pid, "category": mem.category, "similarity": nearest_sim},
        )

    point = edge_store.get_point(pid)
    return edge_store.point_to_response(point)


@app.post("/api/demo/inject-anomaly")
async def inject_anomaly_demo():
    """Inject a deliberately novel out-of-distribution event to test priority sync."""
    import random
    novel_scenarios = [
        "CRITICAL ANOMALY: Unknown cyber-physical telemetry signal detected on auxiliary CAN bus at port 9999 with high-entropy encrypted payload",
        "UNEXPECTED PHENOMENON: Quantum magnetometer detected a 4.2 Tesla field fluctuation in basement sub-level 4",
        "SECURITY BREACH ALERT: Cryptographic signature mismatch detected across firmware bootstrap vector in robot arm cluster #7",
    ]
    text = random.choice(novel_scenarios)
    pid, is_anomaly, nearest_sim = edge_store.add_memory(
        text=text,
        source="anomaly-sensor",
        importance=0.98,
        tags=["anomaly", "critical", "priority-sync"],
        category="incident_reports",
        check_for_anomaly=True,
    )

    sim_str = f"{nearest_sim:.2f}" if nearest_sim is not None else "0.00"
    activity_log.log(
        ActivityType.ANOMALY_DETECTED,
        "🚨 Anomaly injected (Demo)",
        f"Novel event injected (similarity {sim_str} < {settings.ANOMALY_THRESHOLD}) — fast-path priority sync triggered.",
        {"id": pid, "similarity": nearest_sim, "text": text[:80]},
    )

    pushed = sync_agent.sync_priority_fast_path(pid)
    point = edge_store.get_point(pid)
    return {
        "status": "anomaly_injected",
        "memory": edge_store.point_to_response(point),
        "is_anomaly": is_anomaly,
        "nearest_similarity": nearest_sim,
        "fast_path_pushed": pushed,
        "message": f"Anomaly detected (nearest similarity: {sim_str}). Pushed immediately to cloud via fast-path." if pushed else f"Anomaly detected (nearest similarity: {sim_str}). Queued for priority sync."
    }


@app.get("/api/memories/{memory_id}", response_model=MemoryResponse)
async def get_memory(memory_id: str):
    point = edge_store.get_point(memory_id)
    if not point:
        raise HTTPException(404, "Memory not found")
    return edge_store.point_to_response(point)


@app.post("/api/memories/{memory_id}/restore")
async def restore_memory(memory_id: str, _: bool = Depends(verify_auth)):
    """Restore a soft-deleted tombstoned memory within its undo window."""
    success = consolidation_engine.restore_tombstone(memory_id)
    if not success:
        raise HTTPException(400, "Memory is not a valid recoverable tombstone or already purged.")
    return {"status": "restored", "memory_id": memory_id}


@app.delete("/api/memories/{memory_id}")
async def delete_memory(memory_id: str, _: bool = Depends(verify_auth)):
    point = edge_store.get_point(memory_id)
    if not point:
        raise HTTPException(404, "Memory not found")
    edge_store.delete_points([memory_id])
    activity_log.log(
        ActivityType.MEMORY_DELETED,
        "Memory deleted",
        f"Record {memory_id[:8]}… removed.",
    )
    return {"deleted": memory_id}


# ── Search ───────────────────────────────────────────────────────────────────

@app.post("/api/search", response_model=SearchResponse)
async def search_memories(req: SearchRequest):
    t0 = time.perf_counter()
    results = edge_store.search(
        query=req.query,
        limit=req.limit,
        category=req.category,
        min_importance=req.min_importance,
        include_local_only=req.include_local_only,
        include_tombstones=req.include_tombstones,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000

    activity_log.log(
        ActivityType.SEARCH_PERFORMED,
        "Search performed",
        f"Query: '{req.query}' → {len(results)} results in {elapsed_ms:.1f}ms",
        {"query": req.query, "results": len(results), "time_ms": elapsed_ms},
    )

    return SearchResponse(
        query=req.query,
        results=results,
        total_found=len(results),
        search_time_ms=round(elapsed_ms, 2),
    )


# ── Consolidation ───────────────────────────────────────────────────────────

@app.post("/api/consolidate")
async def run_consolidation(_: bool = Depends(verify_auth)):
    """Trigger the consolidation 'sleep cycle'."""
    result = consolidation_engine.run()
    return result


@app.get("/api/consolidation/history")
async def consolidation_history():
    return consolidation_engine.history


# ── Sync ─────────────────────────────────────────────────────────────────────

@app.post("/api/sync")
async def run_sync(_: bool = Depends(verify_auth)):
    result = sync_agent.sync()
    return result


@app.get("/api/sync/status")
async def sync_status():
    return {
        "is_online": sync_agent.is_online,
        "pending_count": sync_agent.get_pending_count(),
        "synced_count": sync_agent.get_synced_count(),
        "last_sync": (
            sync_agent.history[-1].model_dump()
            if sync_agent.history
            else None
        ),
    }


@app.get("/api/sync/history")
async def sync_history():
    return sync_agent.history


@app.post("/api/connectivity/toggle")
async def toggle_connectivity():
    """Simulate online/offline for demo purposes."""
    is_online = sync_agent.toggle_simulated_connectivity()
    return {"is_online": is_online, "mode": "simulated"}


# ── Activity Log ─────────────────────────────────────────────────────────────

@app.get("/api/activity")
async def get_activity(limit: int = 50, activity_type: str | None = None):
    entries = activity_log.get_entries(limit=limit, activity_type=activity_type)
    return [e.model_dump() for e in entries]


# ── Dashboard Stats ──────────────────────────────────────────────────────────

@app.get("/api/stats", response_model=DashboardStats)
async def get_stats():
    all_pts = edge_store.get_all_points(with_vectors=False)
    active_pts = [p for p in all_pts if not p.payload.get("is_tombstone", False)]
    tombstones = [p for p in all_pts if p.payload.get("is_tombstone", False)]
    total = len(active_pts)

    synced = sum(1 for p in active_pts if p.payload.get("synced", False))
    pending = sum(
        1 for p in active_pts
        if p.payload.get("sync_eligibility") == "sync_eligible"
        and not p.payload.get("synced", False)
    )
    local_only = sum(
        1 for p in active_pts if p.payload.get("sync_eligibility") == "local_only"
    )

    avg_decay = (
        sum(p.payload.get("decay_score", 1.0) for p in active_pts) / total
        if total
        else 0.0
    )
    avg_imp = (
        sum(p.payload.get("importance", 0.5) for p in active_pts) / total
        if total
        else 0.0
    )

    # Category breakdown
    categories: dict[str, int] = {}
    sources: dict[str, int] = {}
    for p in active_pts:
        cat = p.payload.get("category", "general")
        categories[cat] = categories.get(cat, 0) + 1
        src = p.payload.get("source", "unknown")
        sources[src] = sources.get(src, 0) + 1

    # Memory timeline from consolidation history
    timeline = []
    for cr in consolidation_engine.history:
        timeline.append({
            "timestamp": cr.started_at,
            "before": cr.memories_before,
            "after": cr.memories_after,
            "merged": cr.records_merged,
            "decayed": cr.records_decayed,
        })

    return DashboardStats(
        total_memories=total,
        synced_count=synced,
        pending_count=pending,
        local_only_count=local_only,
        tombstone_count=len(tombstones),
        avg_decay_score=round(avg_decay, 3),
        avg_importance=round(avg_imp, 3),
        categories=categories,
        sources=sources,
        memory_timeline=timeline,
        consolidation_history=consolidation_engine.history,
        sync_history=sync_agent.history,
    )


# ── Benchmark Endpoint ───────────────────────────────────────────────────────

@app.get("/api/benchmark")
async def run_benchmark():
    """Run an automated benchmark demonstrating deduplication ratio, search MRR, and payload savings."""
    import benchmark
    results = benchmark.run_benchmark_suite(edge_store, consolidation_engine)
    return results


# ── Seed Data & Reset ────────────────────────────────────────────────────────

@app.post("/api/seed")
async def seed_data():
    """Load sample data with deliberate near-duplicates for demo."""
    result = generate_seed_data(edge_store)
    return result


@app.post("/api/reset")
async def reset_all(x_api_key: str | None = Header(None)):
    """Reset the edge store and all history. Protected in production mode."""
    if not settings.DEMO_MODE:
        if not x_api_key or x_api_key != settings.API_KEY:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Reset endpoint is disabled in production mode. Set DEMO_MODE=True or provide admin API key.",
            )

    edge_store.reset_collection()
    consolidation_engine.history.clear()
    sync_agent.history.clear()
    activity_log.clear()
    activity_log.log(
        ActivityType.CONNECTIVITY_CHANGED,
        "Platform reset",
        "All data, history, and logs cleared.",
    )
    return {"status": "reset complete"}


# ── Run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
