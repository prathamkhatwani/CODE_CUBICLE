/**
 * CORTEX — Edge Memory Platform
 * Dashboard Application
 * 
 * Connects to the FastAPI backend at localhost:8000
 * Auto-refreshes status every 5 seconds
 */

// ═══════════════════════════════════════════════════════════════════════
//  STATE
// ═══════════════════════════════════════════════════════════════════════
let memoryChart = null;
let categoryChart = null;
let currentView = 'overview';

// ═══════════════════════════════════════════════════════════════════════
//  UTILITIES
// ═══════════════════════════════════════════════════════════════════════
const API = 'http://localhost:8000';

async function api(endpoint, opts = {}) {
    const res = await fetch(`${API}${endpoint}`, {
        ...opts,
        headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    });
    if (!res.ok) {
        const err = await res.text();
        throw new Error(err || `HTTP ${res.status}`);
    }
    return res.json();
}

function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add('show'));
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 350);
    }, 3500);
}

function timeAgo(iso) {
    if (!iso) return 'Never';
    const diff = (Date.now() - new Date(iso).getTime()) / 1000;
    if (diff < 5) return 'Just now';
    if (diff < 60) return `${Math.floor(diff)}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return new Date(iso).toLocaleDateString();
}

function fmtDate(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function truncate(s, n = 80) {
    return s && s.length > n ? s.slice(0, n) + '…' : (s || '');
}

function syncBadge(mem) {
    let badges = '';
    if (mem.priority === 'high' || mem.priority_reason === 'anomaly') {
        const simTxt = mem.nearest_similarity !== null && mem.nearest_similarity !== undefined
            ? `(sim: ${(mem.nearest_similarity * 100).toFixed(0)}%)`
            : '';
        badges += `<span class="badge anomaly" title="Novel memory ${simTxt} — Priority fast-path">⚡ Anomaly</span> `;
    }
    if (mem.sync_eligibility === 'local_only') {
        badges += '<span class="badge local">Local Only</span>';
    } else if (mem.synced) {
        badges += '<span class="badge synced">Synced</span>';
    } else {
        badges += '<span class="badge pending">Pending</span>';
    }
    return badges;
}

function importanceBar(val) {
    const pct = Math.round(val * 100);
    return `<div class="importance-bar-container"><div class="importance-bar" style="width:${pct}%"></div></div>`;
}

function decayIndicator(val) {
    let state = 'healthy';
    let label = 'Fresh';
    if (val < 0.4) {
        state = 'stale';
        label = 'Stale';
    } else if (val < 0.75) {
        state = 'aging';
        label = 'Aging';
    }
    return `
        <div class="decay-pill ${state}" title="Decay retention score: ${val.toFixed(2)} (${label})">
            <span class="decay-dot"></span>
            <span>${val.toFixed(2)}</span>
        </div>
    `;
}


// ═══════════════════════════════════════════════════════════════════════
//  NAVIGATION
// ═══════════════════════════════════════════════════════════════════════
document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const view = btn.dataset.view;
        document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
        document.getElementById(`view-${view}`).classList.add('active');
        currentView = view;
        refreshView(view);
    });
});

function refreshView(view) {
    if (view === 'overview') { refreshOverview(); }
    if (view === 'explorer') { refreshExplorer(); }
    if (view === 'activity') { refreshActivity(); }
    if (view === 'sync') { refreshSyncPanel(); }
    if (view === 'consolidation') { refreshConsolidation(); }
    if (view === 'simulation' && window.CORTEX_SIM) { /* active */ }
}


// ═══════════════════════════════════════════════════════════════════════
//  HEADER STATUS (auto-refresh every 5s)
// ═══════════════════════════════════════════════════════════════════════
async function refreshStatus() {
    try {
        const s = await api('/api/status');
        document.getElementById('device-name').textContent = s.device_name || 'Edge Device';
        document.getElementById('model-info').textContent = s.embedding_model || 'bge-small-en-v1.5';

        const dot = document.getElementById('connection-dot');
        const txt = document.getElementById('connection-text');
        dot.className = s.is_online ? 'pulse-dot online' : 'pulse-dot offline';
        txt.textContent = s.is_online ? 'Online' : 'Offline';
    } catch (e) {
        // API unreachable
        document.getElementById('connection-dot').className = 'pulse-dot offline';
        document.getElementById('connection-text').textContent = 'Disconnected';
    }
}


// ═══════════════════════════════════════════════════════════════════════
//  OVERVIEW VIEW
// ═══════════════════════════════════════════════════════════════════════
async function refreshOverview() {
    try {
        const stats = await api('/api/stats');
        document.getElementById('stat-total').textContent = stats.total_memories;
        document.getElementById('stat-synced').textContent = stats.synced_count;
        document.getElementById('stat-pending').textContent = stats.pending_count;
        document.getElementById('stat-decay').textContent = stats.avg_decay_score.toFixed(2);

        updateCharts(stats);
    } catch (e) {
        console.warn('Stats refresh failed:', e);
    }
}

function updateCharts(stats) {
    // ── Memory Timeline Chart ──
    const ctxMem = document.getElementById('memory-chart');
    const timeline = stats.memory_timeline || [];

    if (!memoryChart) {
        Chart.defaults.color = '#4A5568';
        Chart.defaults.font.family = "'Inter', -apple-system, sans-serif";

        const labels = timeline.length > 0
            ? timeline.map((t, i) => `Cycle ${i + 1}`)
            : ['Current'];
        const beforeData = timeline.length > 0
            ? timeline.map(t => t.before)
            : [stats.total_memories];
        const afterData = timeline.length > 0
            ? timeline.map(t => t.after)
            : [stats.total_memories];

        memoryChart = new Chart(ctxMem, {
            type: 'line',
            data: {
                labels,
                datasets: [
                    {
                        label: 'Before Consolidation',
                        data: beforeData,
                        borderColor: '#F5A623',
                        backgroundColor: 'rgba(245, 166, 35, 0.08)',
                        tension: 0.35,
                        fill: true,
                        pointRadius: 5,
                        pointBackgroundColor: '#F5A623',
                        borderWidth: 2.5,
                    },
                    {
                        label: 'After Consolidation',
                        data: afterData,
                        borderColor: '#0FB8A0',
                        backgroundColor: 'rgba(15, 184, 160, 0.08)',
                        tension: 0.35,
                        fill: true,
                        pointRadius: 5,
                        pointBackgroundColor: '#0FB8A0',
                        borderWidth: 2.5,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: { grid: { color: 'rgba(226, 232, 240, 0.7)' }, ticks: { color: '#718096' } },
                    y: { grid: { color: 'rgba(226, 232, 240, 0.7)' }, beginAtZero: true, ticks: { color: '#718096' } },
                },
                plugins: {
                    legend: { labels: { boxWidth: 12, padding: 16, color: '#16213E', font: { weight: '600' } } },
                },
            },
        });
    } else {
        // Update existing chart
        const labels = timeline.length > 0
            ? timeline.map((_, i) => `Cycle ${i + 1}`)
            : ['Current'];
        memoryChart.data.labels = labels;
        memoryChart.data.datasets[0].data = timeline.length > 0 ? timeline.map(t => t.before) : [stats.total_memories];
        memoryChart.data.datasets[1].data = timeline.length > 0 ? timeline.map(t => t.after) : [stats.total_memories];
        memoryChart.update();
    }

    // ── Category Doughnut ──
    const ctxCat = document.getElementById('category-chart');
    const cats = stats.categories || {};
    const catLabels = Object.keys(cats);
    const catData = Object.values(cats);
    const catColors = ['#16213E', '#0FB8A0', '#F5A623', '#E85D4A', '#4A5568', '#718096'];

    if (!categoryChart) {
        categoryChart = new Chart(ctxCat, {
            type: 'doughnut',
            data: {
                labels: catLabels,
                datasets: [{ data: catData, backgroundColor: catColors.slice(0, catLabels.length), borderWidth: 2, borderColor: '#FFFFFF' }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '70%',
                plugins: { legend: { position: 'right', labels: { boxWidth: 10, padding: 10, color: '#4A5568', font: { size: 12 } } } },
            },
        });
    } else {
        categoryChart.data.labels = catLabels;
        categoryChart.data.datasets[0].data = catData;
        categoryChart.data.datasets[0].backgroundColor = catColors.slice(0, catLabels.length);
        categoryChart.update();
    }
}


// ═══════════════════════════════════════════════════════════════════════
//  MEMORY EXPLORER VIEW
// ═══════════════════════════════════════════════════════════════════════
async function refreshExplorer() {
    const tbody = document.getElementById('explorer-tbody');
    const filterCat = document.getElementById('filter-category').value;
    try {
        const endpoint = filterCat === 'all' ? '/api/memories' : `/api/memories?category=${filterCat}`;
        const memories = await api(endpoint);
        tbody.innerHTML = '';

        if (memories.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text-muted);padding:40px">No memories yet. Seed sample data from the Activity tab.</td></tr>';
            return;
        }

        memories.forEach(m => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td title="${m.text}">${truncate(m.text, 60)}</td>
                <td><span style="color:var(--accent-secondary)">${m.source}</span></td>
                <td>${m.category}</td>
                <td>${importanceBar(m.importance)}</td>
                <td>${decayIndicator(m.decay_score)}</td>
                <td>${syncBadge(m)}</td>
                <td style="color:var(--text-muted);font-size:0.8rem">${fmtDate(m.created_at)}</td>
            `;
            tbody.appendChild(tr);
        });
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--accent-error)">Failed to load memories</td></tr>';
    }
}

