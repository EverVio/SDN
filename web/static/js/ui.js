import { updateBadgePositions } from './topology.js';

// Interactive UI Global Constants/Variables
export let experimentDuration = 60;

// Log buffer helper to avoid heavy DOM stress
export function addLog(msg, type = '') {
    const area = document.getElementById('logArea');
    if (!area) return;

    const line = document.createElement('div');
    line.className = `log-line ${type}`;

    const now = new Date();
    const ts = String(now.getHours()).padStart(2, '0') + ':' +
        String(now.getMinutes()).padStart(2, '0') + ':' +
        String(now.getSeconds()).padStart(2, '0');

    const tsSpan = document.createElement('span');
    tsSpan.className = 'log-ts';
    tsSpan.textContent = `[${ts}]`;

    const textSpan = document.createElement('span');
    textSpan.textContent = msg;

    line.appendChild(tsSpan);
    line.appendChild(textSpan);
    area.appendChild(line);

    area.scrollTop = area.scrollHeight;

    // Prune logs if too long
    while (area.children.length > 200) {
        area.removeChild(area.firstChild);
    }
}

export function clearLog() {
    const area = document.getElementById('logArea');
    if (area) area.innerHTML = '';
    addLog('Terminal cleared.', '');
}

// Logarithmic Slider Utilities [30s, 600s]
export function sliderToValue(x) {
    const yMin = 30;
    const yMax = 600;
    const val = yMin * Math.pow(yMax / yMin, x / 1000);
    return Math.round(val);
}

export function valueToSlider(y) {
    const yMin = 30;
    const yMax = 600;
    if (y < yMin) return 0;
    if (y > yMax) return 1000;
    const x = 1000 * Math.log(y / yMin) / Math.log(yMax / yMin);
    return Math.round(x);
}

export function formatDuration(seconds) {
    if (seconds < 60) {
        return { value: seconds, unit: 'sec' };
    } else {
        const m = Math.floor(seconds / 60);
        const s = seconds % 60;
        const sStr = String(s).padStart(2, '0');
        return { value: `${m}:${sStr}`, unit: 'min' };
    }
}

// Initialise slider and controllers
export function initControls() {
    const slider = document.getElementById('durationSlider');
    const valueEl = document.getElementById('durationValue');
    const unitEl = document.querySelector('.duration-unit');

    if (!slider || !valueEl) return;

    experimentDuration = 60;

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

    // Make global clearLog callback
    window.clearLog = clearLog;
}

// Update clock display
export function updateClock() {
    const el = document.getElementById('clockDisplay');
    if (!el) return;
    const now = new Date();
    const h = String(now.getHours()).padStart(2, '0');
    const m = String(now.getMinutes()).padStart(2, '0');
    const s = String(now.getSeconds()).padStart(2, '0');
    el.textContent = `${h}:${m}:${s}`;
}

// Change panel running state in DOM
export function setRunningState(isRunning, group) {
    if (window.APP_STATE) window.APP_STATE.running = isRunning;

    const chip = document.getElementById('hudConsole');
    const text = document.getElementById('statusText');
    const code = document.getElementById('statusCode');
    const btnStart = document.getElementById('btnStart');
    const btnStop = document.getElementById('btnStop');
    const progressSection = document.getElementById('hudProgressSection');

    if (isRunning) {
        if (chip) chip.className = 'hud-console running initializing';
        if (text) text.textContent = 'Initializing';
        if (code) code.textContent = '01';
        if (btnStart) btnStart.disabled = true;
        if (btnStop) btnStop.disabled = false;
        if (progressSection) progressSection.style.display = 'flex';

        const track = document.querySelector('.progress-track');
        if (track) track.classList.add('initializing');
        const bar = document.getElementById('progressBar');
        if (bar) bar.style.width = '100%';
        const elapsedEl = document.getElementById('progressElapsed');
        if (elapsedEl) elapsedEl.textContent = '00:00';
    } else {
        if (chip) chip.className = 'hud-console';
        if (text) text.textContent = 'System Standby';
        if (code) code.textContent = '00';
        if (btnStart) btnStart.disabled = false;
        if (btnStop) btnStop.disabled = true;
        if (progressSection) progressSection.style.display = 'none';

        const track = document.querySelector('.progress-track');
        if (track) track.classList.remove('initializing');
    }
    updateBadgePositions();
}

