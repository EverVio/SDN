/* ===================================================================
   NOC // SDN Operations Console — Frontend Logic (Luxury Modern)
   =================================================================== */

let cy = null;
let socket = null;
let linkMap = {};
let edgeUtil = {};
let aggNodes = [];
let weightBadges = {};
let running = false;
let experimentDuration = 60;
let progressTimer = null;
let startTime = null;

// Premium Color Palette matching CSS
const COL = {
    accent: '#00f0ff',
    accentDim: '#4488ff',
    green: '#00ff88',
    amber: '#ffb300',
    red: '#ff3355',
    textMain: '#f1f5f9',
    textMuted: '#94a3b8',
    bgNodeCore: 'rgba(255, 51, 85, 0.1)',
    bgNodeAgg: 'rgba(0, 240, 255, 0.1)',
    bgNodeEdge: 'rgba(0, 255, 136, 0.1)',
    bgNodeHost: 'rgba(255, 255, 255, 0.05)',
    borderCore: '#ff3355',
    borderAgg: '#00f0ff',
    borderEdge: '#00ff88',
    borderHost: '#64748b',
    edgeIdle: 'rgba(255, 255, 255, 0.15)',
    edgeAggIdle: 'rgba(255, 255, 255, 0.25)',
    edgeCoreIdle: 'rgba(255, 255, 255, 0.35)',
};

function utilColor(u) {
    if (u < 0.3) return COL.green;
    if (u < 0.7) return COL.amber;
    return COL.red;
}

function updateClock() {
    const el = document.getElementById('clockDisplay');
    if (!el) return;
    const now = new Date();
    const h = String(now.getHours()).padStart(2, '0');
    const m = String(now.getMinutes()).padStart(2, '0');
    const s = String(now.getSeconds()).padStart(2, '0');
    el.textContent = `${h}:${m}:${s}`;
}

document.addEventListener('DOMContentLoaded', async () => {
    updateClock();
    setInterval(updateClock, 1000);

    initSocket();
    await initTopology();
    initControls();
    updateCanvasStatus();

    addLog('System UI initialized (Luxury Modern V2).', 'info');
    addLog('Awaiting input...', '');
});

function initSocket() {
    socket = io();

    socket.on('connect', () => { addLog('Uplink established.', 'info'); });
    socket.on('disconnect', () => { addLog('Uplink disconnected.', 'error'); });
    socket.on('update_util', (data) => handleUtilUpdate(data));
    socket.on('update_weights', (data) => handleWeightUpdate(data));
    socket.on('progress', (data) => updateProgress(data.elapsed, data.duration));
    socket.on('experiment_log', (data) => { if (data.line) addLog(data.line); });
    socket.on('experiment_complete', (data) => onExperimentComplete(data));
    socket.on('experiment_results', (data) => showResults(data));
    socket.on('status_update', (data) => {
        if (data.running !== undefined) setRunningState(data.running, data.group);
    });
}

