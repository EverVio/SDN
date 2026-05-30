import { COL, TOPOLOGY_VERTICAL_OFFSET, utilColor } from './config.js';
import { addLog } from './ui.js';

// Topology State Variables
export let cy = null;
export let linkMap = {};
export let edgeUtil = {};
export let aggNodes = [];
export let weightBadges = {};

// Holographic Topology Tooltip
export function showNodeTooltip(node) {
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

export function showEdgeTooltip(edge) {
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
        
        <div class="tt-row">
            <span class="tt-label">实时负载利用率:</span>
            <span class="tt-val highlight" style="color: ${barColor}">${utilPct}% (${currentTraffic} Gbps)</span>
        </div>
    `;

    tooltip.innerHTML = html;
    tooltip.classList.add('visible');

    const pos = edge.renderedMidpoint();
    tooltip.style.left = pos.x + 'px';
    tooltip.style.top = pos.y + 'px';
}

export function hideTooltip() {
    const tooltip = document.getElementById('topoTooltip');
    if (tooltip) {
        tooltip.classList.remove('visible');
    }
}

// Update canvas Zoom status display
export function updateCanvasStatus() {
    if (!cy) return;
    const zoomEl = document.getElementById('canvasZoom');
    if (zoomEl) zoomEl.textContent = `Zoom: ${Math.round(cy.zoom() * 100)}%`;
}

// Create overlays above aggregation nodes
export function createWeightBadges() {
    const container = document.getElementById('weightOverlays');
    if (!container) return;
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

// Update position offsets for aggregation overlays
export function updateBadgePositions() {
    if (!cy) return;
    const zoom = cy.zoom();
    const running = window.APP_STATE ? window.APP_STATE.running : false;

    for (const nodeId of aggNodes) {
        const node = cy.$('#' + nodeId);
        if (node.length === 0) continue;

        const dpid = node.data('dpid');
        const badge = weightBadges[dpid];
        if (!badge) continue;

        const pos = node.renderedPosition();
        badge.style.left = pos.x + 'px';
        badge.style.top = (pos.y + 95 * zoom) + 'px';
        badge.style.fontSize = Math.min(24, Math.max(14, 20 * zoom)) + 'px';
        
        const scaleBase = Math.min(1.6, Math.max(0.8, zoom));
        const scaleFactor = running ? scaleBase * 1.06 : scaleBase;
        badge.style.transform = `translate(-50%, 0) scale(${scaleFactor})`;
    }
}

// Handle iperf utility statistics update from socket
export function handleUtilUpdate(data) {
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
            'shadow-blur': 0,
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

// Handle routing dynamic weights update
export function handleWeightUpdate(data) {
    for (const [dpidStr, weights] of Object.entries(data)) {
        const dpid = parseInt(dpidStr);
        const badge = weightBadges[dpid];
        if (!badge) continue;

        const w3 = weights.port3_weight;
        const w4 = weights.port4_weight;
        const total = w3 + w4;
        const p3 = total > 0 ? Math.round(w3 / total * 100) : 50;
        const p4 = 100 - p3;

        let arrow = "⇄";
        if (p3 > p4) arrow = "◀";
        else if (p4 > p3) arrow = "▶";

        badge.textContent = `${p3}% ${arrow} ${p4}%`;

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

// Edge fluid animation frame loop
let lastTime = 0;
export function animateEdges(time) {
    requestAnimationFrame(animateEdges);
    let dt = time - lastTime;
    if (dt < 40) return;
    lastTime = time;

    if (!cy) return;
    cy.batch(() => {
        cy.edges().forEach(edge => {
            const util = edgeUtil[edge.id()] || 0;
            if (util > 0.05) {
                let speed = Math.max(0.5, util * 2.5);
                let currentOffset = edge.numericStyle('line-dash-offset') || 0;
                edge.style('line-dash-offset', currentOffset - speed);
            }
        });
    });
}

// Core topology layout build
export async function initTopology() {
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
                    'background-opacity': 0.08, 
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
                    'background-opacity': 0.08, 
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
                    'background-opacity': 0.08, 
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
                    'background-opacity': 0.05,
                    'border-width': 1.2,
                    'border-color': COL.borderHost,
                    'label': 'data(label)',
                    'font-size': '24px',
                    'color': COL.textMain, 
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
        cy.panBy({ x: 0, y: TOPOLOGY_VERTICAL_OFFSET });
        createWeightBadges();
        updateCanvasStatus();
        requestAnimationFrame(animateEdges);
    });

    // Hover tooltip interactions
    cy.on('mouseover', 'node', function (e) {
        var node = e.target;
        var neighborhood = node.neighborhood().add(node);
        cy.elements().addClass('faded');
        neighborhood.removeClass('faded');
        node.addClass('highlight');
        node.connectedEdges().addClass('highlight-edge');
        showNodeTooltip(node);
    });

    cy.on('mouseout', 'node', function (e) {
        cy.elements().removeClass('faded highlight highlight-edge');
        hideTooltip();
    });

    cy.on('mouseover', 'edge', function (e) {
        var edge = e.target;
        edge.addClass('highlight-edge');
        showEdgeTooltip(edge);
    });

    cy.on('mouseout', 'edge', function (e) {
        e.target.removeClass('highlight-edge');
        hideTooltip();
    });

    cy.on('zoom pan', () => {
        updateBadgePositions();
        hideTooltip();
    });

    // Mount variables & methods to window for backward compatibility
    window.cy = cy;
    window.linkMap = linkMap;
    window.edgeUtil = edgeUtil;
    window.aggNodes = aggNodes;
    window.weightBadges = weightBadges;
    window.updateBadgePositions = updateBadgePositions;
    window.handleUtilUpdate = handleUtilUpdate;
    window.handleWeightUpdate = handleWeightUpdate;
}
