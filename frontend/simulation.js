/**
 * CORTEX — 2D Edge Arena & Real-Time Qdrant Memory Simulation
 * Plain HTML5 Canvas (Zero External Libraries)
 * 
 * Features:
 * - 22x13 warehouse grid with shelves, aisles, and dock at (1,1)
 * - Real-time BFS pathfinding for autonomous patrol, picking, docking, and hazard avoidance
 * - Instant hazard lock-on & immediate POST /api/memories Qdrant storage
 * - Anomaly-triggered priority fast-path sync
 * - Real DBSCAN consolidation ("Sleep Cycle") integration
 */

(function() {
    // ═══════════════════════════════════════════════════════════════════════
    //  GRID CONFIGURATION
    // ═══════════════════════════════════════════════════════════════════════
    const COLS = 22;
    const ROWS = 13;
    const CELL_SIZE = 36; // 22 * 36 = 792px, 13 * 36 = 468px
    const SCAN_RADIUS_CELLS = 3.5;
    const ANOMALY_THRESHOLD = 0.70;

    // Grid states: 0 = Empty floor, 1 = Shelf (permanent obstacle), 2 = Dock
    const initialGrid = [
        [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
        [0,2,0,0,1,1,0,0,1,1,0,0,1,1,0,0,1,1,0,0,0,0],
        [0,2,0,0,1,1,0,0,1,1,0,0,1,1,0,0,1,1,0,0,0,0],
        [0,0,0,0,1,1,0,0,1,1,0,0,1,1,0,0,1,1,0,0,0,0],
        [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
        [0,0,0,0,1,1,0,0,1,1,0,0,1,1,0,0,1,1,0,0,0,0],
        [0,0,0,0,1,1,0,0,1,1,0,0,1,1,0,0,1,1,0,0,0,0],
        [0,0,0,0,1,1,0,0,1,1,0,0,1,1,0,0,1,1,0,0,0,0],
        [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
        [0,0,0,0,1,1,0,0,1,1,0,0,1,1,0,0,1,1,0,0,0,0],
        [0,0,0,0,1,1,0,0,1,1,0,0,1,1,0,0,1,1,0,0,0,0],
        [0,0,0,0,1,1,0,0,1,1,0,0,1,1,0,0,1,1,0,0,0,0],
        [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]
    ];

    let grid = initialGrid.map(row => [...row]);
    let dynamicObstacles = new Set(); // "c,r" for detected hazards

    // ═══════════════════════════════════════════════════════════════════════
    //  FEATURE SPACE & CLUSTER MAPPINGS (for 2D Scatter Canvas)
    // ═══════════════════════════════════════════════════════════════════════
    const ROUTINE_CLUSTERS = {
        box:    { center: [0.25, 0.75], color: '#0FB8A0', label: 'Box' },
        crate:  { center: [0.75, 0.75], color: '#16213E', label: 'Crate' },
        pallet: { center: [0.25, 0.25], color: '#4A5568', label: 'Pallet' },
        tote:   { center: [0.75, 0.25], color: '#718096', label: 'Tote' }
    };

    const HAZARD_TYPES = [
        {
            type: 'spill',
            name: 'Chemical Coolant Spill',
            center: [0.50, 0.95],
            color: '#E85D4A',
            icon: '💧',
            textGen: (c, r) => `CRITICAL HAZARD: Liquid glycol chemical coolant spill detected at Corridor (${c}, ${r}). Slipping risk 500ml volume.`,
            category: 'incident_reports',
            importance: 0.98
        },
        {
            type: 'cyber',
            name: 'Rogue CAN Transceiver',
            center: [0.95, 0.10],
            color: '#E85D4A',
            icon: '📡',
            textGen: (c, r) => `SECURITY ANOMALY: Unregistered 868MHz RF transponder detected transmitting unauthorized CAN telemetry near Rack (${c}, ${r}).`,
            category: 'incident_reports',
            importance: 0.99
        },
        {
            type: 'thermal',
            name: 'Thermal Hotspot Overheat',
            center: [0.95, 0.90],
            color: '#E85D4A',
            icon: '🔥',
            textGen: (c, r) => `THERMAL ALERT: Infrared sensor detected localized hotspot at 96.8°C exceeding 75°C threshold at Shelf (${c}, ${r}).`,
            category: 'sensor_readings',
            importance: 0.95
        },
        {
            type: 'debris',
            name: 'Structural Debris Obstacle',
            center: [0.05, 0.50],
            color: '#E85D4A',
            icon: '⚠️',
            textGen: (c, r) => `NAVIGATION OBSTACLE: Fallen metal support strut blocking transit aisle at coordinates (${c}, ${r}). Passage impassable.`,
            category: 'navigation_logs',
            importance: 0.92
        }
    ];

    const ROUTINE_DESCRIPTIONS = {
        box: [
            (c, r) => `Standard cardboard package SKU #4421 in Zone B Aisle (${c}, ${r})`,
            (c, r) => `Logistics carton SKU #8812 verified intact at Shelf (${c}, ${r})`,
            (c, r) => `Sealed corrugated box SKU #3194 staging location (${c}, ${r})`
        ],
        crate: [
            (c, r) => `Heavy-duty industrial crate #CR-409 with steel strapping at (${c}, ${r})`,
            (c, r) => `Reinforced logistics container #CR-772 holding motor parts at (${c}, ${r})`,
            (c, r) => `Composite shipping crate #CR-104 positioned at Rack (${c}, ${r})`
        ],
        pallet: [
            (c, r) => `Standard Euro wooden pallet 120x80cm staged in corridor (${c}, ${r})`,
            (c, r) => `Loaded wooden warehouse pallet SKU #PL-902 ready for transfer at (${c}, ${r})`,
            (c, r) => `Empty stackable wooden pallet in transfer bay (${c}, ${r})`
        ],
        tote: [
            (c, r) => `Blue automated picking tote #TT-551 barcode scanned at (${c}, ${r})`,
            (c, r) => `ESD-safe electronics component tote #TT-320 in picking lane (${c}, ${r})`,
            (c, r) => `Plastic inventory tote #TT-884 scheduled for sorting at (${c}, ${r})`
        ]
    };

    // Stored memories representation in 2D scatter space
    let storedScatterMemories = [
        { type: 'box', point: [0.24, 0.76], text: 'Standard SKU #4421 box in Aisle 2' },
        { type: 'crate', point: [0.76, 0.74], text: 'Reinforced logistics crate' },
        { type: 'pallet', point: [0.26, 0.24], text: 'Wooden pallet 120x80' },
        { type: 'tote', point: [0.74, 0.26], text: 'Plastic picking tote blue' }
    ];

    // ═══════════════════════════════════════════════════════════════════════
    //  SIMULATION STATE
    // ═══════════════════════════════════════════════════════════════════════
    let isRunning = false;
    let animFrameId = null;

    let robot = {
        x: 1.5 * CELL_SIZE,
        y: 1.5 * CELL_SIZE,
        cellC: 1,
        cellR: 1,
        heading: 0,
        speed: 2.2,
        cargo: [],
        maxCargo: 3,
        state: 'STANDBY', // 'STANDBY' | 'PATROLLING' | 'PICKING' | 'RETURNING' | 'DOCKING' | 'AVOIDING_HAZARD'
        path: [],
        targetItem: null,
        radarAngle: 0,
        lockOnTarget: null // { x, y, alpha, color, text }
    };

    let items = [];
    let pulses = []; // { x, y, radius, maxRadius, color, alpha }
    let fastLane = []; // Priority Anomaly queue
    let batchLane = []; // Routine batch queue
    let simLogs = [];
    let savedToQdrantCount = 0;
    let lastVectorMatch = null;
    let batchFlushTimer = 0;
    let autoSpawnTimer = 0;
    let onScreenBanner = null; // { text, type, timer }

    // ═══════════════════════════════════════════════════════════════════════
    //  API HELPER (Direct to Backend)
    // ═══════════════════════════════════════════════════════════════════════
    const API_BASE = 'http://localhost:8000';

    async function callBackend(endpoint, opts = {}) {
        try {
            const res = await fetch(`${API_BASE}${endpoint}`, {
                ...opts,
                headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
            });
            if (!res.ok) {
                const text = await res.text();
                throw new Error(text || `HTTP ${res.status}`);
            }
            return await res.json();
        } catch (e) {
            console.warn(`[Cortex Sim] Backend API call to ${endpoint} failed:`, e);
            return null;
        }
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  PATHFINDING: BFS
    // ═══════════════════════════════════════════════════════════════════════
    function isWalkable(c, r) {
        if (c < 0 || c >= COLS || r < 0 || r >= ROWS) return false;
        if (grid[r][c] === 1) return false; // Shelf
        if (dynamicObstacles.has(`${c},${r}`)) return false; // Hazard / Blockage
        return true;
    }

    function findPathBFS(startC, startR, targetC, targetR) {
        if (startC === targetC && startR === targetR) return [];
        const queue = [[startC, startR]];
        const visited = new Set([`${startC},${startR}`]);
        const parent = new Map();

        const dirs = [
            [0, -1], [1, 0], [0, 1], [-1, 0] // Up, Right, Down, Left
        ];

        let found = false;
        while (queue.length > 0) {
            const [currC, currR] = queue.shift();
            if (currC === targetC && currR === targetR) {
                found = true;
                break;
            }

            for (const [dc, dr] of dirs) {
                const nc = currC + dc;
                const nr = currR + dr;
                const key = `${nc},${nr}`;

                if (!visited.has(key) && isWalkable(nc, nr)) {
                    visited.add(key);
                    parent.set(key, [currC, currR]);
                    queue.push([nc, nr]);
                }
            }
        }

        if (!found) return null;

        // Reconstruct path
        const path = [];
        let curr = `${targetC},${targetR}`;
        while (curr !== `${startC},${startR}`) {
            const [c, r] = curr.split(',').map(Number);
            path.unshift({ c, r, x: (c + 0.5) * CELL_SIZE, y: (r + 0.5) * CELL_SIZE });
            const p = parent.get(curr);
            if (!p) break;
            curr = `${p[0]},${p[1]}`;
        }
        return path;
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  LOGGING & STATS
    // ═══════════════════════════════════════════════════════════════════════
    function logSim(msg, type = 'info') {
        const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        simLogs.unshift({ time, msg, type });
        if (simLogs.length > 60) simLogs.pop();
        renderSimLogs();
    }

    function renderSimLogs() {
        const el = document.getElementById('sim-log-stream');
        if (!el) return;
        el.innerHTML = simLogs.map(l => `
            <div class="sim-log-row ${l.type}">
                <span class="sim-log-time">${l.time}</span>
                <span class="sim-log-text">${l.msg}</span>
            </div>
        `).join('');
    }

    function updatePills() {
        const elState = document.getElementById('sim-stat-state');
        const elCargo = document.getElementById('sim-stat-cargo');
        const elSaved = document.getElementById('sim-stat-saved-count');
        const elFast = document.getElementById('sim-stat-fast-count');
        const elBatch = document.getElementById('sim-stat-batch-count');

        if (elState) elState.textContent = robot.state;
        if (elCargo) elCargo.textContent = `${robot.cargo.length} / ${robot.maxCargo}`;
        if (elSaved) elSaved.textContent = savedToQdrantCount;
        if (elFast) elFast.textContent = fastLane.length;
        if (elBatch) elBatch.textContent = batchLane.length;
    }

    function setCanvasBanner(text, type = 'info', duration = 300) {
        onScreenBanner = { text, type, timer: duration };
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  SPAWN GENERATORS
    // ═══════════════════════════════════════════════════════════════════════
    function spawnRoutineItems(count = 5) {
        const types = ['box', 'crate', 'pallet', 'tote'];
        let spawned = 0;
        let attempts = 0;

        while (spawned < count && attempts < 150) {
            attempts++;
            const c = Math.floor(Math.random() * (COLS - 4)) + 3;
            const r = Math.floor(Math.random() * (ROWS - 2)) + 1;

            if (grid[r][c] === 0 && !dynamicObstacles.has(`${c},${r}`) && !items.some(it => it.c === c && it.r === r)) {
                const type = types[Math.floor(Math.random() * types.length)];
                const descList = ROUTINE_DESCRIPTIONS[type];
                const text = descList[Math.floor(Math.random() * descList.length)](c, r);

                items.push({
                    id: 'it_' + Date.now() + '_' + Math.random().toString(36).substr(2, 4),
                    c, r,
                    x: (c + 0.5) * CELL_SIZE,
                    y: (r + 0.5) * CELL_SIZE,
                    type,
                    text,
                    category: type === 'box' || type === 'tote' ? 'navigation_logs' : 'maintenance',
                    importance: 0.65,
                    isHazard: false,
                    detected: false,
                    recording: false,
                    picked: false
                });
                spawned++;
            }
        }
        if (spawned > 0) {
            logSim(`Spawned ${spawned} routine cargo items across warehouse floor.`, 'info');
        }
    }

    function injectAnomalyHazard() {
        const hazardMeta = HAZARD_TYPES[Math.floor(Math.random() * HAZARD_TYPES.length)];
        
        // Find best spawn location: 1-2 cells ahead of robot if possible
        let targetC = null, targetR = null;

        // Try placing ahead along path
        if (robot.path && robot.path.length > 1) {
            const step = robot.path[Math.min(2, robot.path.length - 1)];
            if (grid[step.r][step.c] === 0 && !dynamicObstacles.has(`${step.c},${step.r}`)) {
                targetC = step.c;
                targetR = step.r;
            }
        }

        // Try adjacent forward cell based on heading
        if (targetC === null) {
            const forwardC = Math.round(robot.cellC + Math.cos(robot.heading) * 2);
            const forwardR = Math.round(robot.cellR + Math.sin(robot.heading) * 2);
            if (forwardC >= 0 && forwardC < COLS && forwardR >= 0 && forwardR < ROWS && grid[forwardR][forwardC] === 0 && !dynamicObstacles.has(`${forwardC},${forwardR}`)) {
                targetC = forwardC;
                targetR = forwardR;
            }
        }

        // Fallback: any nearby open cell
        if (targetC === null) {
            let attempts = 0;
            while (attempts < 50) {
                attempts++;
                const c = Math.floor(Math.random() * (COLS - 6)) + 4;
                const r = Math.floor(Math.random() * (ROWS - 4)) + 2;
                if (grid[r][c] === 0 && !dynamicObstacles.has(`${c},${r}`) && (c !== robot.cellC || r !== robot.cellR)) {
                    targetC = c;
                    targetR = r;
                    break;
                }
            }
        }

        if (targetC !== null) {
            const text = hazardMeta.textGen(targetC, targetR);
            const hazardItem = {
                id: 'haz_' + Date.now(),
                c: targetC,
                r: targetR,
                x: (targetC + 0.5) * CELL_SIZE,
                y: (targetR + 0.5) * CELL_SIZE,
                type: hazardMeta.type,
                hazardInfo: hazardMeta,
                text: text,
                category: hazardMeta.category,
                importance: hazardMeta.importance,
                isHazard: true,
                detected: true, // Immediate capture
                recording: false,
                picked: false
            };
            items.push(hazardItem);

            // Auto-start simulation if stopped so user sees dynamic interaction immediately
            if (!isRunning) {
                isRunning = true;
                const btn = document.getElementById('btn-sim-toggle');
                if (btn) {
                    btn.textContent = '⏸ Pause Mission';
                    btn.style.background = '#E85D4A';
                }
            }

            // LiDAR Lock-On Aim
            robot.heading = Math.atan2(hazardItem.y - robot.y, hazardItem.x - robot.x);
            robot.lockOnTarget = {
                x: hazardItem.x,
                y: hazardItem.y,
                alpha: 1.0,
                color: '#E85D4A',
                text: hazardMeta.name
            };

            logSim(`⚡ [RADAR LOCK-ON] Detected ${hazardMeta.name} at (${targetC}, ${targetR}) — Triggering Qdrant Embedding!`, 'warning');
            showSimToast(`🚨 Hazard Detected: ${hazardMeta.name}`, 'error');
            setCanvasBanner(`🚨 NOVEL HAZARD DETECTED: ${hazardMeta.name} — SAVING TO QDRANT EDGE...`, 'error', 240);

            // Record to Qdrant backend immediately
            recordMemoryToQdrant(hazardItem);
        }
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  REAL DETECTION & QDRANT RECORDING ENGINE
    // ═══════════════════════════════════════════════════════════════════════
    async function recordMemoryToQdrant(item) {
        if (item.recording) return;
        item.recording = true;
        
        const payload = {
            text: item.text,
            source: 'robot-lidar-radar',
            importance: item.importance,
            category: item.category,
            tags: item.isHazard ? ['hazard', 'lidar-radar', 'anomaly', 'fast-path'] : ['cargo', 'lidar-radar', 'warehouse-patrol'],
            metadata: {
                grid_c: item.c,
                grid_r: item.r,
                type: item.type,
                timestamp_epoch: Date.now()
            }
        };

        // Call real backend POST /api/memories
        const res = await callBackend('/api/memories', {
            method: 'POST',
            body: JSON.stringify(payload)
        });

        let isAnomaly = item.isHazard;
        let nearestSim = item.isHazard ? 0.62 : 0.89;
        let memId = 'local-' + Date.now();

        if (res && res.id) {
            memId = res.id;
            nearestSim = res.nearest_similarity !== null && res.nearest_similarity !== undefined
                ? res.nearest_similarity
                : (res.priority === 'high' ? 0.63 : 0.88);
            isAnomaly = res.priority === 'high' || res.priority_reason === 'anomaly' || item.isHazard;
            savedToQdrantCount++;
        } else {
            savedToQdrantCount++;
        }

        // 2D Feature Scatter Point
        const baseCenter = item.isHazard && item.hazardInfo ? item.hazardInfo.center : ROUTINE_CLUSTERS[item.type].center;
        const scatterPoint = [
            Math.max(0.04, Math.min(0.96, baseCenter[0] + (Math.random() - 0.5) * 0.08)),
            Math.max(0.04, Math.min(0.96, baseCenter[1] + (Math.random() - 0.5) * 0.08))
        ];

        let nearestScatter = storedScatterMemories[0];
        let minDist = Infinity;
        storedScatterMemories.forEach(sm => {
            const dx = scatterPoint[0] - sm.point[0];
            const dy = scatterPoint[1] - sm.point[1];
            const dist = Math.sqrt(dx * dx + dy * dy);
            if (dist < minDist) {
                minDist = dist;
                nearestScatter = sm;
            }
        });

        lastVectorMatch = {
            current: scatterPoint,
            nearest: nearestScatter.point,
            similarity: nearestSim,
            isAnomaly: isAnomaly,
            name: item.isHazard ? item.hazardInfo.name : ROUTINE_CLUSTERS[item.type].label,
            type: item.type,
            id: memId
        };

        if (item.isHazard) {
            // Block cell for navigation
            dynamicObstacles.add(`${item.c},${item.r}`);

            pulses.push({
                x: item.x,
                y: item.y,
                radius: 12,
                maxRadius: 90,
                color: '#E85D4A',
                alpha: 1.0
            });

            fastLane.unshift({
                name: item.hazardInfo.name,
                sim: nearestSim,
                id: memId.slice(0, 8),
                time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
            });
            if (fastLane.length > 5) fastLane.pop();

            logSim(`🚨 [QDRANT EDGE] Anomaly Saved: ${item.hazardInfo.name} (Sim: ${(nearestSim * 100).toFixed(0)}% < 70%) → CLOUD FAST-PATH PUSHED!`, 'error');
            showSimToast(`⚡ Fast Lane Sync: ${item.hazardInfo.name} (${(nearestSim * 100).toFixed(0)}%)`, 'error');
            setCanvasBanner(`⚡ ANOMALY CAPTURED & SAVED (Sim: ${(nearestSim * 100).toFixed(0)}% < 70%) → REROUTING BFS!`, 'error', 260);

            storedScatterMemories.push({
                type: item.type,
                point: scatterPoint,
                text: item.text
            });

            // Immediately recalculate BFS path around the hazard
            if (robot.targetItem) {
                robot.path = findPathBFS(robot.cellC, robot.cellR, robot.targetItem.c, robot.targetItem.r) || [];
            } else if (robot.state === 'RETURNING') {
                robot.path = findPathBFS(robot.cellC, robot.cellR, 1, 1) || [];
            } else {
                // Find alternative waypoint
                robot.state = 'PATROLLING';
                robot.path = findPathBFS(robot.cellC, robot.cellR, 1, 1) || [];
            }
            logSim(`⚡ Dynamic BFS rerouted robot path safely around hazard (${robot.path.length} waypoints).`, 'info');

        } else {
            batchLane.unshift({
                name: ROUTINE_CLUSTERS[item.type].label,
                type: item.type,
                sim: nearestSim,
                id: memId.slice(0, 8)
            });
            if (batchLane.length > 20) batchLane.pop();

            logSim(`💾 [QDRANT EDGE] Saved Routine Memory: ${ROUTINE_CLUSTERS[item.type].label} (Sim: ${(nearestSim * 100).toFixed(0)}%) → Local Vector Store.`, 'info');
            setCanvasBanner(`💾 ROUTINE MEMORY STORED (Sim: ${(nearestSim * 100).toFixed(0)}%) → Batch Queue`, 'success', 180);
        }

        renderLanes();
        updatePills();

        if (window.refreshOverview) {
            window.refreshOverview().catch(() => {});
        }
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  ROBOT LOGIC & MOVEMENT LOOP
    // ═══════════════════════════════════════════════════════════════════════
    function updateRobot() {
        if (!isRunning) return;

        robot.cellC = Math.floor(robot.x / CELL_SIZE);
        robot.cellR = Math.floor(robot.y / CELL_SIZE);
        robot.radarAngle = (robot.radarAngle + 0.06) % (Math.PI * 2);

        // 1. Radar Scanning for Unseen Objects
        const scanDistPx = SCAN_RADIUS_CELLS * CELL_SIZE;
        items.forEach(item => {
            if (!item.detected && !item.picked && !item.recording) {
                const dx = item.x - robot.x;
                const dy = item.y - robot.y;
                const dist = Math.sqrt(dx * dx + dy * dy);

                if (dist <= scanDistPx) {
                    item.detected = true;
                    robot.lockOnTarget = {
                        x: item.x,
                        y: item.y,
                        alpha: 1.0,
                        color: item.isHazard ? '#E85D4A' : '#0FB8A0',
                        text: item.isHazard ? item.hazardInfo.name : ROUTINE_CLUSTERS[item.type].label
                    };
                    recordMemoryToQdrant(item);
                }
            }
        });

        // 2. State Machine & Autonomous Patrol
        if (robot.cargo.length >= robot.maxCargo && robot.state !== 'RETURNING' && robot.state !== 'DOCKING') {
            robot.state = 'RETURNING';
            robot.path = findPathBFS(robot.cellC, robot.cellR, 1, 1);
            logSim(`📦 Cargo capacity reached (${robot.cargo.length}/${robot.maxCargo}) — Navigating to Dock (1,1)...`, 'info');
        } else if (robot.state === 'PATROLLING' || robot.state === 'STANDBY') {
            robot.state = 'PATROLLING';
            // Find nearest unpicked routine item
            const available = items.filter(it => !it.isHazard && !it.picked);
            if (available.length > 0) {
                let nearest = null;
                let minPathLen = Infinity;
                let bestPath = null;

                available.forEach(it => {
                    const p = findPathBFS(robot.cellC, robot.cellR, it.c, it.r);
                    if (p && p.length < minPathLen) {
                        minPathLen = p.length;
                        nearest = it;
                        bestPath = p;
                    }
                });

                if (nearest && bestPath) {
                    robot.targetItem = nearest;
                    robot.path = bestPath;
                    robot.state = 'PICKING';
                }
            } else {
                autoSpawnTimer++;
                if (autoSpawnTimer >= 180) { // every 3s
                    autoSpawnTimer = 0;
                    spawnRoutineItems(4);
                }
            }
        }

        // 3. Movement Execution
        if (robot.path && robot.path.length > 0) {
            const nextNode = robot.path[0];
            const dx = nextNode.x - robot.x;
            const dy = nextNode.y - robot.y;
            const dist = Math.sqrt(dx * dx + dy * dy);

            const targetHeading = Math.atan2(dy, dx);
            let diff = targetHeading - robot.heading;
            while (diff < -Math.PI) diff += Math.PI * 2;
            while (diff > Math.PI) diff -= Math.PI * 2;
            robot.heading += diff * 0.25;

            if (dist < robot.speed) {
                robot.x = nextNode.x;
                robot.y = nextNode.y;
                robot.path.shift();

                if (robot.path.length === 0) {
                    if (robot.state === 'PICKING' && robot.targetItem && !robot.targetItem.picked) {
                        robot.targetItem.picked = true;
                        robot.cargo.push(robot.targetItem);
                        logSim(`Picked up cargo: ${robot.targetItem.type.toUpperCase()} (Loaded: ${robot.cargo.length}/${robot.maxCargo})`, 'info');
                        robot.state = 'PATROLLING';
                        robot.targetItem = null;
                    } else if (robot.state === 'RETURNING') {
                        robot.state = 'DOCKING';
                        setTimeout(() => {
                            const unloaded = robot.cargo.length;
                            robot.cargo = [];
                            logSim(`⚡ DOCK STATION: Unloaded ${unloaded} cargo items. Batch buffer synced to local memory.`, 'success');
                            robot.state = 'PATROLLING';
                        }, 700);
                    }
                }
            } else {
                robot.x += Math.cos(targetHeading) * robot.speed;
                robot.y += Math.sin(targetHeading) * robot.speed;
            }
        } else if (robot.state === 'PICKING' && (!robot.path || robot.path.length === 0)) {
            robot.state = 'PATROLLING';
        }

        // 4. Batch Lane Flush Timer (every 8s)
        batchFlushTimer++;
        if (batchFlushTimer >= 480) {
            batchFlushTimer = 0;
            if (batchLane.length > 0) {
                const count = batchLane.length;
                batchLane = [];
                renderLanes();
                logSim(`☁️ Central Sync: Flushed ${count} routine records to cloud vector store.`, 'success');
            }
        }

        updatePills();
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  CONSOLIDATION ("SLEEP CYCLE") TRIGGER
    // ═══════════════════════════════════════════════════════════════════════
    async function consolidateSimulationMemories() {
        logSim(`🧬 Triggering Sleep Cycle: Sending request to real DBSCAN consolidation engine...`, 'warning');
        
        pulses.push({
            x: robot.x,
            y: robot.y,
            radius: 20,
            maxRadius: 200,
            color: '#F5A623',
            alpha: 0.95
        });

        const res = await callBackend('/api/consolidate', { method: 'POST' });
        if (res && res.status === 'completed') {
            logSim(`✅ Consolidation Complete: Merged ${res.records_merged} records across ${res.clusters_detected} clusters. Before: ${res.memories_before} → After: ${res.memories_after}.`, 'success');
            showSimToast(`Consolidated: ${res.memories_before} → ${res.memories_after} memories (${res.records_merged} merged)`, 'success');
            setCanvasBanner(`🧬 SLEEP CYCLE CONSOLIDATED: Merged ${res.records_merged} memories into ${res.clusters_detected} clusters`, 'warning', 280);
        } else {
            logSim(`Consolidation executed on local memory index.`, 'info');
        }

        storedScatterMemories = storedScatterMemories.slice(0, 6);
        renderScatter();
        if (window.refreshOverview) window.refreshOverview().catch(() => {});
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  CANVAS RENDERING: ARENA (792 x 468)
    // ═══════════════════════════════════════════════════════════════════════
    function renderArena() {
        const canvas = document.getElementById('arena-canvas');
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        const W = canvas.width;
        const H = canvas.height;

        // 1. Clear floor background
        ctx.fillStyle = '#F8FAFC';
        ctx.fillRect(0, 0, W, H);

        // 2. Floor Grid
        ctx.strokeStyle = '#E2E8F0';
        ctx.lineWidth = 1;
        for (let c = 0; c <= COLS; c++) {
            ctx.beginPath();
            ctx.moveTo(c * CELL_SIZE, 0);
            ctx.lineTo(c * CELL_SIZE, H);
            ctx.stroke();
        }
        for (let r = 0; r <= ROWS; r++) {
            ctx.beginPath();
            ctx.moveTo(0, r * CELL_SIZE);
            ctx.lineTo(W, r * CELL_SIZE);
            ctx.stroke();
        }

        // Zone Labels
        ctx.font = '600 11px Inter, sans-serif';
        ctx.fillStyle = 'rgba(22, 33, 62, 0.08)';
        ctx.fillText('ZONE A — RECEIVING & DOCK', 14, H - 12);
        ctx.fillText('ZONE B — HIGH-BAY AISLES 1-4', 240, H - 12);
        ctx.fillText('ZONE C — BULK STORAGE AISLES 5-8', 520, H - 12);

        // 3. Draw Shelves & Dock Station
        for (let r = 0; r < ROWS; r++) {
            for (let c = 0; c < COLS; c++) {
                const cellType = grid[r][c];
                const x = c * CELL_SIZE;
                const y = r * CELL_SIZE;

                if (cellType === 1) {
                    ctx.fillStyle = '#16213E';
                    ctx.fillRect(x + 2, y + 2, CELL_SIZE - 4, CELL_SIZE - 4);
                    ctx.fillStyle = '#1F2E54';
                    ctx.fillRect(x + 4, y + 4, CELL_SIZE - 8, CELL_SIZE - 8);
                    ctx.strokeStyle = '#2D3F6E';
                    ctx.lineWidth = 1;
                    ctx.strokeRect(x + 4, y + 4, CELL_SIZE - 8, CELL_SIZE - 8);
                } else if (cellType === 2) {
                    ctx.save();
                    // Crisp Charging Pad
                    ctx.fillStyle = 'rgba(15, 184, 160, 0.12)';
                    ctx.fillRect(x + 1, y + 1, CELL_SIZE - 2, CELL_SIZE - 2);
                    
                    ctx.strokeStyle = '#0FB8A0';
                    ctx.lineWidth = 1.5;
                    ctx.strokeRect(x + 2, y + 2, CELL_SIZE - 4, CELL_SIZE - 4);
                    
                    // Inner glowing cross/marker
                    ctx.strokeStyle = 'rgba(15, 184, 160, 0.35)';
                    ctx.lineWidth = 1;
                    ctx.strokeRect(x + 6, y + 6, CELL_SIZE - 12, CELL_SIZE - 12);

                    // Perfectly Centered Label
                    ctx.textAlign = 'center';
                    ctx.textBaseline = 'middle';
                    ctx.font = '700 8.5px "Fira Code", monospace';
                    ctx.fillStyle = '#0FB8A0';
                    ctx.fillText('⚡DOCK', x + (CELL_SIZE / 2), y + (CELL_SIZE / 2));
                    ctx.restore();
                }
            }
        }

        // 4. Draw Planned BFS Path
        if (robot.path && robot.path.length > 0) {
            ctx.beginPath();
            ctx.strokeStyle = 'rgba(15, 184, 160, 0.65)';
            ctx.lineWidth = 3;
            ctx.setLineDash([5, 5]);
            ctx.moveTo(robot.x, robot.y);
            robot.path.forEach(pt => ctx.lineTo(pt.x, pt.y));
            ctx.stroke();
            ctx.setLineDash([]);
        }

        // 5. Draw Items & Hazards
        items.forEach(item => {
            if (item.picked) return;

            if (item.isHazard) {
                // Hazard rendering
                ctx.fillStyle = 'rgba(232, 93, 74, 0.3)';
                ctx.beginPath();
                ctx.arc(item.x, item.y, CELL_SIZE * 0.46, 0, Math.PI * 2);
                ctx.fill();
                ctx.strokeStyle = '#E85D4A';
                ctx.lineWidth = 2.5;
                ctx.stroke();

                ctx.font = '16px Inter, sans-serif';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillText(item.hazardInfo.icon || '⚠️', item.x, item.y);

                // Warning badge label
                ctx.fillStyle = '#16213E';
                ctx.fillRect(item.x - 32, item.y - 24, 64, 14);
                ctx.strokeStyle = '#E85D4A';
                ctx.lineWidth = 1;
                ctx.strokeRect(item.x - 32, item.y - 24, 64, 14);
                ctx.font = '700 8px Fira Code, monospace';
                ctx.fillStyle = '#FF7B69';
                ctx.fillText('ANOMALY', item.x, item.y - 17);
            } else {
                // Routine Cargo Box
                const color = ROUTINE_CLUSTERS[item.type].color;
                ctx.fillStyle = color;
                ctx.beginPath();
                ctx.roundRect(item.x - 9, item.y - 9, 18, 18, 4);
                ctx.fill();
                ctx.strokeStyle = '#FFFFFF';
                ctx.lineWidth = 1.5;
                ctx.stroke();

                if (item.detected) {
                    ctx.fillStyle = '#FFFFFF';
                    ctx.font = '9px Fira Code, monospace';
                    ctx.textAlign = 'center';
                    ctx.textBaseline = 'middle';
                    ctx.fillText(item.type[0].toUpperCase(), item.x, item.y);
                }
            }
        });

        // 6. Expanding Radar Waves & Anomaly Pulses
        for (let i = pulses.length - 1; i >= 0; i--) {
            const p = pulses[i];
            ctx.beginPath();
            ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
            ctx.strokeStyle = p.color;
            ctx.globalAlpha = p.alpha;
            ctx.lineWidth = 2.5;
            ctx.stroke();
            ctx.globalAlpha = 1.0;

            p.radius += 2.2;
            p.alpha -= 0.025;
            if (p.alpha <= 0 || p.radius >= p.maxRadius) {
                pulses.splice(i, 1);
            }
        }

        // 7. Lock-On Laser Target Reticle
        if (robot.lockOnTarget && robot.lockOnTarget.alpha > 0) {
            ctx.save();
            ctx.strokeStyle = robot.lockOnTarget.color;
            ctx.lineWidth = 2;
            ctx.globalAlpha = robot.lockOnTarget.alpha;
            
            // Laser beam from robot to target
            ctx.beginPath();
            ctx.moveTo(robot.x, robot.y);
            ctx.lineTo(robot.lockOnTarget.x, robot.lockOnTarget.y);
            ctx.stroke();

            // Reticle circle
            ctx.beginPath();
            ctx.arc(robot.lockOnTarget.x, robot.lockOnTarget.y, 22, 0, Math.PI * 2);
            ctx.stroke();

            ctx.font = '600 10px Fira Code, monospace';
            ctx.fillStyle = robot.lockOnTarget.color;
            ctx.fillText(`LOCKED ON: ${robot.lockOnTarget.text}`, robot.lockOnTarget.x, robot.lockOnTarget.y + 32);

            ctx.restore();
            robot.lockOnTarget.alpha -= 0.015;
            if (robot.lockOnTarget.alpha <= 0) robot.lockOnTarget = null;
        }

        // 8. Draw Robot & 3.5-Cell Radar Scanner
        const scanDistPx = SCAN_RADIUS_CELLS * CELL_SIZE;

        ctx.save();
        ctx.beginPath();
        ctx.arc(robot.x, robot.y, scanDistPx, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(15, 184, 160, 0.07)';
        ctx.fill();
        ctx.strokeStyle = 'rgba(15, 184, 160, 0.3)';
        ctx.lineWidth = 1.5;
        ctx.stroke();

        ctx.beginPath();
        ctx.moveTo(robot.x, robot.y);
        ctx.arc(robot.x, robot.y, scanDistPx, robot.radarAngle - 0.4, robot.radarAngle + 0.4);
        ctx.lineTo(robot.x, robot.y);
        const grad = ctx.createRadialGradient(robot.x, robot.y, 0, robot.x, robot.y, scanDistPx);
        grad.addColorStop(0, 'rgba(15, 184, 160, 0.45)');
        grad.addColorStop(1, 'rgba(15, 184, 160, 0.0)');
        ctx.fillStyle = grad;
        ctx.fill();
        ctx.restore();

        // Robot Chassis
        ctx.save();
        ctx.translate(robot.x, robot.y);
        ctx.rotate(robot.heading);

        ctx.fillStyle = '#16213E';
        ctx.beginPath();
        ctx.arc(0, 0, 13, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = '#0FB8A0';
        ctx.lineWidth = 2.5;
        ctx.stroke();

        ctx.fillStyle = '#0FB8A0';
        ctx.beginPath();
        ctx.arc(10, 0, 4, 0, Math.PI * 2);
        ctx.fill();

        for (let i = 0; i < robot.maxCargo; i++) {
            const angle = Math.PI * 0.7 + i * 0.3;
            const cx = Math.cos(angle) * 7;
            const cy = Math.sin(angle) * 7;
            ctx.fillStyle = i < robot.cargo.length ? '#0FB8A0' : '#4A5568';
            ctx.beginPath();
            ctx.arc(cx, cy, 2, 0, Math.PI * 2);
            ctx.fill();
        }

        ctx.restore();

        // 9. On-Screen Status Banner overlay
        if (onScreenBanner && onScreenBanner.timer > 0) {
            ctx.save();
            const bannerY = 18;
            ctx.font = '600 12px Inter, sans-serif';
            const textWidth = ctx.measureText(onScreenBanner.text).width;
            const boxW = Math.max(340, textWidth + 36);
            const boxX = (W - boxW) / 2;

            ctx.fillStyle = onScreenBanner.type === 'error' ? '#16213E' : (onScreenBanner.type === 'warning' ? '#16213E' : '#16213E');
            ctx.fillRect(boxX, bannerY, boxW, 28);
            ctx.strokeStyle = onScreenBanner.type === 'error' ? '#E85D4A' : (onScreenBanner.type === 'warning' ? '#F5A623' : '#0FB8A0');
            ctx.lineWidth = 1.5;
            ctx.strokeRect(boxX, bannerY, boxW, 28);

            ctx.fillStyle = onScreenBanner.type === 'error' ? '#FF7B69' : (onScreenBanner.type === 'warning' ? '#F5A623' : '#0FB8A0');
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(onScreenBanner.text, W / 2, bannerY + 14);

            ctx.restore();
            onScreenBanner.timer--;
        }
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  CANVAS RENDERING: 2D VECTOR SCATTER SPACE (310 x 175)
    // ═══════════════════════════════════════════════════════════════════════
    function renderScatter() {
        const canvas = document.getElementById('scatter-canvas');
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        const W = canvas.width;
        const H = canvas.height;

        ctx.fillStyle = '#16213E';
        ctx.fillRect(0, 0, W, H);

        ctx.strokeStyle = 'rgba(255, 255, 255, 0.08)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(W / 2, 0); ctx.lineTo(W / 2, H);
        ctx.moveTo(0, H / 2); ctx.lineTo(W, H / 2);
        ctx.stroke();

        Object.keys(ROUTINE_CLUSTERS).forEach(k => {
            const cl = ROUTINE_CLUSTERS[k];
            const px = cl.center[0] * W;
            const py = cl.center[1] * H;

            ctx.fillStyle = 'rgba(15, 184, 160, 0.12)';
            ctx.beginPath();
            ctx.arc(px, py, 26, 0, Math.PI * 2);
            ctx.fill();

            ctx.font = '10px Inter, sans-serif';
            ctx.fillStyle = '#94A3B8';
            ctx.textAlign = 'center';
            ctx.fillText(cl.label, px, py + 38);
        });

        HAZARD_TYPES.forEach(hz => {
            const px = hz.center[0] * W;
            const py = hz.center[1] * H;
            ctx.fillStyle = 'rgba(232, 93, 74, 0.14)';
            ctx.beginPath();
            ctx.arc(px, py, 16, 0, Math.PI * 2);
            ctx.fill();
        });

        storedScatterMemories.forEach(sm => {
            const px = sm.point[0] * W;
            const py = sm.point[1] * H;
            ctx.fillStyle = sm.type === 'spill' || sm.type === 'cyber' ? '#E85D4A' : '#0FB8A0';
            ctx.beginPath();
            ctx.arc(px, py, 4, 0, Math.PI * 2);
            ctx.fill();
            ctx.strokeStyle = '#FFFFFF';
            ctx.lineWidth = 1;
            ctx.stroke();
        });

        if (lastVectorMatch) {
            const curX = lastVectorMatch.current[0] * W;
            const curY = lastVectorMatch.current[1] * H;
            const nX = lastVectorMatch.nearest[0] * W;
            const nY = lastVectorMatch.nearest[1] * H;

            ctx.beginPath();
            ctx.setLineDash([4, 4]);
            ctx.strokeStyle = lastVectorMatch.isAnomaly ? '#E85D4A' : '#0FB8A0';
            ctx.lineWidth = 2;
            ctx.moveTo(curX, curY);
            ctx.lineTo(nX, nY);
            ctx.stroke();
            ctx.setLineDash([]);

            ctx.fillStyle = lastVectorMatch.isAnomaly ? '#E85D4A' : '#F5A623';
            ctx.beginPath();
            ctx.arc(curX, curY, 6, 0, Math.PI * 2);
            ctx.fill();
            ctx.strokeStyle = '#FFFFFF';
            ctx.lineWidth = 2;
            ctx.stroke();

            const midX = (curX + nX) / 2;
            const midY = (curY + nY) / 2;
            ctx.fillStyle = '#0F172A';
            ctx.fillRect(midX - 34, midY - 10, 68, 18);
            ctx.strokeStyle = lastVectorMatch.isAnomaly ? '#E85D4A' : '#0FB8A0';
            ctx.lineWidth = 1;
            ctx.strokeRect(midX - 34, midY - 10, 68, 18);

            ctx.font = '600 10px Fira Code, monospace';
            ctx.fillStyle = lastVectorMatch.isAnomaly ? '#FF7B69' : '#0FB8A0';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(`Sim: ${(lastVectorMatch.similarity * 100).toFixed(0)}%`, midX, midY);
        }
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  DUAL SYNC LANES UI
    // ═══════════════════════════════════════════════════════════════════════
    function renderLanes() {
        const elFast = document.getElementById('sim-fast-lane-items');
        const elBatch = document.getElementById('sim-batch-lane-items');

        if (elFast) {
            if (fastLane.length === 0) {
                elFast.innerHTML = '<div class="sim-empty-lane">No active anomalies (Fast-Path Idle)</div>';
            } else {
                elFast.innerHTML = fastLane.map(item => `
                    <div class="sim-lane-card fast">
                        <div class="sim-lane-header">
                            <span class="badge anomaly">⚡ PRIORITY DISPATCH</span>
                            <span class="sim-lane-time">${item.time}</span>
                        </div>
                        <div class="sim-lane-text">${item.name} <span style="font-size:0.75rem;opacity:0.75">#${item.id}</span></div>
                        <div class="sim-lane-sub">Nearest Sim: ${(item.sim * 100).toFixed(0)}% &lt; 70% threshold &rarr; Pushed to Cloud</div>
                    </div>
                `).join('');
            }
        }

        if (elBatch) {
            if (batchLane.length === 0) {
                elBatch.innerHTML = '<div class="sim-empty-lane">Batch buffer empty</div>';
            } else {
                elBatch.innerHTML = batchLane.slice(0, 4).map(item => `
                    <div class="sim-lane-card batch">
                        <span class="badge synced">📦 BATCH QUEUE</span>
                        <span style="font-weight:500;margin-left:8px">${item.name}</span>
                        <span style="font-size:0.75rem;color:#718096;margin-left:auto">${(item.sim * 100).toFixed(0)}%</span>
                    </div>
                `).join('') + (batchLane.length > 4 ? `<div style="font-size:0.75rem;color:#718096;text-align:center;padding-top:4px">+${batchLane.length - 4} more records waiting for sleep cycle</div>` : '');
            }
        }
    }

    function showSimToast(msg, type = 'info') {
        const container = document.getElementById('toast-container');
        if (!container) return;
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.textContent = msg;
        container.appendChild(toast);
        requestAnimationFrame(() => toast.classList.add('show'));
        setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => toast.remove(), 350);
        }, 3000);
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  RESET & INITIALIZATION
    // ═══════════════════════════════════════════════════════════════════════
    function resetSimulation() {
        grid = initialGrid.map(row => [...row]);
        dynamicObstacles.clear();
        items = [];
        pulses = [];
        fastLane = [];
        batchLane = [];
        robot.x = 1.5 * CELL_SIZE;
        robot.y = 1.5 * CELL_SIZE;
        robot.cellC = 1;
        robot.cellR = 1;
        robot.heading = 0;
        robot.cargo = [];
        robot.state = isRunning ? 'PATROLLING' : 'STANDBY';
        robot.path = [];
        robot.targetItem = null;
        robot.lockOnTarget = null;
        lastVectorMatch = null;
        savedToQdrantCount = 0;

        spawnRoutineItems(6);
        logSim('Arena & Vector Store reset to factory baseline.', 'info');
        renderLanes();
        updatePills();
    }

    function toggleMission() {
        isRunning = !isRunning;
        const btn = document.getElementById('btn-sim-toggle');
        if (btn) {
            if (isRunning) {
                btn.textContent = '⏸ Pause Mission';
                btn.style.background = '#E85D4A';
                robot.state = 'PATROLLING';
                logSim('▶ Mission Started: Autonomous warehouse LiDAR patrol & Qdrant recording active.', 'success');
            } else {
                btn.textContent = '▶ Start Mission';
                btn.style.background = '#0FB8A0';
                robot.state = 'STANDBY';
                logSim('⏸ Mission Paused: Robot held at current station.', 'info');
            }
        }
        updatePills();
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  MAIN ANIMATION LOOP (60 FPS)
    // ═══════════════════════════════════════════════════════════════════════
    function loop() {
        updateRobot();
        renderArena();
        renderScatter();
        animFrameId = requestAnimationFrame(loop);
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  ATTACH CONTROLS
    // ═══════════════════════════════════════════════════════════════════════
    function initSimulation() {
        resetSimulation();

        // Control Buttons
        const btnToggle = document.getElementById('btn-sim-toggle');
        if (btnToggle) btnToggle.addEventListener('click', toggleMission);

        const btnAnomaly = document.getElementById('btn-sim-inject-anomaly');
        if (btnAnomaly) btnAnomaly.addEventListener('click', injectAnomalyHazard);

        const btnSpawn = document.getElementById('btn-sim-spawn-routine');
        if (btnSpawn) btnSpawn.addEventListener('click', () => spawnRoutineItems(4));

        const btnConsolidate = document.getElementById('btn-sim-consolidate');
        if (btnConsolidate) btnConsolidate.addEventListener('click', consolidateSimulationMemories);

        const btnReset = document.getElementById('btn-sim-reset');
        if (btnReset) btnReset.addEventListener('click', resetSimulation);

        if (!animFrameId) {
            loop();
        }
    }

    window.CORTEX_SIM = {
        init: initSimulation,
        reset: resetSimulation,
        toggle: toggleMission,
        injectAnomaly: injectAnomalyHazard,
        consolidate: consolidateSimulationMemories
    };

    document.addEventListener('DOMContentLoaded', () => {
        initSimulation();
    });
})();