async function initTopology() {
    const resp = await fetch('/api/topology');
    const topo = await resp.json();

    linkMap = topo.linkMap || {};
    aggNodes = topo.nodes.filter(n => n.data.type === 'aggregation').map(n => n.data.id);

    const elements = [];

    for (const node of topo.nodes) {
        const d = node.data;
        elements.push({
            group: 'nodes',
            data: {
                id: d.id,
                label: d.label,
                nodeType: d.type,
                dpid: d.dpid || null,
                weightText: d.weightText || '',
                edgeDpid: d.edgeDpid || null,
            },
            position: node.position,
            classes: d.type,
            selectable: false,
            locked: false,
        });
    }

    for (const edge of topo.edges) {
        const d = edge.data;
        elements.push({
            group: 'edges',
            data: {
                id: d.id,
                source: d.source,
                target: d.target,
                edgeType: d.type,
                bandwidth: d.bandwidth,
                utilization: 0,
            },
            classes: d.type,
            selectable: false,
        });
    }

    // Advanced, refined Cytoscape styling
    cy = cytoscape({
        container: document.getElementById('cy'),
        elements: elements,
        layout: { name: 'preset' },
        userZoomingEnabled: true,
        userPanningEnabled: true,
        boxSelectionEnabled: false,
        autoungrabify: false,
        style: [
            {
                selector: 'node',
                style: {
                    'color': COL.textMain,
                    'font-family': 'JetBrains Mono, monospace',
                    'text-valign': 'bottom',
                    'text-halign': 'center',
                    'text-margin-y': 6,
                    'font-size': '12px',
                    'font-weight': '500',
                    'text-outline-width': 2,
                    'text-outline-color': '#0B0F19',
                    'text-background-color': 'transparent',
                    'text-background-opacity': 0,
                }
            },
            {
                selector: 'node.core',
                style: {
                    'shape': 'round-rectangle',
                    'width': 50,
                    'height': 50,
                    'background-color': COL.bgNodeCore,
                    'background-image': "data:image/svg+xml;utf8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='50' height='50' viewBox='0 0 50 50'%3E%3Crect x='15' y='15' width='20' height='20' fill='none' stroke='%23ff3355' stroke-width='2'/%3E%3Ccircle cx='25' cy='25' r='4' fill='%23ff3355'/%3E%3C/svg%3E",
                    'border-width': 2,
                    'border-color': COL.borderCore,
                    'label': 'data(label)',
                    'shadow-blur': 15,
                    'shadow-color': 'rgba(255, 51, 85, 0.4)',
                },
            },
            {
                selector: 'node.aggregation',
                style: {
                    'shape': 'ellipse',
                    'width': 42,
                    'height': 42,
                    'background-color': COL.bgNodeAgg,
                    'background-image': "data:image/svg+xml;utf8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='42' height='42' viewBox='0 0 42 42'%3E%3Ccircle cx='21' cy='21' r='10' fill='none' stroke='%2300f0ff' stroke-width='1.5'/%3E%3Ccircle cx='21' cy='21' r='3' fill='%2300f0ff'/%3E%3C/svg%3E",
                    'border-width': 2,
                    'border-color': COL.borderAgg,
                    'label': 'data(label)',
                    'shadow-blur': 12,
                    'shadow-color': 'rgba(0, 240, 255, 0.3)',
                },
            },
            {
                selector: 'node.edge',
                style: {
                    'shape': 'ellipse',
                    'width': 36,
                    'height': 36,
                    'background-color': COL.bgNodeEdge,
                    'background-image': "data:image/svg+xml;utf8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='36' height='36' viewBox='0 0 36 36'%3E%3Cpath d='M18 10 L26 24 L10 24 Z' fill='none' stroke='%2300ff88' stroke-width='1.5'/%3E%3Ccircle cx='18' cy='18' r='2' fill='%2300ff88'/%3E%3C/svg%3E",
                    'border-width': 2,
                    'border-color': COL.borderEdge,
                    'label': 'data(label)',
                    'shadow-blur': 10,
                    'shadow-color': 'rgba(0, 255, 136, 0.3)',
                },
            },
            {
                selector: 'node.host',
                style: {
                    'shape': 'round-rectangle',
                    'width': 56,
                    'height': 20,
                    'background-color': COL.bgNodeHost,
                    'background-image': "data:image/svg+xml;utf8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='56' height='20' viewBox='0 0 56 20'%3E%3Cline x1='15' y1='7' x2='41' y2='7' stroke='%2364748b' stroke-width='1'/%3E%3Cline x1='15' y1='13' x2='41' y2='13' stroke='%2364748b' stroke-width='1'/%3E%3C/svg%3E",
                    'border-width': 1.5,
                    'border-color': COL.borderHost,
                    'label': 'data(label)',
                    'font-size': '10px',
                    'color': COL.textMuted,
                    'text-margin-y': 4,
                },
            },
            {
                selector: 'edge.agg-core',
                style: {
                    'width': 2.5,
                    'line-color': COL.edgeCoreIdle,
                    'curve-style': 'bezier',
                    'opacity': 0.6,
                    'line-style': 'dashed',
                    'line-dash-pattern': [4, 8],
                },
            },
            {
                selector: 'edge.edge-agg',
                style: {
                    'width': 2.0,
                    'line-color': COL.edgeAggIdle,
                    'curve-style': 'bezier',
                    'opacity': 0.5,
                    'line-style': 'dashed',
                    'line-dash-pattern': [4, 6],
                },
            },
            {
                selector: 'edge.host-edge',
                style: {
                    'width': 1.5,
                    'line-color': COL.edgeIdle,
                    'curve-style': 'bezier',
                    'opacity': 0.4,
                    'line-style': 'dashed',
                    'line-dash-pattern': [3, 5],
                },
            },
            {
                selector: '.faded',
                style: {
                    'opacity': 0.05,
                    'text-opacity': 0
                }
            },
            {
                selector: 'node.highlight',
                style: {
                    'border-width': 3,
                    'border-color': '#fff',
                    'shadow-blur': 25,
                    'shadow-color': '#fff',
                }
            },
            {
                selector: 'edge.highlight-edge',
                style: {
                    'shadow-blur': 15,
                    'shadow-color': '#fff',
                    'line-color': '#fff',
                    'opacity': 1,
                    'width': 3,
                }
            }
        ],
    });

    cy.on('zoom pan', () => updateCanvasStatus());

    cy.ready(() => {
        cy.fit(60); 
        createWeightBadges();
        updateCanvasStatus();
        requestAnimationFrame(animateEdges); // Start animation loop
    });

    // Hover logic for neighborhood highlighting
    cy.on('mouseover', 'node', function(e){
        var node = e.target;
        var neighborhood = node.neighborhood().add(node);
        cy.elements().addClass('faded');
        neighborhood.removeClass('faded');
        node.addClass('highlight');
        node.connectedEdges().addClass('highlight-edge');
    });

    cy.on('mouseout', 'node', function(e){
        cy.elements().removeClass('faded highlight highlight-edge');
    });

    cy.on('mouseover', 'edge', function(e){
        e.target.addClass('highlight-edge');
    });

    cy.on('mouseout', 'edge', function(e){
        e.target.removeClass('highlight-edge');
    });

    cy.on('zoom pan', updateBadgePositions);
}