document.getElementById('filter-category').addEventListener('change', refreshExplorer);
document.getElementById('btn-refresh-explorer').addEventListener('click', refreshExplorer);


// ═══════════════════════════════════════════════════════════════════════
//  SEARCH VIEW
// ═══════════════════════════════════════════════════════════════════════
let searchTimer = null;
document.getElementById('search-input').addEventListener('input', (e) => {
    clearTimeout(searchTimer);
    const q = e.target.value.trim();
    const container = document.getElementById('search-results');

    if (!q) {
        container.innerHTML = '<div class="empty-state">Enter a query to search memories semantically</div>';
        return;
    }

    searchTimer = setTimeout(async () => {
        try {
            const res = await api('/api/search', { method: 'POST', body: JSON.stringify({ query: q, limit: 15 }) });
            container.innerHTML = '';

            if (!res.results || res.results.length === 0) {
                container.innerHTML = '<div class="empty-state">No matching memories found</div>';
                return;
            }

            // Search header
            const header = document.createElement('div');
            header.className = 'search-info';
            header.innerHTML = `Found <strong>${res.total_found}</strong> results in <strong>${res.search_time_ms.toFixed(1)}ms</strong>`;
            container.appendChild(header);

            res.results.forEach(r => {
                const card = document.createElement('div');
                card.className = 'search-card';
                const scorePct = r.score ? (r.score * 100).toFixed(1) : '—';
                card.innerHTML = `
                    <div class="search-card-text">${r.text}</div>
                    <div class="search-card-score">
                        <div class="score-bar-bg"><div class="score-bar-fill" style="width:${r.score ? r.score * 100 : 0}%"></div></div>
                        <span class="score-value">${scorePct}%</span>
                    </div>
                    <div class="search-meta">
                        ${syncBadge(r)}
                        <span>Source: <em>${r.source}</em></span>
                        <span>Category: <em>${r.category}</em></span>
                        ${r.tags && r.tags.length > 0 ? r.tags.map(t => `<span class="tag">${t}</span>`).join('') : ''}
                        ${r.is_consolidated ? '<span class="badge consolidated">Consolidated</span>' : ''}
                        ${r.pii_detected ? '<span class="badge local">PII</span>' : ''}
                    </div>
                `;
                container.appendChild(card);
            });
        } catch (e) {
            container.innerHTML = '<div class="empty-state" style="color:var(--accent-error)">Search failed — is the backend running?</div>';
        }
    }, 300);
});


