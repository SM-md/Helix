// =============================================================
// logs.js — Mission queue history log
// =============================================================

const LOGS_KEY = 'helix_mission_logs';

function loadLogs() {
    try { return JSON.parse(localStorage.getItem(LOGS_KEY)) || []; }
    catch { return []; }
}

function saveLogs(logs) {
    try { localStorage.setItem(LOGS_KEY, JSON.stringify(logs)); } catch {}
}

window.saveMissionToLog = function (status = 'Manually Cleared') {
    if (!window.missionQueue || window.missionQueue.length === 0) return;

    const logs = loadLogs();
    const completed = window.missionQueue.filter(wp => wp.status === 'completed').length;
    const total     = window.missionQueue.length;
    const plants    = window.missionQueue.filter(wp => wp.type === 'plant').length;

    logs.unshift({
        id:        Date.now(),
        timestamp: new Date().toLocaleString(),
        status,
        total,
        completed,
        plants,
        waypoints: JSON.parse(JSON.stringify(window.missionQueue)),
    });

    // Keep last 50 logs
    if (logs.length > 50) logs.splice(50);
    saveLogs(logs);
    renderLogs();
};

function renderLogs() {
    const container = document.getElementById('log-list');
    if (!container) return;

    const logs = loadLogs();
    if (logs.length === 0) {
        container.innerHTML = '<p style="color:#777;text-align:center;margin-top:20px;">No mission history yet.</p>';
        return;
    }

    container.innerHTML = logs.map(log => {
        const isCompleted = log.status === 'Completed';
        const cls   = isCompleted ? 'log-completed' : 'log-cleared';
        const icon  = isCompleted ? '✅' : '📋';
        return `
        <div class="log-item ${cls}">
            <div class="log-header">
                <span>${icon} Mission Log</span>
                <span class="log-status" style="color:${isCompleted ? 'var(--rtk-fixed)' : 'var(--warning)'}">
                    ${log.status}
                </span>
            </div>
            <div style="font-size:0.8rem;color:var(--text-muted);margin-bottom:8px;">${log.timestamp}</div>
            <div class="log-details">
                <span>🗺️ ${log.total} waypoints</span>
                <span>✅ ${log.completed} completed</span>
                <span>🌱 ${log.plants} plant sites</span>
            </div>
        </div>`;
    }).join('');
}

// Render on load
document.addEventListener('DOMContentLoaded', renderLogs);
renderLogs();