function updateCanvasStatus() {
    if (!cy) return;
    const zoomEl = document.getElementById('canvasZoom');
    if (zoomEl) zoomEl.textContent = `Zoom: ${Math.round(cy.zoom() * 100)}%`;
}

function createWeightBadges() {
    const container = document.getElementById('weightOverlays');
    container.innerHTML = '';
    weightBadges = {};

    for (const nodeId of aggNodes) {
        const node = cy.$('#' + nodeId);
        if (node.length === 0) continue;

        const dpid = node.data('dpid');
        const badge = document.createElement('div');
        badge.className = 'weight-badge';
        badge.textContent = '50:50';
        badge.dataset.dpid = dpid;
        container.appendChild(badge);
        weightBadges[dpid] = badge;
    }

    updateBadgePositions();
}

function updateBadgePositions() {
    if (!cy) return;
    const zoom = cy.zoom();

    for (const nodeId of aggNodes) {
        const node = cy.$('#' + nodeId);
        if (node.length === 0) continue;

        const dpid = node.data('dpid');
        const badge = weightBadges[dpid];
        if (!badge) continue;

        const pos = node.renderedPosition();
        badge.style.left = pos.x + 'px';
        badge.style.top = (pos.y + 34 * zoom) + 'px';
        badge.style.fontSize = Math.max(9, 11 * zoom) + 'px';
        badge.style.transform = `translate(-50%, 0) scale(${Math.min(1.5, Math.max(0.7, zoom))})`;
    }
}