// ═══════════════════════════════════════════════════════════════════════
//  CONSOLIDATION VIEW
// ═══════════════════════════════════════════════════════════════════════
async function refreshConsolidation() {
    try {
        const history = await api('/api/consolidation/history');
        if (history.length > 0) {
            const last = history[history.length - 1];
            document.getElementById('cons-last-run').textContent = timeAgo(last.completed_at);
            document.getElementById('cons-merged').textContent = history.reduce((s, h) => s + h.records_merged, 0);
            document.getElementById('cons-decayed').textContent = history.reduce((s, h) => s + h.records_decayed, 0);

            renderConsolidationLog(history);
        }
    } catch (e) { /* ignore */ }
}

function renderConsolidationLog(history) {
    const log = document.getElementById('consolidation-log');
    log.innerHTML = '';

    history.slice().reverse().forEach(run => {
        // Run header
        const header = document.createElement('div');
        header.className = 'log-entry log-header';
        header.innerHTML = `
            <span class="log-time">${fmtDate(run.started_at)}</span>
            <span class="log-badge ${run.status}">${run.status}</span>
            <span>Before: <strong>${run.memories_before}</strong> → After: <strong>${run.memories_after}</strong></span>
            <span>Clusters: ${run.clusters_found} | Merged: ${run.records_merged} | Decayed: ${run.records_decayed} | Tagged local: ${run.records_tagged_local}</span>
        `;
        log.appendChild(header);

        // Merge details
        if (run.merge_details && run.merge_details.length > 0) {
            run.merge_details.forEach(md => {
                const entry = document.createElement('div');
                entry.className = 'log-entry merge-detail';
                entry.innerHTML = `
                    <div class="merge-header">🔗 Cluster #${md.cluster_id} — ${md.original_ids.length} records merged (similarity: ${(md.similarity * 100).toFixed(1)}%)</div>
                    <div class="merge-originals">
                        ${md.original_texts.map(t => `<div class="merge-text-item">• ${truncate(t, 100)}</div>`).join('')}
                    </div>
                    <div class="merge-result">→ <strong>${truncate(md.merged_text, 100)}</strong></div>
                `;
                log.appendChild(entry);
            });
        }

        // Decay details
        if (run.decay_details && run.decay_details.length > 0) {
            run.decay_details.forEach(dd => {
                const entry = document.createElement('div');
                entry.className = 'log-entry decay-detail';
                entry.innerHTML = `
                    <span>📉 ${dd.action === 'dropped' ? '❌ Dropped' : '↘️ Decayed'}: "${truncate(dd.text_preview, 70)}"</span>
                    <span class="decay-scores">${dd.old_decay_score.toFixed(3)} → ${dd.new_decay_score.toFixed(3)}</span>
                `;
                log.appendChild(entry);
            });
        }
    });

    if (history.length === 0) {
        log.innerHTML = '<div style="color:var(--text-muted);padding:20px;text-align:center">No consolidation runs yet. Click "Run Sleep Cycle" to start.</div>';
    }
}

