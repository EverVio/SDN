import { addLog, setRunningState, showResults } from './ui.js';
import { handleUtilUpdate, handleWeightUpdate } from './topology.js';

export let socket = null;

// Elapsed timer verification and update
export function updateProgress(elapsed, duration) {
    const bar = document.getElementById('progressBar');
    const track = document.querySelector('.progress-track');
    const elapsedEl = document.getElementById('progressElapsed');
    const statusText = document.getElementById('statusText');
    const chip = document.getElementById('hudConsole');

    if (elapsed === 0) {
        if (chip) chip.className = 'hud-console running initializing';
        if (track) track.classList.add('initializing');
        if (bar) bar.style.width = '100%';
        if (elapsedEl) elapsedEl.textContent = '00:00';
        if (statusText) statusText.textContent = 'Initializing';
    } else {
        if (chip) chip.className = 'hud-console running';
        if (track) track.classList.remove('initializing');
        
        const pct = Math.min(100, (elapsed / duration) * 100);
        if (bar) bar.style.width = pct + '%';

        const displayElapsed = Math.min(elapsed, duration);
        const mins = Math.floor(displayElapsed / 60);
        const secs = Math.floor(displayElapsed % 60);
        if (elapsedEl) elapsedEl.textContent = `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;

        if (statusText && window.APP_STATE && window.APP_STATE.running) {
            statusText.textContent = 'Active Engine';
        }
    }
}

export function onExperimentComplete(data) {
    setRunningState(false);
    addLog(`EXPERIMENT COMPLETED IN ${Math.round(data.elapsed || 0)}s.`, 'system');
}

export function initSocket() {
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

    window.socket = socket;
}