function handleUtilUpdate(data) {
    const edgeMax = {};

    for (const [key, util] of Object.entries(data)) {
        const edgeId = linkMap[key];
        if (!edgeId) continue;
        if (!(edgeId in edgeMax) || util > edgeMax[edgeId]) {
            edgeMax[edgeId] = util;
        }
    }

    for (const [edgeId, util] of Object.entries(edgeMax)) {
        edgeUtil[edgeId] = util;
        const edge = cy.$('#' + edgeId);
        if (edge.length === 0) continue;

        const color = utilColor(util);
        const width = edge.hasClass('agg-core') ? 2.5 + util * 3 : (edge.hasClass('edge-agg') ? 2.0 + util * 2.5 : 1.5 + util * 2);
        const opacity = 0.6 + util * 0.4;

        edge.style({
            'line-color': color,
            'width': width,
            'opacity': opacity,
            'shadow-blur': util > 0.4 ? 12 + (util*10) : 0,
            'shadow-color': color,
        });
    }

    cy.edges().forEach(edge => {
        const id = edge.id();
        if (!(id in edgeMax)) {
            let idleColor = COL.edgeIdle;
            let idleWidth = 1.5;
            let idleOp = 0.4;
            if (edge.hasClass('agg-core')) { idleColor = COL.edgeCoreIdle; idleWidth = 2.5; idleOp = 0.6; }
            else if (edge.hasClass('edge-agg')) { idleColor = COL.edgeAggIdle; idleWidth = 2.0; idleOp = 0.5; }
            edge.style({
                'line-color': idleColor,
                'width': idleWidth,
                'opacity': idleOp,
                'shadow-blur': 0,
            });
        }
    });
}

function handleWeightUpdate(data) {
    for (const [dpidStr, weights] of Object.entries(data)) {
        const dpid = parseInt(dpidStr);
        const badge = weightBadges[dpid];
        if (!badge) continue;

        const w3 = weights.port3_weight;
        const w4 = weights.port4_weight;
        const total = w3 + w4;
        const p3 = total > 0 ? Math.round(w3 / total * 100) : 50;
        const p4 = 100 - p3;

        badge.textContent = `P3:${w3} P4:${w4} [${p3}%]`;
        badge.classList.add('changed');
        setTimeout(() => badge.classList.remove('changed'), 600);
    }
}

// 对数映射转换函数：滑块值 [0, 1000] -> 实际秒数 [30, 600]
function sliderToValue(x) {
    const yMin = 30;
    const yMax = 600;
    const val = yMin * Math.pow(yMax / yMin, x / 1000);
    return Math.round(val);
}

// 逆向对数映射：实际秒数 [30, 600] -> 滑块值 [0, 1000]
function valueToSlider(y) {
    const yMin = 30;
    const yMax = 600;
    if (y < yMin) return 0;
    if (y > yMax) return 1000;
    const x = 1000 * Math.log(y / yMin) / Math.log(yMax / yMin);
    return Math.round(x);
}

// 格式化显示时长，自动切换单位
function formatDuration(seconds) {
    if (seconds < 60) {
        return { value: seconds, unit: 'sec' };
    } else {
        const m = Math.floor(seconds / 60);
        const s = seconds % 60;
        const sStr = String(s).padStart(2, '0');
        return { value: `${m}:${sStr}`, unit: 'min' };
    }
}

function initControls() {
    const slider = document.getElementById('durationSlider');
    const valueEl = document.getElementById('durationValue');
    const unitEl = document.querySelector('.duration-unit');

    // 默认测试时长设为 60 秒
    experimentDuration = 60;

    // 根据默认时长初始化滑块位置与显示
    const initialSliderVal = valueToSlider(experimentDuration);
    slider.value = initialSliderVal;

    const initialFormat = formatDuration(experimentDuration);
    valueEl.textContent = initialFormat.value;
    if (unitEl) unitEl.textContent = initialFormat.unit;

    slider.addEventListener('input', () => {
        const x = parseInt(slider.value);
        const val = sliderToValue(x);
        experimentDuration = val;

        const format = formatDuration(val);
        valueEl.textContent = format.value;
        if (unitEl) unitEl.textContent = format.unit;
    });
}

async function startExperiment() {
    const group = document.getElementById('groupSelect').value;
    const duration = experimentDuration;
    
    addLog(`INIT EXECUTING POLICY: ${group.toUpperCase()} / ${duration}s`, 'info');
    setRunningState(true, group);

    try {
        const resp = await fetch('/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ group, duration }),
        });
        const data = await resp.json();

        if (!resp.ok) {
            addLog(`ABORTED: ${data.error}`, 'error');
            setRunningState(false);
            return;
        }

        addLog(`RUNTIME ACTIVE: ${group.toUpperCase()}`, 'info');
    } catch (err) {
        addLog(`LINK FAIL: ${err.message}`, 'error');
        setRunningState(false);
    }
}