document.getElementById('btn-run-sleep').addEventListener('click', async () => {
    const btn = document.getElementById('btn-run-sleep');
    const prog = document.getElementById('consolidation-progress');
    const fill = prog.querySelector('.progress-bar-fill');

    btn.disabled = true;
    btn.textContent = 'Running…';
    prog.style.display = 'block';
    fill.style.width = '10%';

    // Animate progress bar while waiting for the API
    let pct = 10;
    const progressInterval = setInterval(() => {
        pct = Math.min(pct + Math.random() * 15, 90);
        fill.style.width = `${pct}%`;
    }, 300);

    try {
        const result = await api('/api/consolidate', { method: 'POST' });

        clearInterval(progressInterval);
        fill.style.width = '100%';

        setTimeout(() => {
            prog.style.display = 'none';
            btn.disabled = false;
            btn.textContent = 'Run Sleep Cycle';

            // Update stats
            document.getElementById('cons-last-run').textContent = 'Just now';
            document.getElementById('cons-merged').textContent = result.records_merged || 0;
            document.getElementById('cons-decayed').textContent = result.records_decayed || 0;

            // Show merge details
            renderConsolidationLog([result]);

            // Refresh overview
            refreshOverview();

            const dropped = result.memories_before - result.memories_after;
            showToast(
                `Sleep cycle complete: ${result.memories_before} → ${result.memories_after} memories (${dropped > 0 ? '-' + dropped : 'no change'})`,
                'success'
            );
        }, 600);
    } catch (e) {
        clearInterval(progressInterval);
        prog.style.display = 'none';
        btn.disabled = false;
        btn.textContent = 'Run Sleep Cycle';
        showToast('Consolidation failed: ' + e.message, 'error');
    }
});


