/* ===================================================================
   NOC // SDN Operations Console — Frontend Logic (Optimized Layout)
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

const COL = {
    accent: '#00f0ff',
    accentDim: '#009ba3',
    accentBright: '#66f7ff',
    green: '#00ff88',
    greenDim: '#00cc70',
    amber: '#ffb300',
    amberDim: '#d49500',
    red: '#ff3355',
    redDim: '#d62443',
    blue: '#4488ff',
    blueDim: '#3575e6',
    textPrimary: '#f1f5f9',
    textDim: '#64748b',
    bgNode: '#121c2a',
    bgNodeCore: '#1a0e14',
    edgeIdle: '#1a2636',
    edgeAggIdle: '#1e2e40',
    edgeCoreIdle: '#261a22',
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

    addLog('控制台已初始化', 'info');
    addLog('等待操作输入…', '');
});

function initSocket() {
    socket = io();

    socket.on('connect', () => { addLog('上行链路已建立', 'info'); });
    socket.on('disconnect', () => { addLog('上行链路已断开', 'error'); });
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
                selector: 'node.core',
                style: {
                    'shape': 'rectangle',
                    'width': 56,
                    'height': 56,
                    'background-color': COL.bgNodeCore,
                    'border-width': 2.5,
                    'border-color': COL.red,
                    'label': 'data(label)',
                    'text-wrap': 'wrap',
                    'text-valign': 'center',
                    'text-halign': 'center',
                    'font-size': '16px', /* 提升字号（原本 9px） */
                    'font-family': 'Share Tech Mono, monospace',
                    'font-weight': '600',
                    'color': COL.textPrimary,
                    'text-outline-width': 2,
                    'text-outline-color': COL.bgNodeCore,
                    'background-opacity': 0.9,
                    'shadow-blur': 16,
                    'shadow-color': 'rgba(255,51,85,0.25)',
                },
            },
            {
                selector: 'node.aggregation',
                style: {
                    'shape': 'ellipse',
                    'width': 48,
                    'height': 48,
                    'background-color': COL.bgNode,
                    'border-width': 2.5,
                    'border-color': COL.blue,
                    'label': 'data(label)',
                    'text-wrap': 'wrap',
                    'text-valign': 'center',
                    'text-halign': 'center',
                    'font-size': '14px',
                    'font-family': 'Share Tech Mono, monospace',
                    'font-weight': '600',
                    'color': COL.textPrimary,
                    'text-outline-width': 2,
                    'text-outline-color': COL.bgNode,
                    'background-opacity': 0.9,
                    'shadow-blur': 12,
                    'shadow-color': 'rgba(68,136,255,0.2)',
                },
            },
            {
                selector: 'node.edge',
                style: {
                    'shape': 'ellipse',
                    'width': 42,
                    'height': 42,
                    'background-color': COL.bgNode,
                    'border-width': 2,
                    'border-color': COL.green,
                    'label': 'data(label)',
                    'text-wrap': 'wrap',
                    'text-valign': 'center',
                    'text-halign': 'center',
                    'font-size': '14px', /* 提升字号（原本 9px） */
                    'font-family': 'Share Tech Mono, monospace',
                    'font-weight': '600',
                    'color': COL.textPrimary,
                    'text-outline-width': 2,
                    'text-outline-color': COL.bgNode,
                    'background-opacity': 0.9,
                    'shadow-blur': 10,
                    'shadow-color': 'rgba(0,255,136,0.15)',
                },
            },
            {
                selector: 'node.host',
                style: {
                    'shape': 'round-rectangle',
                    'width': 68,
                    'height': 24,
                    'background-color': '#101820',
                    'border-width': 1.5,
                    'border-color': '#3a4d64',
                    'label': 'data(label)',
                    'text-wrap': 'none',
                    'text-valign': 'center',
                    'text-halign': 'center',
                    'font-size': '11px',
                    'font-family': 'Share Tech Mono, monospace',
                    'font-weight': '500',
                    'color': COL.textDim,
                    'text-outline-width': 1,
                    'text-outline-color': '#101820',
                    'background-opacity': 0.85,
                },
            },
            {
                selector: 'edge.agg-core',
                style: {
                    'width': 2.0,
                    'line-color': COL.edgeCoreIdle,
                    'curve-style': 'bezier',
                    'opacity': 0.7,
                },
            },
            {
                selector: 'edge.edge-agg',
                style: {
                    'width': 1.6,
                    'line-color': COL.edgeAggIdle,
                    'curve-style': 'bezier',
                    'opacity': 0.6,
                },
            },
            {
                selector: 'edge.host-edge',
                style: {
                    'width': 1.2,
                    'line-color': COL.edgeIdle,
                    'curve-style': 'bezier',
                    'opacity': 0.45,
                },
            },
        ],
    });

    cy.on('zoom pan', () => updateCanvasStatus());

    cy.ready(() => {
        cy.fit(60); 
        createWeightBadges();
        updateCanvasStatus();
    });

    cy.on('zoom pan', updateBadgePositions);
}