// Render statistical results report popup
export function showResults(data) {
    // Check if result modal backdrop exists
    let backdrop = document.getElementById('resultsModal');
    if (!backdrop) {
        backdrop = document.createElement('div');
        backdrop.id = 'resultsModal';
        backdrop.className = 'modal-backdrop';
        document.body.appendChild(backdrop);
    }

    const group = data.group || 'base';
    const rows = data.results || [];

    const groupNames = {
        'base': 'Baseline (ECMP)',
        'threshold': 'Threshold Active',
        'predictive': 'MLP Predictive'
    };

    // Calculate core aggregate indices
    let totalLoss = 0;
    let maxJitter = 0;
    let totalBw = 0;
    const count = rows.length;

    rows.forEach(r => {
        totalLoss += parseFloat(r.avg_loss_pct) || 0;
        const j = parseFloat(r.avg_jitter_ms) || 0;
        if (j > maxJitter) maxJitter = j;
        totalBw += parseFloat(r.avg_bandwidth_mbps) || 0;
    });

    const avgLoss = count > 0 ? totalLoss / count : 0;
    const avgBw = count > 0 ? totalBw / count : 0;

    // Define card alert levels
    let lossCardClass = '';
    if (avgLoss >= 30.0) lossCardClass = 'crit';
    else if (avgLoss >= 15.0) lossCardClass = 'warn';

    let jitterCardClass = '';
    if (maxJitter >= 30.0) jitterCardClass = 'crit';
    else if (maxJitter >= 20.0) jitterCardClass = 'warn';

    let bwCardClass = '';
    if (avgBw < 0.3) bwCardClass = 'crit';
    else if (avgBw < 0.4) bwCardClass = 'warn';

    let tableRowsHtml = '';
    rows.forEach(r => {
        const loss = parseFloat(r.avg_loss_pct) || 0;
        const jitter = parseFloat(r.avg_jitter_ms) || 0;
        const bw = parseFloat(r.avg_bandwidth_mbps) || 0;

        let lossClass = 'metric-good';
        if (loss >= 30.0) lossClass = 'metric-bad';
        else if (loss >= 15.0) lossClass = 'metric-warn';

        let jitterClass = 'metric-good';
        if (jitter >= 30.0) jitterClass = 'metric-bad';
        else if (jitter >= 20.0) jitterClass = 'metric-warn';

        let bwClass = 'metric-good';
        if (bw < 0.3) bwClass = 'metric-bad';
        else if (bw < 0.4) bwClass = 'metric-warn';

        tableRowsHtml += `
            <tr>
                <td>${r.flow}</td>
                <td class="${lossClass}">${loss.toFixed(2)}%</td>
                <td class="${jitterClass}">${jitter.toFixed(3)} ms</td>
                <td class="${bwClass}">${bw.toFixed(2)} Gbps</td>
            </tr>
        `;
    });

    backdrop.innerHTML = `
        <div class="modal-glass">
            <div class="modal-header">
                <div class="m-title">
                    <span class="panel-indicator"></span>
                    <h2>${groupNames[group] || group} 性能分析报告</h2>
                </div>
                <button class="m-close" onclick="closeResultsModal()">×</button>
            </div>
            <div class="modal-body">
                <!-- Summary HUD Cards Grid -->
                <div class="results-summary-grid">
                    <div class="summary-card loss-card ${lossCardClass}">
                        <span class="sc-label">平均丢包率</span>
                        <span class="sc-val">${avgLoss.toFixed(2)}%</span>
                    </div>
                    <div class="summary-card jitter-card ${jitterCardClass}">
                        <span class="sc-label">最大时延抖动</span>
                        <span class="sc-val">${maxJitter.toFixed(3)} ms</span>
                    </div>
                    <div class="summary-card bw-card ${bwCardClass}">
                        <span class="sc-label">平均吞吐</span>
                        <span class="sc-val">${avgBw.toFixed(2)} Gbps</span>
                    </div>
                </div>

                <!-- Detailed Table Container -->
                <div class="results-table-container">
                    <table class="results-table">
                        <thead>
                            <tr>
                                <th>测试流向</th>
                                <th>平均丢包</th>
                                <th>时延抖动</th>
                                <th>网络吞吐</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${tableRowsHtml || '<tr><td colspan="4" style="text-align:center;color:var(--text-muted)">无实验分析数据</td></tr>'}
                        </tbody>
                    </table>
                </div>
            </div>
            <div class="modal-footer">
                <button class="btn btn-ghost" onclick="closeResultsModal()" style="display:inline-block;width:auto;padding:8px 20px">关闭报告</button>
            </div>
        </div>
    `;

    backdrop.style.display = 'flex';
}

export function closeResultsModal() {
    const backdrop = document.getElementById('resultsModal');
    if (backdrop) {
        backdrop.style.display = 'none';
    }
}

// Mount modal controllers to window for HTML click hooks
window.closeResultsModal = closeResultsModal;
window.closeModal = closeResultsModal;
window.showResults = showResults;
window.setRunningState = setRunningState;