// ═══════════════════════════════════════════════════════════════════════
//  SYNC PANEL VIEW & FIBER-OPTIC BRIDGE
// ═══════════════════════════════════════════════════════════════════════
function emitSyncPacket(type = 'batch', text = '') {
    const layer = document.getElementById('fiber-packets-layer');
    if (!layer) return;
    const packet = document.createElement('div');
    packet.className = `fiber-packet ${type}`;
    packet.innerHTML = type === 'anomaly'
        ? `⚡ Fast-Path: ${text || 'Novel Anomaly'}`
        : `📦 Vector Batch: ${text || '384-d embed'}`;

    const offsetY = (Math.random() - 0.5) * 20;
    packet.style.top = `calc(50% + ${offsetY}px)`;

    layer.appendChild(packet);
    setTimeout(() => {
        if (packet.parentNode) packet.remove();
    }, 2100);
}

async function refreshSyncPanel() {
    try {
        const status = await api('/api/sync/status');
        const pendingEl = document.getElementById('sync-pending-count');
        if (pendingEl) pendingEl.textContent = status.pending_count;
        
        const lastTimeEl = document.getElementById('sync-last-time');
        if (lastTimeEl) {
            lastTimeEl.textContent = status.last_sync
                ? timeAgo(status.last_sync.completed_at)
                : 'Never';
        }

        const toggleBtn = document.getElementById('btn-toggle-connection');
        if (toggleBtn) {
            toggleBtn.textContent = status.is_online ? 'Go Offline' : 'Go Online';
            toggleBtn.className = status.is_online ? 'btn' : 'btn text-error';
        }

        // Update Fiber Bridge Visual Elements
        const breakOverlay = document.getElementById('fiber-break-overlay');
        const fiberDot = document.getElementById('fiber-dot');
        const fiberText = document.getElementById('fiber-status-text');
        const edgeStatus = document.getElementById('sync-edge-status');
        const cloudStatus = document.getElementById('sync-cloud-status');
        const edgeVv = document.getElementById('sync-edge-vv');

        if (status.is_online) {
            if (breakOverlay) breakOverlay.style.display = 'none';
            if (fiberDot) {
                fiberDot.className = 'fiber-pulse-dot online';
            }
            if (fiberText) fiberText.textContent = '⚡ FIBER PIPELINE: ONLINE & SYNCHRONIZED';
            if (cloudStatus) {
                cloudStatus.textContent = 'ONLINE (gRPC 6334)';
                cloudStatus.className = 'text-success';
            }
        } else {
            if (breakOverlay) breakOverlay.style.display = 'flex';
            if (fiberDot) {
                fiberDot.className = 'fiber-pulse-dot offline';
            }
            if (fiberText) fiberText.textContent = '⚠️ FIBER PIPELINE: AIR-GAPPED (OFFLINE BUFFER ACTIVE)';
            if (cloudStatus) {
                cloudStatus.textContent = 'DISCONNECTED (Air-Gapped)';
                cloudStatus.className = 'text-error';
            }
        }

        if (edgeStatus) edgeStatus.textContent = 'ACTIVE (384-d)';
        if (edgeVv) edgeVv.textContent = `{"node-01": ${Math.max(12, status.pending_count + 12)}, "cloud": 4}`;

        // Render Priority Fast-Path Queue & Unsynced Memories
        try {
            const memories = await api('/api/memories?limit=100');
            const priorityEl = document.getElementById('sync-priority-list');
            if (priorityEl) {
                const priorityItems = memories.filter(m => m.priority === 'high' || m.priority_reason === 'anomaly' || !m.synced);
                if (priorityItems.length === 0) {
                    priorityEl.innerHTML = '<div style="color:var(--text-muted);padding:24px;text-align:center">No pending items in sync queue. All edge records synced.</div>';
                } else {
                    priorityEl.innerHTML = priorityItems.slice(0, 10).map(m => `
                        <div class="sim-lane-card ${m.priority === 'high' || m.priority_reason === 'anomaly' ? 'fast' : 'batch'}" style="margin-bottom:8px">
                            <div class="sim-lane-header">
                                ${syncBadge(m)}
                                <span class="sim-lane-time">${timeAgo(m.created_at)}</span>
                            </div>
                            <div class="sim-lane-text" style="font-weight:600">${m.text}</div>
                            <div class="sim-lane-sub">
                                ${m.nearest_similarity !== null && m.nearest_similarity !== undefined
                                    ? `Nearest Neighbor Sim: ${(m.nearest_similarity * 100).toFixed(0)}% ${m.nearest_similarity < 0.70 ? '(< 70% threshold — Fast-Path)' : ''}`
                                    : 'Routine Local Memory'} &bull; Source: ${m.source}
                            </div>
                        </div>
                    `).join('');
                }
            }
        } catch (memErr) {
            console.warn('Failed to load priority items for sync panel:', memErr);
        }

        // Render sync history
        const history = await api('/api/sync/history');
        const logEl = document.getElementById('sync-log');
        if (logEl) {
            logEl.innerHTML = '';

            if (history.length === 0) {
                logEl.innerHTML = '<div style="color:var(--text-muted);padding:20px;text-align:center">No sync events yet.</div>';
                return;
            }

            history.slice().reverse().forEach(s => {
                const entry = document.createElement('div');
                entry.className = 'log-entry';
                entry.innerHTML = `
                    <span class="log-time">${fmtDate(s.started_at)}</span>
                    <span class="log-badge ${s.status}">${s.status}</span>
                    <span>Pushed: ${s.records_pushed} | Pulled: ${s.records_pulled} | Conflicts: ${s.conflicts_resolved}</span>
                    ${s.errors.length > 0 ? `<span class="text-error" style="margin-left:8px">${s.errors.join('; ')}</span>` : ''}
                `;
                logEl.appendChild(entry);
            });
        }
    } catch (e) {
        console.warn('Sync panel refresh failed:', e);
    }
}