async function stopExperiment() {
    addLog('REQUESTING ABORT...', 'warn');

    try {
        const resp = await fetch('/stop', { method: 'POST' });
        const data = await resp.json();

        if (!resp.ok) {
            addLog(`ABORT FAILED: ${data.error}`, 'error');
            return;
        }

        addLog('EXECUTION TERMINATED.', 'warn');
    } catch (err) {
        addLog(`ABORT ERROR: ${err.message}`, 'error');
    }

    setRunningState(false);
}

function setRunningState(isRunning, group) {
    running = isRunning;
    const chip = document.getElementById('hudConsole'); // 绑定到新的 HUD 浮空指示层
    const text = document.getElementById('statusText');
    const code = document.getElementById('statusCode');
    const btnStart = document.getElementById('btnStart');
    const btnStop = document.getElementById('btnStop');
    const progressSection = document.getElementById('hudProgressSection'); // 绑定到新的 HUD 进度容器

    if (isRunning) {
        if (chip) chip.className = 'hud-console running initializing';
        if (text) text.textContent = 'Initializing';
        if (code) code.textContent = '01';
        btnStart.disabled = true;
        btnStop.disabled = false;
        if (progressSection) progressSection.style.display = 'flex';

        // 瞬间切换到本地加载反馈
        const track = document.querySelector('.progress-track');
        if (track) track.classList.add('initializing');
        const bar = document.getElementById('progressBar');
        if (bar) bar.style.width = '100%';
        const elapsedEl = document.getElementById('progressElapsed');
        const remainingEl = document.getElementById('progressRemaining');
        const pctEl = document.getElementById('progressPct');
        if (elapsedEl) elapsedEl.textContent = '00:00';
        if (remainingEl) remainingEl.textContent = 'Establishing OpenFlow...';
        if (pctEl) pctEl.textContent = 'INIT';
    } else {
        if (chip) chip.className = 'hud-console';
        if (text) text.textContent = 'System Standby';
        if (code) code.textContent = '00';
        btnStart.disabled = false;
        btnStop.disabled = true;
        if (progressSection) progressSection.style.display = 'none';

        const track = document.querySelector('.progress-track');
        if (track) track.classList.remove('initializing');
    }
}