function updateCanvasStatus() {
    if (!cy) return;
    const zoomEl = document.getElementById('canvasZoom');
    if (zoomEl) zoomEl.textContent = `缩放：${Math.round(cy.zoom() * 100)}%`;
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
        const x = pos.x;
        const y = pos.y;

        badge.style.left = x + 'px';
        badge.style.top = (y + 38 * zoom) + 'px';
        badge.style.fontSize = Math.max(10, 12 * zoom) + 'px';
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
        const width = 1.5 + util * 4.0;
        const opacity = 0.5 + util * 0.5;

        edge.style({
            'line-color': color,
            'width': width,
            'opacity': opacity,
            'shadow-blur': util > 0.5 ? 10 : 0,
            'shadow-color': color,
        });
    }

    cy.edges().forEach(edge => {
        const id = edge.id();
        if (!(id in edgeMax)) {
            let idleColor = COL.edgeIdle;
            let idleWidth = 1.2;
            if (edge.hasClass('agg-core')) { idleColor = COL.edgeCoreIdle; idleWidth = 2.0; }
            else if (edge.hasClass('edge-agg')) { idleColor = COL.edgeAggIdle; idleWidth = 1.6; }
            edge.style({
                'line-color': idleColor,
                'width': idleWidth,
                'opacity': edge.hasClass('host-edge') ? 0.45 : 0.6,
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

        badge.textContent = `端口3:${w3} 端口4:${w4} [${p3}/${p4}]`;
        badge.classList.add('changed');
        setTimeout(() => badge.classList.remove('changed'), 600);
    }
}

function initControls() {
    const slider = document.getElementById('durationSlider');
    const valueEl = document.getElementById('durationValue');

    slider.addEventListener('input', () => {
        const val = parseInt(slider.value);
        valueEl.textContent = val;
        experimentDuration = val;
    });
}

async function startExperiment() {
    const group = document.getElementById('groupSelect').value;
    const duration = experimentDuration;
    const groupNames = { l2: 'L2 基线 ECMP', threshold: '阈值响应', predictive: 'MLP 预测式' };

    addLog(`开始执行：${groupNames[group]} / ${duration}秒`, 'info');
    setRunningState(true, group);

    try {
        const resp = await fetch('/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ group, duration }),
        });
        const data = await resp.json();

        if (!resp.ok) {
            addLog(`执行中止：${data.error}`, 'error');
            setRunningState(false);
            return;
        }

        addLog(`实验运行中：${groupNames[group]}`, 'info');
    } catch (err) {
        addLog(`链路异常：${err.message}`, 'error');
        setRunningState(false);
    }
}

async function stopExperiment() {
    addLog('已请求中止', 'warn');

    try {
        const resp = await fetch('/stop', { method: 'POST' });
        const data = await resp.json();

        if (!resp.ok) {
            addLog(`中止失败：${data.error}`, 'error');
            return;
        }

        addLog('实验已终止', 'warn');
    } catch (err) {
        addLog(`中止错误：${err.message}`, 'error');
    }

    setRunningState(false);
}

function setRunningState(isRunning, group) {
    running = isRunning;
    const chip = document.getElementById('statusChip');
    const text = document.getElementById('statusText');
    const code = document.getElementById('statusCode');
    const btnStart = document.getElementById('btnStart');
    const btnStop = document.getElementById('btnStop');
    const progressSection = document.getElementById('progressSection');

    if (isRunning) {
        chip.className = 'status-chip running';
        text.textContent = '运行中';
        code.textContent = '01';
        btnStart.disabled = true;
        btnStop.disabled = false;
        progressSection.style.display = 'block';
        startTime = Date.now();
    } else {
        chip.className = 'status-chip';
        text.textContent = '待机';
        code.textContent = '00';
        btnStart.disabled = false;
        btnStop.disabled = true;
        progressSection.style.display = 'none';
        startTime = null;

        if (progressTimer) {
            clearInterval(progressTimer);
            progressTimer = null;
        }
    }
}

function updateProgress(elapsed, duration) {
    const bar = document.getElementById('progressBar');
    const elapsedEl = document.getElementById('progressElapsed');
    const remainingEl = document.getElementById('progressRemaining');
    const pctEl = document.getElementById('progressPct');

    const pct = Math.min(100, (elapsed / duration) * 100);
    bar.style.width = pct + '%';

    const mins = Math.floor(elapsed / 60);
    const secs = Math.floor(elapsed % 60);
    elapsedEl.textContent = `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;

    const rem = Math.max(0, duration - elapsed);
    remainingEl.textContent = `剩余：${Math.ceil(rem)}秒`;
    pctEl.textContent = `${Math.floor(pct)}%`;
}

function onExperimentComplete(data) {
    setRunningState(false);

    if (data.status === 'success') {
        addLog(`完成于 ${Math.floor(data.elapsed)}秒`, 'info');
    } else {
        addLog(`失败，退出码 ${data.exit_code}`, 'error');
    }
}

function showResults(data) {
    const modal = document.getElementById('resultsModal');
    const title = document.getElementById('modalTitle');
    const body = document.getElementById('modalBody');

    const groupNames = {
        'l2': 'L2 基线 ECMP',
        'threshold': '阈值响应',
        'predictive': 'MLP 预测式',
    };

    title.textContent = `${groupNames[data.group] || data.group} // 报告`;

    let html = '<table class="results-table">';
    html += '<thead><tr>';
    html += '<th>流</th><th>丢包率 %</th><th>抖动（毫秒）</th><th>带宽（Mbps）</th>';
    html += '</tr></thead><tbody>';

    for (const row of data.results) {
        const loss = parseFloat(row.avg_loss_pct);
        const jitter = parseFloat(row.avg_jitter_ms);
        const bw = parseFloat(row.avg_bandwidth_mbps);

        const lossClass = loss > 5 ? 'metric-bad' : loss > 0 ? 'metric-warn' : 'metric-good';
        const jitterClass = jitter > 5 ? 'metric-bad' : jitter > 1 ? 'metric-warn' : 'metric-good';

        html += '<tr>';
        html += `<td>${row.flow}</td>`;
        html += `<td class="${lossClass}">${loss.toFixed(2)}%</td>`;
        html += `<td class="${jitterClass}">${jitter.toFixed(3)} ms</td>`;
        html += `<td>${bw.toFixed(2)} Mbps</td>`;
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
        let idleWidth = 1.2;
        let idleOpacity = 0.45;
        if (edge.hasClass('agg-core')) { idleColor = COL.edgeCoreIdle; idleWidth = 2.0; idleOpacity = 0.6; }
        else if (edge.hasClass('edge-agg')) { idleColor = COL.edgeAggIdle; idleWidth = 1.6; idleOpacity = 0.6; }
        edge.style({
            'line-color': idleColor,
            'width': idleWidth,
            'opacity': idleOpacity,
            'shadow-blur': 0,
        });
    });

    for (const badge of Object.values(weightBadges)) {
        badge.textContent = '50:50';
        badge.classList.remove('changed');
    }

    edgeUtil = {};
    addLog('显示已重置', '');
}

function addLog(msg, cls) {
    const area = document.getElementById('logArea');
    const line = document.createElement('div');
    line.className = 'log-line' + (cls ? ` ${cls}` : '');

    const now = new Date();
    const ts = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;

    const tsSpan = document.createElement('span');
    tsSpan.className = 'log-ts';
    tsSpan.textContent = ts;

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
    addLog('日志已清空', '');
}