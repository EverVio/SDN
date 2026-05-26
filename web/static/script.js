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
                    'text-margin-y': 18,
                    'font-size': '27px',
                    'font-weight': '700',
                    'text-outline-width': 2,
                    'text-outline-color': '#0B0F19',
                    'text-background-color': 'transparent',
                    'text-background-opacity': 0,
                }
            },
            {
                selector: 'node.core',
                style: {
                    'shape': 'diamond',
                    'width': 42,
                    'height': 42,
                    'background-color': COL.borderCore,
                    'background-opacity': 0.08, /* 显式开启半透明度，形成与图例一致的内空外框发光效果 */
                    'border-width': 2,
                    'border-color': COL.borderCore,
                    'label': 'data(label)',
                    'shadow-blur': 16,
                    'shadow-color': 'rgba(255, 51, 85, 0.45)',
                },
            },
            {
                selector: 'node.aggregation',
                style: {
                    'shape': 'ellipse',
                    'width': 36,
                    'height': 36,
                    'background-color': COL.borderAgg,
                    'background-opacity': 0.08, /* 显式开启半透明度，形成与图例一致的内空外框发光效果 */
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
                    'width': 30,
                    'height': 30,
                    'background-color': COL.borderEdge,
                    'background-opacity': 0.08, /* 显式开启半透明度，形成与图例一致的内空外框发光效果 */
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
                    'width': 44,
                    'height': 18,
                    'background-color': COL.borderHost,
                    'background-opacity': 0.05, /* 显式开启半透明度，形成与图例一致的内空外框发光效果 */
                    'border-width': 1.2,
                    'border-color': COL.borderHost,
                    'label': 'data(label)',
                    'font-size': '24px',
                    'color': COL.textMain, /* 增强终端亮度以防暗色模糊 */
                    'text-margin-y': 15,
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

    // Hover logic for neighborhood highlighting and holographic tooltip
    cy.on('mouseover', 'node', function(e){
        var node = e.target;
        var neighborhood = node.neighborhood().add(node);
        cy.elements().addClass('faded');
        neighborhood.removeClass('faded');
        node.addClass('highlight');
        node.connectedEdges().addClass('highlight-edge');
        showNodeTooltip(node);
    });

    cy.on('mouseout', 'node', function(e){
        cy.elements().removeClass('faded highlight highlight-edge');
        hideTooltip();
    });

    cy.on('mouseover', 'edge', function(e){
        var edge = e.target;
        edge.addClass('highlight-edge');
        showEdgeTooltip(edge);
    });

    cy.on('mouseout', 'edge', function(e){
        e.target.removeClass('highlight-edge');
        hideTooltip();
    });

    cy.on('zoom pan', () => {
        updateBadgePositions();
        hideTooltip(); // 画布拖拽缩放时隐藏Tooltip，保证完美流畅度
    });
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
        badge.textContent = '50% ⇄ 50%';
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
        badge.style.left = pos.x + 'px'; /* 补回X轴绝对定位 */
        badge.style.top = (pos.y + 95 * zoom) + 'px'; /* 增大纵向偏置，彻底拉开与汇聚层文字标签的上下间隔，防重合遮挡 */
        badge.style.fontSize = Math.min(24, Math.max(14, 20 * zoom)) + 'px'; /* 适度收缩动态字号，根绝重叠干涉 */
        const scaleBase = Math.min(1.6, Math.max(0.8, zoom));
        const scaleFactor = running ? scaleBase * 1.06 : scaleBase; // 实验运行时稳定大 6%，且有变化时保持静止定位，不跳动抖动
        badge.style.transform = `translate(-50%, 0) scale(${scaleFactor})`;
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
        // 极限制宽度微调：只比空闲状态下增加极其微量的一点点，物理宽度变化限制在 0.1px ~ 0.3px，视觉完全清澈，无突兀跳变
        let baseWidth = 1.5;
        let baseOpacity = 0.4;
        let addWidth = util * 0.1;
        let addOpacity = util * 0.1;

        if (edge.hasClass('agg-core')) {
            baseWidth = 2.5;
            baseOpacity = 0.6;
            addWidth = util * 0.3;
            addOpacity = util * 0.1;
        } else if (edge.hasClass('edge-agg')) {
            baseWidth = 2.0;
            baseOpacity = 0.5;
            addWidth = util * 0.2;
            addOpacity = util * 0.1;
        }

        const width = baseWidth + addWidth;
        const opacity = baseOpacity + addOpacity;

        edge.style({
            'line-color': color,
            'width': width,
            'opacity': opacity,
            'shadow-blur': 0, // 彻底剥离极其夸张的雾状外围霓虹发光阴影，保持拓扑连线绝对高精度与高透感
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

        // 智能分析路由倾向与指向箭头
        let arrow = "⇄";
        if (p3 > p4) arrow = "◀";
        else if (p4 > p3) arrow = "▶";

        badge.textContent = `${p3}% ${arrow} ${p4}%`;

        // 智能不平衡路由分配警告阈值
        const diff = Math.abs(p3 - p4);
        badge.classList.remove('imbalance-warn', 'imbalance-crit');
        if (diff >= 60) {
            badge.classList.add('imbalance-crit');
        } else if (diff >= 30) {
            badge.classList.add('imbalance-warn');
        }

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
    updateBadgePositions();
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
        badge.textContent = '50% ⇄ 50%';
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
                let speed = Math.max(0.5, util * 2.5); // 极度克制优雅的流速，防视觉夸张
                let currentOffset = edge.numericStyle('line-dash-offset') || 0;
                edge.style('line-dash-offset', currentOffset - speed);
            }
        });
    });
}