document.getElementById('btn-toggle-connection').addEventListener('click', async (e) => {
    try {
        const res = await api('/api/connectivity/toggle', { method: 'POST' });
        e.target.textContent = res.is_online ? 'Go Offline' : 'Go Online';
        e.target.className = res.is_online ? 'btn' : 'btn text-error';
        refreshStatus();
        refreshSyncPanel();
        showToast(`Connectivity: ${res.is_online ? 'Online' : 'Offline'}`, res.is_online ? 'success' : 'warning');
    } catch (err) {
        showToast('Failed to toggle connectivity', 'error');
    }
});

document.getElementById('btn-trigger-sync').addEventListener('click', async (e) => {
    const btn = e.target;
    btn.disabled = true;
    btn.textContent = 'Syncing…';

    // Fire continuous visual data packets across fiber pipe during sync
    emitSyncPacket('batch', 'Payload Push (384-d)');
    setTimeout(() => emitSyncPacket('batch', 'Payload Push (384-d)'), 300);
    setTimeout(() => emitSyncPacket('batch', 'Delta Ingestion'), 600);

    try {
        const result = await api('/api/sync', { method: 'POST' });
        const lastEl = document.getElementById('sync-last-time');
        if (lastEl) lastEl.textContent = 'Just now';
        refreshSyncPanel();
        refreshOverview();
        showToast(
            result.status === 'completed'
                ? `Sync complete: pushed ${result.records_pushed}, pulled ${result.records_pulled}, conflicts ${result.conflicts_resolved}`
                : `Sync failed: ${result.errors.join('; ')}`,
            result.status === 'completed' ? 'success' : 'error'
        );
    } catch (err) {
        showToast('Sync failed: ' + err.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Trigger Sync';
    }
});


// ═══════════════════════════════════════════════════════════════════════
//  ACTIVITY LOG VIEW
// ═══════════════════════════════════════════════════════════════════════
async function refreshActivity() {
    const feed = document.getElementById('activity-feed');
    try {
        const entries = await api('/api/activity?limit=100');
        feed.innerHTML = '';

        if (entries.length === 0) {
            feed.innerHTML = '<div style="color:var(--text-muted);padding:40px;text-align:center">No activity yet. Seed data or add memories to get started.</div>';
            return;
        }

        entries.forEach(a => {
            const item = document.createElement('div');
            item.className = 'activity-item';
            item.innerHTML = `
                <div class="act-icon">${a.icon || '●'}</div>
                <div class="act-content">
                    <div class="act-header">
                        <span class="act-title">${a.title}</span>
                        <span class="act-time">${timeAgo(a.timestamp)}</span>
                    </div>
                    <div class="act-desc">${a.description}</div>
                    ${a.details && Object.keys(a.details).length > 0
                        ? `<details class="act-details"><summary>Details</summary><pre>${JSON.stringify(a.details, null, 2)}</pre></details>`
                        : ''
                    }
                </div>
            `;
            feed.appendChild(item);
        });
    } catch (e) {
        feed.innerHTML = '<div style="color:var(--accent-error);padding:20px">Failed to load activity log</div>';
    }
}

const btnRefAct = document.getElementById('btn-refresh-activity');
if (btnRefAct) {
    btnRefAct.addEventListener('click', () => {
        refreshActivity();
        showToast('Activity log refreshed', 'info');
    });
}


// ═══════════════════════════════════════════════════════════════════════
//  ADD MEMORY FORM
// ═══════════════════════════════════════════════════════════════════════
document.getElementById('mem-importance').addEventListener('input', (e) => {
    document.getElementById('importance-val').textContent = e.target.value;
});

document.getElementById('add-memory-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = e.target.querySelector('button[type="submit"]');
    btn.disabled = true;
    btn.textContent = 'Adding…';

    const body = {
        text: document.getElementById('mem-text').value,
        source: document.getElementById('mem-source').value,
        category: document.getElementById('mem-category').value,
        tags: document.getElementById('mem-tags').value.split(',').map(s => s.trim()).filter(Boolean),
        importance: parseInt(document.getElementById('mem-importance').value, 10) / 100,
    };

    try {
        await api('/api/memories', { method: 'POST', body: JSON.stringify(body) });
        showToast('Memory added successfully', 'success');
        e.target.reset();
        document.getElementById('importance-val').textContent = '70';
        document.getElementById('mem-importance').value = 70;
        refreshOverview();
    } catch (err) {
        showToast('Failed to add memory: ' + err.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Add Memory';
    }
});


// ═══════════════════════════════════════════════════════════════════════
//  SEED DATA & RESET BUTTONS
// ═══════════════════════════════════════════════════════════════════════
const btnSeed = document.getElementById('btn-seed-data');
if (btnSeed) {
    btnSeed.addEventListener('click', async (e) => {
        const btn = e.target;
        btn.disabled = true;
        btn.textContent = 'Loading…';
        try {
            const res = await api('/api/seed', { method: 'POST' });
            showToast(res.message || `Seeded ${res.total_records} records`, 'success');
            refreshOverview();
            refreshActivity();
        } catch (err) {
            showToast('Seed failed: ' + err.message, 'error');
        } finally {
            btn.disabled = false;
            btn.textContent = '📦 Seed Sample Data (32 Records)';
        }
    });
}

const btnReset = document.getElementById('btn-reset-data');
if (btnReset) {
    btnReset.addEventListener('click', async (e) => {
        if (!confirm('Are you sure you want to reset all memories on Edge Qdrant to clean factory baseline?')) return;
        const btn = e.target;
        btn.disabled = true;
        btn.textContent = 'Resetting…';
        try {
            await api('/api/reset', { method: 'POST' });
            showToast('Platform reset complete — vector memory cleared.', 'info');
            refreshOverview();
            refreshActivity();
            refreshExplorer();
            refreshSyncPanel();
        } catch (err) {
            showToast('Reset failed: ' + err.message, 'error');
        } finally {
            btn.disabled = false;
            btn.textContent = '🔄 Reset Platform';
        }
    });
}

// Anomaly demo triggers
async function handleAnomalyInject(btn) {
    if (!btn) return;
    const origText = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Injecting…';
    try {
        const res = await api('/api/demo/inject-anomaly', { method: 'POST' });
        showToast(res.message, 'warning');

        // If on Sync view, show active feedback alert card
        emitSyncPacket('anomaly', res.memory ? res.memory.text.slice(0, 20) : 'Hazard');
        const alertBox = document.getElementById('sync-anomaly-alert-box');
        if (alertBox) {
            const simPct = res.nearest_similarity !== null && res.nearest_similarity !== undefined
                ? `${(res.nearest_similarity * 100).toFixed(0)}%`
                : '< 70%';
            alertBox.innerHTML = `
                <div class="card" style="border: 1.5px solid var(--coral-local); background: rgba(232, 93, 74, 0.06); padding: 18px 20px; box-shadow: 0 4px 12px rgba(232, 93, 74, 0.12);">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                        <span class="badge anomaly">⚡ NOVEL ANOMALY DETECTED & STORED IN QDRANT</span>
                        <span style="font-family:var(--font-mono); font-size:0.8rem; color:var(--coral-local); font-weight:700;">Nearest Cosine Sim: ${simPct} &lt; 70%</span>
                    </div>
                    <div style="font-weight:600; font-size:0.95rem; color:var(--navy-primary); margin-bottom:6px;">${res.memory ? res.memory.text : 'Novel out-of-distribution event'}</div>
                    <div style="font-size:0.8rem; color:var(--text-muted); display:flex; justify-content:space-between; align-items:center;">
                        <span>${res.fast_path_pushed ? '✅ Pushed directly to Cloud Qdrant via Priority Fast-Path.' : '⏳ Queued in local priority fast-path queue (Cloud Unreachable / Offline mode).'}</span>
                        <span style="font-family:var(--font-mono); font-size:0.75rem; color:var(--signal-teal);">ID: ${res.memory ? res.memory.id.slice(0, 8) : '—'}</span>
                    </div>
                </div>
            `;
            alertBox.style.display = 'block';
        }

        refreshOverview();
        refreshSyncPanel();
        refreshView(currentView);
    } catch (err) {
        showToast('Anomaly injection failed: ' + err.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = origText;
    }
}

['btn-inject-anomaly-overview', 'btn-inject-anomaly-sync', 'btn-inject-anomaly-activity'].forEach(id => {
    const el = document.getElementById(id);
    if (el) {
        el.addEventListener('click', () => handleAnomalyInject(el));
    }
});


// ═══════════════════════════════════════════════════════════════════════
//  INITIALISATION
// ═══════════════════════════════════════════════════════════════════════
async function init() {
    await refreshStatus();
    await refreshOverview();

    // Auto-refresh status every 5s
    setInterval(refreshStatus, 5000);
    // Auto-refresh current view every 10s
    setInterval(() => refreshView(currentView), 10000);
}

document.addEventListener('DOMContentLoaded', init);
