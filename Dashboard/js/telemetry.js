// =============================================================
// telemetry.js — WebSocket connection + message dispatcher
// =============================================================

window.roverSocket = null;

function connectWebSocket() {
    window.roverSocket = new WebSocket(`ws://${window.location.hostname}:8000/ws`);

    window.roverSocket.onopen = function () {
        console.log('🟢 WebSocket Connected');
        // Send heartbeat every second to keep the session alive
        setInterval(() => {
            if (window.roverSocket.readyState === WebSocket.OPEN) {
                window.roverSocket.send(JSON.stringify({ command: 'heartbeat' }));
            }
        }, 1000);
    };

    window.roverSocket.onclose = function () {
        const gpsEl = document.getElementById('gps-status');
        if (gpsEl) { gpsEl.innerText = 'DISCONNECTED'; gpsEl.className = 'stat-value no-fix'; }
        setTimeout(connectWebSocket, 2000);
    };

    window.roverSocket.onmessage = function (event) {
        const data = JSON.parse(event.data);

        // ── Battery telemetry ──────────────────────────────────
        if (data.type === 'telemetry') {
            // 3S LiPo: 12.6V = 100%, 11.1V = 5% (cutoff), 10.5V = 0%
            // Use 12.6V (full) and 10.5V (empty) as the scale
            const V_MAX = 12.6;
            const V_MIN = 10.5;
            const pct = Math.round(Math.max(0, Math.min(100,
                (data.voltage - V_MIN) / (V_MAX - V_MIN) * 100
            )));

            const elPct = document.getElementById('battery-pct');
            const elVolt = document.getElementById('battery-voltage');

            if (elPct) {
                elPct.innerText = pct + '%';
                elPct.classList.remove('batt-good', 'batt-low', 'batt-critical');
                if      (pct > 40) elPct.classList.add('batt-good');
                else if (pct > 15) elPct.classList.add('batt-low');
                else               elPct.classList.add('batt-critical');
            }
            if (elVolt) {
                elVolt.innerText = data.voltage.toFixed(2) + ' V';
                elVolt.classList.remove('batt-good', 'batt-low', 'batt-critical');
                if      (pct > 40) elVolt.classList.add('batt-good');
                else if (pct > 15) elVolt.classList.add('batt-low');
                else               elVolt.classList.add('batt-critical');
            }
        }

        // ── GPS fix ───────────────────────────────────────────
        else if (data.type === 'gps') {
            const el = document.getElementById('gps-status');
            if (el) {
                if (data.fix) {
                    el.innerText  = 'GPS FIXED';
                    el.className  = 'stat-value gps-fixed';
                } else {
                    // Server sent a gps packet but fix flag is false
                    el.innerText  = 'SEARCHING…';
                    el.className  = 'stat-value no-fix';
                }
            }
            if (data.fix) {
                if (window.updateRoverPosition)
                    window.updateRoverPosition(data.lat, data.lon, data.heading);
                if (window.setRadarHeading)
                    window.setRadarHeading(data.heading);
            }
        }

        // ── LiDAR radar ───────────────────────────────────────
        else if (data.type === 'radar') {
            if (window.drawRadar) window.drawRadar(data.front, data.left, data.right);
        }

        // ── Planter / mangrove counter ─────────────────────────
        else if (data.type === 'planter') {
            // Server sends remaining count — clamp to our 0-11 range
            const serverCount = Math.max(0, Math.min(11, data.mangroves_remaining));
            window.mangroveCount = serverCount;
            updateMangroveDisplay();
        }

        // ── Robot mode change ─────────────────────────────────
        else if (data.type === 'mode') {
            const badge = document.getElementById('mode-badge');
            if (badge) {
                badge.innerText    = data.mode;
                badge.className    = 'mode-badge ' + (data.mode === 'AUTO' ? 'mode-auto' : 'mode-manual');
            }
        }

        // ── Autonomous nav status ─────────────────────────────
        else if (data.type === 'auto_status') {
            if (window.updateAutoStatus) window.updateAutoStatus(data);
            if (data.phase === 'complete' && window.onMissionComplete) {
                window.onMissionComplete();
            }
        }

        // ── Waypoint completed ────────────────────────────────
        else if (data.type === 'auto_wp_complete') {
            if (window.onWaypointComplete)
                window.onWaypointComplete(data.completed_index);
        }

        // ── ESP32 log lines ───────────────────────────────────
        else if (data.type === 'esp32_log') {
            console.log('[ESP32]', data.message);
            if (data.message.includes('Planting sequence complete') && window.onPlantComplete) {
                window.onPlantComplete();
            }
        }

        // ── Obstacle trap alert ───────────────────────────────
        else if (data.type === 'alert' && data.message === 'TRAPPED') {
            const modal = document.getElementById('alert-modal');
            if (modal) modal.style.display = 'flex';
        }
    };
}

// =============================================================
// MANGROVE COUNTER — manual adjustment + display
// =============================================================

const MANGROVE_MAX = 11;
window.mangroveCount = MANGROVE_MAX;   // starts full (11 seedlings loaded)

function updateMangroveDisplay() {
    const el = document.getElementById('mangrove-count');
    if (!el) return;
    el.innerText = window.mangroveCount;
    el.classList.remove('batt-good', 'batt-low', 'batt-critical');
    const pct = window.mangroveCount / MANGROVE_MAX;
    if      (pct > 0.5) el.classList.add('batt-good');
    else if (pct > 0.2) el.classList.add('batt-low');
    else                el.classList.add('batt-critical');
}

// Called by the +/− buttons in the telemetry bar
window.adjustMangrove = function (delta) {
    const next = window.mangroveCount + delta;
    if (next < 0 || next > MANGROVE_MAX) return;   // hard clamp — no beep needed
    window.mangroveCount = next;
    updateMangroveDisplay();
};

// Initialise display on page load
updateMangroveDisplay();

connectWebSocket();