/* ===== HOLOGRAPHIC TOPOLOGY TOOLTIP FUNCTIONS ===== */
function showNodeTooltip(node) {
    const tooltip = document.getElementById('topoTooltip');
    if (!tooltip) return;

    const label = node.data('label') || node.id();
    const type = node.data('nodeType') || 'Unknown';
    const dpid = node.data('dpid') || node.data('edgeDpid') || 'None';
    const links = node.connectedEdges().length;

    const typeNames = {
        'core': 'Core (核心层交换机)',
        'aggregation': 'Aggregation (汇聚层交换机)',
        'edge': 'Edge (边缘层交换机)',
        'host': 'Host (终端计算节点)'
    };
    
    const badgeColors = {
        'core': '#ff3355',
        'aggregation': '#00f0ff',
        'edge': '#00ff88',
        'host': '#94a3b8'
    };

    let html = `
        <div class="tt-title">
            <span>${label.toUpperCase()}</span>
            <span class="tt-badge" style="color: ${badgeColors[type] || '#fff'}; border: 1px solid ${badgeColors[type] || '#fff'}">${type.toUpperCase()}</span>
        </div>
        <div class="tt-row">
            <span class="tt-label">DPID / Ident:</span>
            <span class="tt-val highlight">${dpid}</span>
        </div>
        <div class="tt-row">
            <span class="tt-label">架构层次:</span>
            <span class="tt-val">${typeNames[type] || type}</span>
        </div>
        <div class="tt-row">
            <span class="tt-label">活跃链路:</span>
            <span class="tt-val">${links} 端口在线</span>
        </div>
    `;

    tooltip.innerHTML = html;
    tooltip.classList.add('visible');
    
    const pos = node.renderedPosition();
    const zoom = cy.zoom();
    tooltip.style.left = pos.x + 'px';
    tooltip.style.top = (pos.y - (node.height() / 2) * zoom - 15) + 'px';
}

function showEdgeTooltip(edge) {
    const tooltip = document.getElementById('topoTooltip');
    if (!tooltip) return;

    const id = edge.id();
    const source = cy.$('#' + edge.data('source')).data('label') || edge.data('source');
    const target = cy.$('#' + edge.data('target')).data('label') || edge.data('target');
    const type = edge.data('edgeType') || 'Unknown';
    const bandwidth = edge.data('bandwidth') || '10G';
    const util = edgeUtil[id] || 0;

    const typeNames = {
        'agg-core': 'Agg-Core (骨干核心干线)',
        'edge-agg': 'Edge-Agg (汇聚级中继线)',
        'host-edge': 'Host-Edge (终端接入链路)'
    };

    const utilPct = Math.round(util * 100);
    let barColor = COL.green;
    if (util >= 0.7) barColor = COL.red;
    else if (util >= 0.3) barColor = COL.amber;

    const rawBw = parseFloat(bandwidth) || 10;
    const currentTraffic = (rawBw * util).toFixed(2);

    let html = `
        <div class="tt-title">
            <span>LINK OVERVIEW</span>
            <span class="tt-badge" style="color: var(--accent-secondary); border: 1px solid var(--accent-secondary)">MONITOR</span>
        </div>
        <div class="tt-row">
            <span class="tt-label">拓扑关联:</span>
            <span class="tt-val highlight">${source.toUpperCase()} ⇄ ${target.toUpperCase()}</span>
        </div>
        <div class="tt-row">
            <span class="tt-label">带宽能力:</span>
            <span class="tt-val">${bandwidth}bps</span>
        </div>
        <div class="tt-row">
            <span class="tt-label">链路性质:</span>
            <span class="tt-val">${typeNames[type] || type}</span>
        </div>
        
        <div class="tt-progress-wrap">
            <div class="tt-progress-header">
                <span>实时负载利用率</span>
                <span class="tt-val highlight" style="color: ${barColor}">${utilPct}% (${currentTraffic} Gbps)</span>
            </div>
            <div class="tt-progress-bar">
                <div class="tt-progress-fill" style="width: ${utilPct}%; background-color: ${barColor}; box-shadow: 0 0 8px ${barColor}"></div>
            </div>
        </div>
    `;

    tooltip.innerHTML = html;
    tooltip.classList.add('visible');

    const pos = edge.renderedMidpoint();
    tooltip.style.left = pos.x + 'px';
    tooltip.style.top = pos.y + 'px';
}

function hideTooltip() {
    const tooltip = document.getElementById('topoTooltip');
    if (tooltip) {
        tooltip.classList.remove('visible');
    }
}