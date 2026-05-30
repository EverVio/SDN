import { initTopology } from './topology.js';
import { initSocket } from './socket.js';
import { initControls, updateClock, addLog, setRunningState, experimentDuration } from './ui.js';

// Centralised State Management
export const APP_STATE = {
    running: false,
};

// Start routing experiment strategy
export async function startExperiment() {
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

// Manually request abort experiment
export async function stopExperiment() {
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

// Initialise core app modules on DOM load
document.addEventListener('DOMContentLoaded', async () => {
    updateClock();
    setInterval(updateClock, 1000);

    initSocket();
    await initTopology();
    initControls();

    addLog('System UI initialized.', 'info');
    addLog('Awaiting input...', '');
});

// Mount variables and functions to window for index.html inline click events
window.APP_STATE = APP_STATE;
window.startExperiment = startExperiment;
window.stopExperiment = stopExperiment;