function updateProgress(elapsed, duration) {
    const bar = document.getElementById('progressBar');
    const track = document.querySelector('.progress-track');
    const elapsedEl = document.getElementById('progressElapsed');
    const remainingEl = document.getElementById('progressRemaining');
    const pctEl = document.getElementById('progressPct');
    const statusText = document.getElementById('statusText');
    const chip = document.getElementById('hudConsole');

    if (elapsed === 0) {
        if (chip) chip.className = 'hud-console running initializing';
        if (track) track.classList.add('initializing');
        if (bar) bar.style.width = '100%';
        if (elapsedEl) elapsedEl.textContent = '00:00';
        if (remainingEl) remainingEl.textContent = 'Establishing OpenFlow...';
        if (pctEl) pctEl.textContent = 'INIT';
        if (statusText) statusText.textContent = 'Initializing';
    } else {
        if (chip) chip.className = 'hud-console running';
        if (track) track.classList.remove('initializing');
        const pct = Math.min(100, (elapsed / duration) * 100);
        if (bar) bar.style.width = pct + '%';

        const mins = Math.floor(elapsed / 60);
        const secs = Math.floor(elapsed % 60);
        if (elapsedEl) elapsedEl.textContent = `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;

        const rem = Math.max(0, duration - elapsed);
        if (remainingEl) remainingEl.textContent = `Remaining: ${Math.ceil(rem)}s`;
        if (pctEl) pctEl.textContent = `${Math.floor(pct)}%`;
        if (statusText && running) statusText.textContent = 'Active Engine';
    }
}

function onExperimentComplete(data) {
    setRunningState(false);

    if (data.status === 'success') {
        addLog(`TASK COMPLETE IN ${Math.floor(data.elapsed)}s`, 'info');
    } else {
        addLog(`TASK FAILED. EXIT CODE ${data.exit_code}`, 'error');
    }
}

function showResults(data) {
    const modal = document.getElementById('resultsModal');
    const title = document.getElementById('modalTitle');
    const body = document.getElementById('modalBody');

    const groupNames = {
        'l2': 'L2 Baseline ECMP',
        'threshold': 'Threshold Active',
        'predictive': 'MLP Predictive',
    };

    title.textContent = `${groupNames[data.group] || data.group} // REPORT`;

    let html = '<table class="results-table">';
    html += '<thead><tr>';
    html += '<th>Flow</th><th>Loss (%)</th><th>Jitter (ms)</th><th>BW (Mbps)</th>';
    html += '</tr></thead><tbody>';

    for (const row of data.results) {
        const loss = parseFloat(row.avg_loss_pct);
        const jitter = parseFloat(row.avg_jitter_ms);
        const bw = parseFloat(row.avg_bandwidth_mbps);

        const lossClass = loss > 5 ? 'metric-bad' : loss > 0 ? 'metric-warn' : 'metric-good';
        const jitterClass = jitter > 5 ? 'metric-bad' : jitter > 1 ? 'metric-warn' : 'metric-good';

        html += '<tr>';
        html += `<td><span style="opacity: 0.7; font-size: 11px;">#</span> ${row.flow}</td>`;
        html += `<td class="${lossClass}">${loss.toFixed(2)}%</td>`;
        html += `<td class="${jitterClass}">${jitter.toFixed(3)}</td>`;
        html += `<td>${bw.toFixed(2)}</td>`;
        html += '</tr>';
    }

    html += '</tbody></table>';
    body.innerHTML = html;
    modal.style.display = 'flex';
}

function closeModal() {
    document.getElementById('resultsModal').style.display = 'none';
}

function resetHighlight() {
    if (!cy) return;

    cy.edges().forEach(edge => {
        let idleColor = COL.edgeIdle;
        let idleWidth = 1.5;
        let idleOp = 0.4;
        if (edge.hasClass('agg-core')) { idleColor = COL.edgeCoreIdle; idleWidth = 2.5; idleOp = 0.6; }
        else if (edge.hasClass('edge-agg')) { idleColor = COL.edgeAggIdle; idleWidth = 2.0; idleOp = 0.5; }
        edge.style({
            'line-color': idleColor,
            'width': idleWidth,
            'opacity': idleOp,
            'shadow-blur': 0,
        });
    });

    for (const badge of Object.values(weightBadges)) {
        badge.textContent = '50:50';
        badge.classList.remove('changed');
    }

    edgeUtil = {};
    addLog('Canvas visual state reset.', '');
}

function addLog(msg, cls) {
    const area = document.getElementById('logArea');
    const line = document.createElement('div');
    
    let finalCls = cls || '';
    if (msg.startsWith('>>>') || msg.includes('[SYSTEM]')) {
        finalCls = finalCls ? `${finalCls} system` : 'system';
    }
    line.className = 'log-line' + (finalCls ? ` ${finalCls}` : '');

    const now = new Date();
    const ts = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;

    const tsSpan = document.createElement('span');
    tsSpan.className = 'log-ts';
    tsSpan.textContent = `[${ts}]`;

    line.appendChild(tsSpan);
    line.appendChild(document.createTextNode(msg));

    area.appendChild(line);
    area.scrollTop = area.scrollHeight;

    while (area.children.length > 200) {
        area.removeChild(area.firstChild);
    }
}

function clearLog() {
    document.getElementById('logArea').innerHTML = '';
    addLog('Terminal cleared.', '');
}

// Data flow animation
let lastTime = 0;
function animateEdges(time) {
    requestAnimationFrame(animateEdges);
    let dt = time - lastTime;
    if (dt < 40) return; // Limit ~25fps for performance
    lastTime = time;

    cy.batch(() => {
        cy.edges().forEach(edge => {
            const util = edgeUtil[edge.id()] || 0;
            // Only animate edges with active utilization to save performance
            if (util > 0.05) {
                let speed = Math.max(1, util * 8); // Speed scales with utilization
                let currentOffset = edge.numericStyle('line-dash-offset') || 0;
                edge.style('line-dash-offset', currentOffset - speed);
            }
        });
    });
}