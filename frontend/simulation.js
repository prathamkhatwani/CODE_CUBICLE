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

    // Grid states: 0 = Empty floor, 1 = Shelf (permanent obstacle), 2 = Dock Alpha, 3 = Dock Bravo
    const initialGrid = [
        [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
        [0,2,0,0,1,1,0,0,1,1,0,0,1,1,0,0,1,1,0,0,3,0],
        [0,2,0,0,1,1,0,0,1,1,0,0,1,1,0,0,1,1,0,0,3,0],
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
    let showHeatmap = false;

    let exploredGrid = Array.from({ length: ROWS }, () => Array(COLS).fill(false));
    let visitHeatmap = Array.from({ length: ROWS }, () => Array(COLS).fill(0));

    let robots = [
        {
            id: 'edge-001',
            name: 'Alpha',
            badge: 'Alpha [edge-001]',
            color: '#0FB8A0', // Teal
            zoneMinC: 1,
            zoneMaxC: 10,
            dockC: 1,
            dockR: 1,
            x: 1.5 * CELL_SIZE,
            y: 1.5 * CELL_SIZE,
            cellC: 1,
            cellR: 1,
            heading: 0,
            speed: 2.2,
            battery: 100,
            cargo: [],
            maxCargo: 3,
            state: 'STANDBY', // 'STANDBY' | 'PATROLLING' | 'PICKING' | 'RETURNING' | 'DOCKING' | 'AVOIDING_HAZARD'
            path: [],
            trail: [],
            targetItem: null,
            radarAngle: 0,
            lockOnTarget: null
        },
        {
            id: 'edge-002',
            name: 'Bravo',
            badge: 'Bravo [edge-002]',
            color: '#F5A623', // Amber
            zoneMinC: 11,
            zoneMaxC: 20,
            dockC: 20,
            dockR: 1,
            x: 20.5 * CELL_SIZE,
            y: 1.5 * CELL_SIZE,
            cellC: 20,
            cellR: 1,
            heading: Math.PI,
            speed: 2.2,
            battery: 100,
            cargo: [],
            maxCargo: 3,
            state: 'STANDBY',
            path: [],
            trail: [],
            targetItem: null,
            radarAngle: Math.PI,
            lockOnTarget: null
        }
    ];

    let robotAlpha = robots[0];
    let robotBravo = robots[1];

    let items = [];
    let pulses = []; // { x, y, radius, maxRadius, color, alpha, isAnomaly, rings, rotation }
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
    const API_BASE = '';

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
    //  PATHFINDING: BFS (Multi-Robot Aware)
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

    function findLocalPatrolPath(bot) {
        const candidates = [];
        const minC = bot.zoneMinC || 1;
        const maxC = bot.zoneMaxC || (COLS - 2);

        for (let r = 1; r < ROWS - 1; r++) {
            for (let c = minC; c <= maxC; c++) {
                if (grid[r][c] === 0 && !dynamicObstacles.has(`${c},${r}`)) {
                    const dist = Math.abs(c - bot.cellC) + Math.abs(r - bot.cellR);
                    if (dist >= 2 && dist <= 12) {
                        candidates.push({ c, r });
                    }
                }
            }
        }
        // Fallback to full grid if zone is constrained
        if (candidates.length === 0) {
            for (let r = 1; r < ROWS - 1; r++) {
                for (let c = 1; c < COLS - 1; c++) {
                    if (grid[r][c] === 0 && !dynamicObstacles.has(`${c},${r}`)) {
                        candidates.push({ c, r });
                    }
                }
            }
        }
        candidates.sort(() => Math.random() - 0.5);
        for (const cand of candidates) {
            const p = findPathBFS(bot.cellC, bot.cellR, cand.c, cand.r);
            if (p && p.length > 0) {
                bot.path = p;
                return p;
            }
        }
        return null;
    }

    function replanPathAroundHazards(bot) {
        if (!bot) return;

        if (bot.targetItem && !dynamicObstacles.has(`${bot.targetItem.c},${bot.targetItem.r}`)) {
            const p = findPathBFS(bot.cellC, bot.cellR, bot.targetItem.c, bot.targetItem.r);
            if (p && p.length > 0) {
                bot.path = p;
                return;
            }
        }

        // If target item is unreachable or is a hazard, reset it
        bot.targetItem = null;

        if (bot.state === 'RETURNING') {
            const dockPath = findPathBFS(bot.cellC, bot.cellR, bot.dockC, bot.dockR);
            if (dockPath && dockPath.length > 0) {
                bot.path = dockPath;
            } else {
                findLocalPatrolPath(bot);
            }
            return;
        }

        // Search for an unblocked routine item in or near bot's zone
        const available = items.filter(it => !it.isHazard && !it.picked && !dynamicObstacles.has(`${it.c},${it.r}`));
        let bestPath = null;
        let chosenItem = null;
        for (const it of available) {
            const p = findPathBFS(bot.cellC, bot.cellR, it.c, it.r);
            if (p && p.length > 0 && (!bestPath || p.length < bestPath.length)) {
                bestPath = p;
                chosenItem = it;
            }
        }

        if (bestPath && chosenItem) {
            bot.targetItem = chosenItem;
            bot.path = bestPath;
            bot.state = 'PICKING';
        } else {
            // Patrol towards safe area or local patrol loop
            bot.state = 'PATROLLING';
            const dockPath = findPathBFS(bot.cellC, bot.cellR, bot.dockC, bot.dockR);
            if (dockPath && dockPath.length > 0) {
                bot.path = dockPath;
            } else {
                findLocalPatrolPath(bot);
            }
        }
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
        const elStateAlpha = document.getElementById('sim-stat-state-alpha');
        const elStateBravo = document.getElementById('sim-stat-state-bravo');
        const elSaved = document.getElementById('sim-stat-saved-count');
        const elFast = document.getElementById('sim-stat-fast-count');
        const elFleetSync = document.getElementById('sim-stat-fleet-sync');

        if (elStateAlpha) elStateAlpha.textContent = `${robotAlpha.state} (${robotAlpha.cargo.length}/${robotAlpha.maxCargo})`;
        if (elStateBravo) elStateBravo.textContent = `${robotBravo.state} (${robotBravo.cargo.length}/${robotBravo.maxCargo})`;
        if (elSaved) elSaved.textContent = savedToQdrantCount;
        if (elFast) elFast.textContent = fastLane.length;
        if (elFleetSync) elFleetSync.textContent = 'CONNECTED';
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
        
        // Find best spawn location: in front of Robot Alpha (edge-001) in left zone
        let targetC = null, targetR = null;

        // Try placing ahead along Alpha's path
        if (robotAlpha.path && robotAlpha.path.length > 1) {
            const step = robotAlpha.path[Math.min(2, robotAlpha.path.length - 1)];
            if (grid[step.r][step.c] === 0 && !dynamicObstacles.has(`${step.c},${step.r}`)) {
                targetC = step.c;
                targetR = step.r;
            }
        }

        // Try adjacent forward cell based on Alpha's heading
        if (targetC === null) {
            const forwardC = Math.round(robotAlpha.cellC + Math.cos(robotAlpha.heading) * 2);
            const forwardR = Math.round(robotAlpha.cellR + Math.sin(robotAlpha.heading) * 2);
            if (forwardC >= 0 && forwardC < COLS && forwardR >= 0 && forwardR < ROWS && grid[forwardR][forwardC] === 0 && !dynamicObstacles.has(`${forwardC},${forwardR}`)) {
                targetC = forwardC;
                targetR = forwardR;
            }
        }

        // Fallback: open cell in Alpha's left patrol zone (cols 2-8)
        if (targetC === null) {
            let attempts = 0;
            while (attempts < 50) {
                attempts++;
                const c = Math.floor(Math.random() * 7) + 2;
                const r = Math.floor(Math.random() * (ROWS - 4)) + 2;
                if (grid[r][c] === 0 && !dynamicObstacles.has(`${c},${r}`) && (c !== robotAlpha.cellC || r !== robotAlpha.cellR)) {
                    targetC = c;
                    targetR = r;
                    break;
                }
            }
        }

        if (targetC !== null) {
            // Block cell IMMEDIATELY in navigation
            dynamicObstacles.add(`${targetC},${targetR}`);

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
                detected: true, // Immediate capture by Alpha
                recording: false,
                picked: false
            };
            items.push(hazardItem);

            // Replan Alpha's path around hazard IMMEDIATELY and synchronously
            replanPathAroundHazards(robotAlpha);

            // Auto-start simulation if stopped so user sees dynamic fleet interaction immediately
            if (!isRunning) {
                isRunning = true;
                const btn = document.getElementById('btn-sim-toggle');
                if (btn) {
                    btn.textContent = '⏸ Pause Fleet Mission';
                    btn.style.background = '#E85D4A';
                }
            }

            // Robot Alpha LiDAR Lock-On Aim
            robotAlpha.heading = Math.atan2(hazardItem.y - robotAlpha.y, hazardItem.x - robotAlpha.x);
            robotAlpha.lockOnTarget = {
                x: hazardItem.x,
                y: hazardItem.y,
                alpha: 1.0,
                color: '#E85D4A',
                text: `${hazardMeta.name} [ALPHA 001]`
            };

            logSim(`⚡ [ALPHA RADAR LOCK-ON] Detected ${hazardMeta.name} at (${targetC}, ${targetR}) — Triggering Qdrant Edge Embedding!`, 'warning');
            showSimToast(`🚨 Alpha (edge-001) Detected: ${hazardMeta.name}`, 'error');
            setCanvasBanner(`🚨 NOVEL HAZARD IN ALPHA'S AISLE: ${hazardMeta.name} — SAVING TO QDRANT EDGE...`, 'error', 240);

            // Record to Qdrant backend immediately
            recordMemoryToQdrant(hazardItem, robotAlpha);
        }
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  REAL DETECTION & QDRANT RECORDING ENGINE (MULTI-ROBOT & FLEET SYNC)
    // ═══════════════════════════════════════════════════════════════════════
    async function recordMemoryToQdrant(item, detectingBot = robotAlpha) {
        if (item.recording) return;
        item.recording = true;
        
        const payload = {
            text: item.text,
            source: detectingBot ? `robot-${detectingBot.id}-lidar` : 'robot-lidar-radar',
            importance: item.importance,
            category: item.category,
            tags: item.isHazard ? ['hazard', 'lidar-radar', 'anomaly', 'fast-path', 'fleet-broadcast'] : ['cargo', 'lidar-radar', 'warehouse-patrol'],
            metadata: {
                grid_c: item.c,
                grid_r: item.r,
                type: item.type,
                detector_device: detectingBot ? detectingBot.id : 'edge-001',
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

            // 3-ring holographic radar shockwave explosion
            pulses.push({
                x: item.x,
                y: item.y,
                radius: 14,
                maxRadius: 110,
                color: '#E85D4A',
                alpha: 1.0,
                isAnomaly: true,
                rings: 3,
                rotation: 0,
                name: item.hazardInfo.name
            });

            fastLane.unshift({
                name: `${item.hazardInfo.name} (${detectingBot.name})`,
                sim: nearestSim,
                id: memId.slice(0, 8),
                time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
            });
            if (fastLane.length > 5) fastLane.pop();

            logSim(`🚨 [QDRANT EDGE] Anomaly Saved by ${detectingBot.name}: ${item.hazardInfo.name} (Sim: ${(nearestSim * 100).toFixed(0)}% < 70%) → CLOUD FAST-PATH PUSHED!`, 'error');

            storedScatterMemories.push({
                type: item.type,
                point: scatterPoint,
                text: item.text
            });

            // ═══════════════════════════════════════════════════════════════
            // 🌟 AHA! MOMENT: FLEET BROADCAST VIA CLOUD SYNC BRIDGE
            // ═══════════════════════════════════════════════════════════════
            // Robot Alpha saved to Qdrant Edge -> pushed via Fast-Path to Cloud Qdrant.
            // Cloud sync broadcasts the hazard obstacle to the entire fleet (Robot Bravo edge-002).
            // Robot Bravo recalculates its route to avoid the hazard BEFORE entering that aisle!
            const otherBot = detectingBot.id === 'edge-001' ? robotBravo : robotAlpha;
            
            // Replan for detecting bot
            replanPathAroundHazards(detectingBot);

            // Trigger fleet broadcast to other bot
            setTimeout(() => {
                logSim(`📡 [FLEET CLOUD SYNC] ${otherBot.name} (${otherBot.id}) received hazard telemetry from ${detectingBot.name} — Rerouting route before entering corridor!`, 'warning');
                showSimToast(`📡 Fleet Broadcast: ${otherBot.name} rerouting around hazard!`, 'info');
                setCanvasBanner(`📡 FLEET BROADCAST: ${detectingBot.name} &rarr; Cloud &rarr; ${otherBot.name} — Rerouting path!`, 'warning', 320);

                // Give other bot a visual notification pulse
                pulses.push({
                    x: otherBot.x,
                    y: otherBot.y,
                    radius: 10,
                    maxRadius: 75,
                    color: otherBot.color,
                    alpha: 0.9,
                    isAnomaly: false,
                    rings: 1,
                    rotation: 0
                });

                // Force other bot to replan path immediately
                replanPathAroundHazards(otherBot);
            }, 300);
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
    function updateRobots() {
        if (!isRunning) return;

        robots.forEach(bot => {
            bot.cellC = Math.floor(bot.x / CELL_SIZE);
            bot.cellR = Math.floor(bot.y / CELL_SIZE);
            bot.radarAngle = (bot.radarAngle + 0.06) % (Math.PI * 2);

            // 1. Fog-of-War Exploration Pass & Heatmap Visit Tracking
            const scanDistPx = SCAN_RADIUS_CELLS * CELL_SIZE;
            for (let r = 0; r < ROWS; r++) {
                for (let c = 0; c < COLS; c++) {
                    if (!exploredGrid[r][c]) {
                        const cx = (c + 0.5) * CELL_SIZE;
                        const cy = (r + 0.5) * CELL_SIZE;
                        if (Math.hypot(cx - bot.x, cy - bot.y) <= scanDistPx + 12) {
                            exploredGrid[r][c] = true;
                        }
                    }
                }
            }

            if (bot.cellR >= 0 && bot.cellR < ROWS && bot.cellC >= 0 && bot.cellC < COLS) {
                visitHeatmap[bot.cellR][bot.cellC] = (visitHeatmap[bot.cellR][bot.cellC] || 0) + 1;
            }

            // Battery level simulation
            if (bot.state === 'DOCKING') {
                bot.battery = Math.min(100, (bot.battery || 100) + 0.6);
            } else {
                bot.battery = Math.max(12, (bot.battery || 100) - 0.012);
            }

            // Exploration breadcrumbs trail
            bot.trail = bot.trail || [];
            if (bot.trail.length === 0 || Math.hypot(bot.x - bot.trail[bot.trail.length - 1].x, bot.y - bot.trail[bot.trail.length - 1].y) > 14) {
                bot.trail.push({ x: bot.x, y: bot.y, alpha: 0.6 });
                if (bot.trail.length > 40) bot.trail.shift();
            }

            // 2. Radar Scanning for Unseen Objects
            items.forEach(item => {
                if (!item.detected && !item.picked && !item.recording) {
                    const dx = item.x - bot.x;
                    const dy = item.y - bot.y;
                    const dist = Math.sqrt(dx * dx + dy * dy);

                    if (dist <= scanDistPx) {
                        item.detected = true;
                        if (item.isHazard) {
                            dynamicObstacles.add(`${item.c},${item.r}`);
                            replanPathAroundHazards(bot);
                        }
                        bot.lockOnTarget = {
                            x: item.x,
                            y: item.y,
                            alpha: 1.0,
                            color: item.isHazard ? '#E85D4A' : bot.color,
                            text: item.isHazard ? `${item.hazardInfo.name} [${bot.name}]` : ROUTINE_CLUSTERS[item.type].label
                        };
                        recordMemoryToQdrant(item, bot);
                    }
                }
            });

            // 3. State Machine & Autonomous Patrol
            if (bot.cargo.length >= bot.maxCargo && bot.state !== 'RETURNING' && bot.state !== 'DOCKING') {
                bot.state = 'RETURNING';
                const pathToDock = findPathBFS(bot.cellC, bot.cellR, bot.dockC, bot.dockR);
                if (pathToDock && pathToDock.length > 0) {
                    bot.path = pathToDock;
                    logSim(`📦 ${bot.name} cargo full (${bot.cargo.length}/${bot.maxCargo}) — Returning to Dock (${bot.dockC},${bot.dockR})...`, 'info');
                } else {
                    logSim(`⚠️ ${bot.name} Dock (${bot.dockC},${bot.dockR}) blocked! Field depot unload engaged.`, 'warning');
                    bot.state = 'DOCKING';
                    setTimeout(() => {
                        const unloaded = bot.cargo.length;
                        bot.cargo = [];
                        logSim(`⚡ ${bot.name} FIELD DEPOT: Unloaded ${unloaded} items safely.`, 'success');
                        bot.state = 'PATROLLING';
                        findLocalPatrolPath(bot);
                    }, 800);
                }
            } else if (bot.state === 'PATROLLING' || bot.state === 'STANDBY') {
                bot.state = 'PATROLLING';
                // Find nearest reachable unpicked routine item in/near bot zone
                const available = items.filter(it => !it.isHazard && !it.picked && !robots.some(other => other !== bot && other.targetItem === it));
                let nearest = null;
                let minPathLen = Infinity;
                let bestPath = null;

                available.forEach(it => {
                    const p = findPathBFS(bot.cellC, bot.cellR, it.c, it.r);
                    if (p && p.length < minPathLen) {
                        minPathLen = p.length;
                        nearest = it;
                        bestPath = p;
                    }
                });

                if (nearest && bestPath) {
                    bot.targetItem = nearest;
                    bot.path = bestPath;
                    bot.state = 'PICKING';
                } else {
                    // If bot has no path, patrol reachable open waypoints in bot zone
                    if (!bot.path || bot.path.length === 0) {
                        findLocalPatrolPath(bot);
                    }
                }
            }

            // 4. Movement Execution
            if (bot.path && bot.path.length > 0) {
                const nextNode = bot.path[0];

                // Obstacle & Hazard Collision Avoidance Guard
                if (dynamicObstacles.has(`${nextNode.c},${nextNode.r}`)) {
                    logSim(`⚠️ [${bot.name}] Hazard blocking route at (${nextNode.c}, ${nextNode.r})! Rerouting dynamically...`, 'warning');
                    bot.path = [];
                    replanPathAroundHazards(bot);
                } else {
                    const dx = nextNode.x - bot.x;
                    const dy = nextNode.y - bot.y;
                    const dist = Math.sqrt(dx * dx + dy * dy);

                    const targetHeading = Math.atan2(dy, dx);
                    let diff = targetHeading - bot.heading;
                    while (diff < -Math.PI) diff += Math.PI * 2;
                    while (diff > Math.PI) diff -= Math.PI * 2;
                    bot.heading += diff * 0.25;

                    if (dist < bot.speed) {
                        bot.x = nextNode.x;
                        bot.y = nextNode.y;
                        bot.path.shift();

                        if (bot.path.length === 0) {
                            if (bot.state === 'PICKING' && bot.targetItem && !bot.targetItem.picked) {
                                bot.targetItem.picked = true;
                                bot.cargo.push(bot.targetItem);
                                logSim(`[${bot.name}] Picked up ${bot.targetItem.type.toUpperCase()} (Loaded: ${bot.cargo.length}/${bot.maxCargo})`, 'info');
                                bot.state = 'PATROLLING';
                                bot.targetItem = null;
                            } else if (bot.state === 'RETURNING') {
                                bot.state = 'DOCKING';
                                setTimeout(() => {
                                    const unloaded = bot.cargo.length;
                                    bot.cargo = [];
                                    logSim(`⚡ ${bot.name} DOCK: Unloaded ${unloaded} items. Synced to Qdrant Edge.`, 'success');
                                    bot.state = 'PATROLLING';
                                    findLocalPatrolPath(bot);
                                }, 700);
                            }
                        }
                    } else {
                        bot.x += Math.cos(targetHeading) * bot.speed;
                        bot.y += Math.sin(targetHeading) * bot.speed;
                    }
                }
            } else if (!bot.path || bot.path.length === 0) {
                if (bot.state === 'RETURNING') {
                    // Try to replan to dock, or field unload if dock is unreachable
                    const pathToDock = findPathBFS(bot.cellC, bot.cellR, bot.dockC, bot.dockR);
                    if (pathToDock && pathToDock.length > 0) {
                        bot.path = pathToDock;
                    } else {
                        // Field unload if dock is blocked by hazards
                        bot.state = 'DOCKING';
                        setTimeout(() => {
                            const unloaded = bot.cargo.length;
                            bot.cargo = [];
                            logSim(`⚡ ${bot.name} FIELD DEPOT: Emergency field unload (${unloaded} items). Corridor clear.`, 'success');
                            bot.state = 'PATROLLING';
                            findLocalPatrolPath(bot);
                        }, 600);
                    }
                } else if (bot.state === 'PICKING' || bot.state === 'PATROLLING') {
                    bot.state = 'PATROLLING';
                    findLocalPatrolPath(bot);
                }
            }

            // 4b. Physical Hazard Separation Guard
            items.forEach(it => {
                if (it.isHazard) {
                    const dist = Math.hypot(bot.x - it.x, bot.y - it.y);
                    const safeDist = CELL_SIZE * 0.95;
                    if (dist < safeDist && dist > 0) {
                        const angle = Math.atan2(bot.y - it.y, bot.x - it.x);
                        const push = (safeDist - dist) + 1;
                        bot.x += Math.cos(angle) * push;
                        bot.y += Math.sin(angle) * push;
                        bot.cellC = Math.max(0, Math.min(COLS - 1, Math.floor(bot.x / CELL_SIZE)));
                        bot.cellR = Math.max(0, Math.min(ROWS - 1, Math.floor(bot.y / CELL_SIZE)));
                        if (bot.path && bot.path.some(pt => dynamicObstacles.has(`${pt.c},${pt.r}`))) {
                            replanPathAroundHazards(bot);
                        }
                    }
                }
            });
        });

        // 4c. Inter-Robot Collision Avoidance & Priority Yielding (Alpha vs Bravo)
        const dAlphaBravo = Math.hypot(robotAlpha.x - robotBravo.x, robotAlpha.y - robotBravo.y);
        const minBotSeparation = CELL_SIZE * 0.95;
        if (dAlphaBravo < minBotSeparation && dAlphaBravo > 0) {
            // Bravo gently yields waypoint priority to Alpha if their paths cross
            if (robotBravo.path && robotBravo.path.length > 0) {
                const nextB = robotBravo.path[0];
                if (Math.hypot(nextB.x - robotAlpha.x, nextB.y - robotAlpha.y) < CELL_SIZE) {
                    findLocalPatrolPath(robotBravo);
                }
            } else if (robotAlpha.path && robotAlpha.path.length > 0) {
                const nextA = robotAlpha.path[0];
                if (Math.hypot(nextA.x - robotBravo.x, nextA.y - robotBravo.y) < CELL_SIZE) {
                    findLocalPatrolPath(robotAlpha);
                }
            }
        }

        // Global cargo auto-spawn if floor is sparse
        const unpickedCargo = items.filter(it => !it.isHazard && !it.picked);
        if (unpickedCargo.length < 3) {
            autoSpawnTimer++;
            if (autoSpawnTimer >= 120) {
                autoSpawnTimer = 0;
                spawnRoutineItems(4);
            }
        }

        // 5. Batch Lane Flush Timer (every 8s)
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
        
        robots.forEach(bot => {
            pulses.push({
                x: bot.x,
                y: bot.y,
                radius: 20,
                maxRadius: 180,
                color: '#F5A623',
                alpha: 0.95
            });
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

        // 3. Aisle Traffic Heatmap Overlay (if toggled)
        if (showHeatmap) {
            let maxVisits = 1;
            for (let r = 0; r < ROWS; r++) {
                for (let c = 0; c < COLS; c++) {
                    if (visitHeatmap[r][c] > maxVisits) maxVisits = visitHeatmap[r][c];
                }
            }

            for (let r = 0; r < ROWS; r++) {
                for (let c = 0; c < COLS; c++) {
                    const count = visitHeatmap[r][c];
                    if (count > 0 && grid[r][c] === 0) {
                        const ratio = Math.min(1.0, count / maxVisits);
                        const x = c * CELL_SIZE;
                        const y = r * CELL_SIZE;
                        ctx.save();
                        if (ratio < 0.35) {
                            ctx.fillStyle = `rgba(15, 184, 160, ${ratio * 0.75})`;
                        } else if (ratio < 0.7) {
                            ctx.fillStyle = `rgba(245, 166, 35, ${ratio * 0.8})`;
                        } else {
                            ctx.fillStyle = `rgba(232, 93, 74, ${ratio * 0.85})`;
                        }
                        ctx.fillRect(x + 1, y + 1, CELL_SIZE - 2, CELL_SIZE - 2);
                        ctx.restore();
                    }
                }
            }
        }

        // 4. Draw Shelves & Dock Station
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
                    // Dock Alpha Pad (Teal)
                    ctx.fillStyle = 'rgba(15, 184, 160, 0.12)';
                    ctx.fillRect(x + 1, y + 1, CELL_SIZE - 2, CELL_SIZE - 2);
                    
                    ctx.strokeStyle = '#0FB8A0';
                    ctx.lineWidth = 1.5;
                    ctx.strokeRect(x + 2, y + 2, CELL_SIZE - 4, CELL_SIZE - 4);
                    
                    ctx.strokeStyle = 'rgba(15, 184, 160, 0.35)';
                    ctx.lineWidth = 1;
                    ctx.strokeRect(x + 6, y + 6, CELL_SIZE - 12, CELL_SIZE - 12);

                    ctx.textAlign = 'center';
                    ctx.textBaseline = 'middle';
                    ctx.font = '700 8px "Fira Code", monospace';
                    ctx.fillStyle = '#0FB8A0';
                    ctx.fillText('⚡ALPHA', x + (CELL_SIZE / 2), y + (CELL_SIZE / 2));
                    ctx.restore();
                } else if (cellType === 3) {
                    ctx.save();
                    // Dock Bravo Pad (Amber)
                    ctx.fillStyle = 'rgba(245, 166, 35, 0.12)';
                    ctx.fillRect(x + 1, y + 1, CELL_SIZE - 2, CELL_SIZE - 2);
                    
                    ctx.strokeStyle = '#F5A623';
                    ctx.lineWidth = 1.5;
                    ctx.strokeRect(x + 2, y + 2, CELL_SIZE - 4, CELL_SIZE - 4);
                    
                    ctx.strokeStyle = 'rgba(245, 166, 35, 0.35)';
                    ctx.lineWidth = 1;
                    ctx.strokeRect(x + 6, y + 6, CELL_SIZE - 12, CELL_SIZE - 12);

                    ctx.textAlign = 'center';
                    ctx.textBaseline = 'middle';
                    ctx.font = '700 8px "Fira Code", monospace';
                    ctx.fillStyle = '#F5A623';
                    ctx.fillText('⚡BRAVO', x + (CELL_SIZE / 2), y + (CELL_SIZE / 2));
                    ctx.restore();
                }
            }
        }

        // 5. Exploration Trail Breadcrumbs (For Both Robots)
        robots.forEach(bot => {
            if (bot.trail && bot.trail.length > 1) {
                ctx.save();
                ctx.strokeStyle = bot.id === 'edge-001' ? 'rgba(15, 184, 160, 0.35)' : 'rgba(245, 166, 35, 0.35)';
                ctx.lineWidth = 2;
                ctx.setLineDash([2, 4]);
                ctx.beginPath();
                ctx.moveTo(bot.trail[0].x, bot.trail[0].y);
                for (let i = 1; i < bot.trail.length; i++) {
                    ctx.lineTo(bot.trail[i].x, bot.trail[i].y);
                }
                ctx.stroke();
                ctx.setLineDash([]);

                bot.trail.forEach((pt, idx) => {
                    const alpha = (idx / bot.trail.length) * 0.6;
                    ctx.fillStyle = bot.id === 'edge-001' ? `rgba(15, 184, 160, ${alpha})` : `rgba(245, 166, 35, ${alpha})`;
                    ctx.beginPath();
                    ctx.arc(pt.x, pt.y, 2, 0, Math.PI * 2);
                    ctx.fill();
                });
                ctx.restore();
            }
        });

        // 6. Draw Planned BFS Paths (Both Robots)
        robots.forEach(bot => {
            if (bot.path && bot.path.length > 0) {
                ctx.beginPath();
                ctx.strokeStyle = bot.id === 'edge-001' ? 'rgba(15, 184, 160, 0.65)' : 'rgba(245, 166, 35, 0.65)';
                ctx.lineWidth = 3;
                ctx.setLineDash([5, 5]);
                ctx.moveTo(bot.x, bot.y);
                bot.path.forEach(pt => ctx.lineTo(pt.x, pt.y));
                ctx.stroke();
                ctx.setLineDash([]);
            }
        });

        // 7. Draw Items & Hazards
        items.forEach(item => {
            if (item.picked) return;

            if (item.isHazard) {
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

        // 8. Dynamic "Fog-of-War" Shroud Pass for Unexplored Cells
        ctx.save();
        for (let r = 0; r < ROWS; r++) {
            for (let c = 0; c < COLS; c++) {
                if (!exploredGrid[r][c]) {
                    const x = c * CELL_SIZE;
                    const y = r * CELL_SIZE;
                    ctx.fillStyle = 'rgba(10, 15, 29, 0.65)';
                    ctx.fillRect(x, y, CELL_SIZE, CELL_SIZE);
                    
                    // Subtle futuristic hatch dot
                    ctx.fillStyle = 'rgba(255, 255, 255, 0.08)';
                    ctx.beginPath();
                    ctx.arc(x + CELL_SIZE / 2, y + CELL_SIZE / 2, 1, 0, Math.PI * 2);
                    ctx.fill();
                }
            }
        }
        ctx.restore();

        // 9. Expanding Radar Waves & 3-Ring Holographic Anomaly Shockwaves
        for (let i = pulses.length - 1; i >= 0; i--) {
            const p = pulses[i];
            ctx.save();
            if (p.isAnomaly) {
                // Multi-ring holographic shockwave explosion
                p.rotation = (p.rotation || 0) + 0.04;

                // Outer primary ring
                ctx.beginPath();
                ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
                ctx.strokeStyle = '#E85D4A';
                ctx.globalAlpha = p.alpha;
                ctx.lineWidth = 3;
                ctx.stroke();

                // Middle ring
                ctx.beginPath();
                ctx.arc(p.x, p.y, Math.max(2, p.radius * 0.7), 0, Math.PI * 2);
                ctx.strokeStyle = '#FF7B69';
                ctx.globalAlpha = p.alpha * 0.8;
                ctx.lineWidth = 2;
                ctx.stroke();

                // Inner ring
                ctx.beginPath();
                ctx.arc(p.x, p.y, Math.max(1, p.radius * 0.4), 0, Math.PI * 2);
                ctx.strokeStyle = '#F5A623';
                ctx.globalAlpha = p.alpha * 0.9;
                ctx.lineWidth = 1.5;
                ctx.stroke();

                // Rotating Dashed Crosshairs
                ctx.save();
                ctx.translate(p.x, p.y);
                ctx.rotate(p.rotation);
                ctx.strokeStyle = 'rgba(232, 93, 74, ' + (p.alpha * 0.7) + ')';
                ctx.lineWidth = 1.5;
                ctx.setLineDash([4, 6]);
                ctx.beginPath();
                ctx.moveTo(-p.radius, 0); ctx.lineTo(p.radius, 0);
                ctx.moveTo(0, -p.radius); ctx.lineTo(0, p.radius);
                ctx.stroke();
                ctx.restore();

                p.radius += 2.6;
                p.alpha -= 0.02;
            } else {
                ctx.beginPath();
                ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
                ctx.strokeStyle = p.color;
                ctx.globalAlpha = p.alpha;
                ctx.lineWidth = 2.5;
                ctx.stroke();

                p.radius += 2.2;
                p.alpha -= 0.025;
            }
            ctx.restore();

            if (p.alpha <= 0 || p.radius >= p.maxRadius) {
                pulses.splice(i, 1);
            }
        }

        // 10, 11, 12. Draw Both Robots (Alpha & Bravo): Lock-On, Radar, Chassis, and Mini-HUD
        const scanDistPx = SCAN_RADIUS_CELLS * CELL_SIZE;

        robots.forEach(bot => {
            // Lock-On Laser Target Reticle for this robot
            if (bot.lockOnTarget && bot.lockOnTarget.alpha > 0) {
                ctx.save();
                ctx.strokeStyle = bot.lockOnTarget.color;
                ctx.lineWidth = 2;
                ctx.globalAlpha = bot.lockOnTarget.alpha;
                
                // Laser beam from robot to target
                ctx.beginPath();
                ctx.moveTo(bot.x, bot.y);
                ctx.lineTo(bot.lockOnTarget.x, bot.lockOnTarget.y);
                ctx.stroke();

                // Reticle circle
                ctx.beginPath();
                ctx.arc(bot.lockOnTarget.x, bot.lockOnTarget.y, 22, 0, Math.PI * 2);
                ctx.stroke();

                ctx.font = '600 10px Fira Code, monospace';
                ctx.fillStyle = bot.lockOnTarget.color;
                ctx.fillText(`LOCKED ON: ${bot.lockOnTarget.text}`, bot.lockOnTarget.x, bot.lockOnTarget.y + 32);

                ctx.restore();
                bot.lockOnTarget.alpha -= 0.015;
                if (bot.lockOnTarget.alpha <= 0) bot.lockOnTarget = null;
            }

            // Radar Scanner Cone
            ctx.save();
            ctx.beginPath();
            ctx.arc(bot.x, bot.y, scanDistPx, 0, Math.PI * 2);
            ctx.fillStyle = bot.id === 'edge-001' ? 'rgba(15, 184, 160, 0.07)' : 'rgba(245, 166, 35, 0.07)';
            ctx.fill();
            ctx.strokeStyle = bot.id === 'edge-001' ? 'rgba(15, 184, 160, 0.3)' : 'rgba(245, 166, 35, 0.3)';
            ctx.lineWidth = 1.5;
            ctx.stroke();

            ctx.beginPath();
            ctx.moveTo(bot.x, bot.y);
            ctx.arc(bot.x, bot.y, scanDistPx, bot.radarAngle - 0.4, bot.radarAngle + 0.4);
            ctx.lineTo(bot.x, bot.y);
            const grad = ctx.createRadialGradient(bot.x, bot.y, 0, bot.x, bot.y, scanDistPx);
            if (bot.id === 'edge-001') {
                grad.addColorStop(0, 'rgba(15, 184, 160, 0.45)');
                grad.addColorStop(1, 'rgba(15, 184, 160, 0.0)');
            } else {
                grad.addColorStop(0, 'rgba(245, 166, 35, 0.45)');
                grad.addColorStop(1, 'rgba(245, 166, 35, 0.0)');
            }
            ctx.fillStyle = grad;
            ctx.fill();
            ctx.restore();

            // Robot Chassis
            ctx.save();
            ctx.translate(bot.x, bot.y);
            ctx.rotate(bot.heading);

            ctx.fillStyle = '#16213E';
            ctx.beginPath();
            ctx.arc(0, 0, 13, 0, Math.PI * 2);
            ctx.fill();
            ctx.strokeStyle = bot.color;
            ctx.lineWidth = 2.5;
            ctx.stroke();

            ctx.fillStyle = bot.color;
            ctx.beginPath();
            ctx.arc(10, 0, 4, 0, Math.PI * 2);
            ctx.fill();

            for (let i = 0; i < bot.maxCargo; i++) {
                const angle = Math.PI * 0.7 + i * 0.3;
                const cx = Math.cos(angle) * 7;
                const cy = Math.sin(angle) * 7;
                ctx.fillStyle = i < bot.cargo.length ? bot.color : '#4A5568';
                ctx.beginPath();
                ctx.arc(cx, cy, 2, 0, Math.PI * 2);
                ctx.fill();
            }
            ctx.restore();

            // Floating Robot Mini-HUD
            ctx.save();
            const hudX = Math.max(70, Math.min(W - 70, bot.x));
            const hudY = Math.max(30, bot.y - 32);

            ctx.fillStyle = 'rgba(10, 15, 29, 0.92)';
            ctx.beginPath();
            ctx.roundRect(hudX - 64, hudY - 14, 128, 22, 6);
            ctx.fill();
            ctx.strokeStyle = bot.color;
            ctx.lineWidth = 1.2;
            ctx.stroke();

            // Robot identifier name tag + battery %
            const batt = Math.round(bot.battery || 100);
            ctx.font = '700 8.5px "Fira Code", monospace';
            ctx.fillStyle = bot.color;
            ctx.textAlign = 'left';
            ctx.textBaseline = 'middle';
            ctx.fillText(`${bot.name} ⚡${batt}%`, hudX - 58, hudY - 3);

            // Cargo slots representation [📦][📦][ ]
            let cargoStr = '';
            for (let i = 0; i < bot.maxCargo; i++) {
                cargoStr += i < bot.cargo.length ? '📦' : '▫️';
            }
            ctx.font = '8px Inter, sans-serif';
            ctx.textAlign = 'right';
            ctx.fillText(cargoStr, hudX + 56, hudY - 3);

            // Target / state subtitle pill
            ctx.font = '600 7.5px "Fira Code", monospace';
            ctx.fillStyle = '#94A3B8';
            ctx.textAlign = 'center';
            const targetText = bot.targetItem
                ? `TARGET: (${bot.targetItem.c},${bot.targetItem.r})`
                : (bot.state === 'DOCKING' ? 'RECHARGING' : bot.state);
            ctx.fillText(targetText, hudX, hudY + 5);

            ctx.restore();
        });

        // 13. On-Screen Status Banner overlay
        if (onScreenBanner && onScreenBanner.timer > 0) {
            ctx.save();
            const bannerY = 18;
            ctx.font = '600 12px Inter, sans-serif';
            const textWidth = ctx.measureText(onScreenBanner.text).width;
            const boxW = Math.max(340, textWidth + 36);
            const boxX = (W - boxW) / 2;

            ctx.fillStyle = '#16213E';
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

        // Reset Robot Alpha (edge-001)
        robotAlpha.x = 1.5 * CELL_SIZE;
        robotAlpha.y = 1.5 * CELL_SIZE;
        robotAlpha.cellC = 1;
        robotAlpha.cellR = 1;
        robotAlpha.heading = 0;
        robotAlpha.battery = 100;
        robotAlpha.cargo = [];
        robotAlpha.trail = [];
        robotAlpha.state = isRunning ? 'PATROLLING' : 'STANDBY';
        robotAlpha.path = [];
        robotAlpha.targetItem = null;
        robotAlpha.lockOnTarget = null;

        // Reset Robot Bravo (edge-002)
        robotBravo.x = 20.5 * CELL_SIZE;
        robotBravo.y = 1.5 * CELL_SIZE;
        robotBravo.cellC = 20;
        robotBravo.cellR = 1;
        robotBravo.heading = Math.PI;
        robotBravo.battery = 100;
        robotBravo.cargo = [];
        robotBravo.trail = [];
        robotBravo.state = isRunning ? 'PATROLLING' : 'STANDBY';
        robotBravo.path = [];
        robotBravo.targetItem = null;
        robotBravo.lockOnTarget = null;

        lastVectorMatch = null;
        savedToQdrantCount = 0;

        // Reset explored & heatmap matrices
        exploredGrid = Array.from({ length: ROWS }, () => Array(COLS).fill(false));
        visitHeatmap = Array.from({ length: ROWS }, () => Array(COLS).fill(0));

        // Uncover initial dock regions for both robots
        for (let r = 0; r <= 3; r++) {
            for (let c = 0; c <= 3; c++) {
                exploredGrid[r][c] = true;
            }
            for (let c = 18; c <= 21; c++) {
                exploredGrid[r][c] = true;
            }
        }

        spawnRoutineItems(7);
        logSim('Multi-Robot Fleet initialized: Alpha (edge-001) in Zone A/B, Bravo (edge-002) in Zone C.', 'info');
        renderLanes();
        updatePills();
    }

    function toggleMission() {
        isRunning = !isRunning;
        const btn = document.getElementById('btn-sim-toggle');
        if (btn) {
            if (isRunning) {
                btn.textContent = '⏸ Pause Fleet Mission';
                btn.style.background = '#E85D4A';
                robots.forEach(r => r.state = 'PATROLLING');
                logSim('▶ Fleet Mission Active: Alpha (edge-001) & Bravo (edge-002) patrol and edge sync active.', 'success');
            } else {
                btn.textContent = '▶ Start Fleet Mission';
                btn.style.background = '#0FB8A0';
                robots.forEach(r => r.state = 'STANDBY');
                logSim('⏸ Fleet Mission Paused: Both robots held at current positions.', 'info');
            }
        }
        updatePills();
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  MAIN ANIMATION LOOP (60 FPS)
    // ═══════════════════════════════════════════════════════════════════════
    function loop() {
        updateRobots();
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

        const btnHeatmap = document.getElementById('btn-sim-heatmap');
        if (btnHeatmap) {
            btnHeatmap.addEventListener('click', () => {
                showHeatmap = !showHeatmap;
                btnHeatmap.style.background = showHeatmap ? '#E85D4A' : '#4A5568';
                showSimToast(`Aisle Heatmap: ${showHeatmap ? 'ENABLED' : 'DISABLED'}`, 'info');
            });
        }

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
