// =============================================================
// waypoints.js — Mission queue, map route drawing, mission control
// =============================================================

window.missionQueue = [];
window.routeLayer   = null;
window.missionActive = false;

// =============================================================
// ADD / REMOVE WAYPOINTS
// =============================================================

window.addWaypoint = function (type) {
    const lat = parseFloat(document.getElementById('lat-input').value);
    const lng = parseFloat(document.getElementById('lng-input').value);
    if (isNaN(lat) || isNaN(lng)) {
        alert('Click the map first to set valid coordinates!');
        return;
    }
    window.missionQueue.push({ type, lat, lng, status: 'pending' });
    if (type === 'plant') {
        document.getElementById('lat-input').value = '';
        document.getElementById('lng-input').value = '';
    }
    window.renderQueue();
    window.drawMapRoute();
};

window.removeWaypoint = function (index) {
    window.missionQueue.splice(index, 1);
    window.renderQueue();
    window.drawMapRoute();
};

// =============================================================
// RENDER QUEUE LIST
// =============================================================

window.renderQueue = function () {
    const list = document.getElementById('waypoint-list');
    list.innerHTML = '';
    window.missionQueue.forEach((wp, i) => {
        const li  = document.createElement('li');
        const cls = wp.status === 'completed' ? 'completed' : wp.type;
        li.className = `waypoint-item ${cls}`;
        const icon  = wp.status === 'completed' ? '✔️' : (wp.type === 'navigate' ? '📍' : '🌱');
        const label = wp.type === 'navigate' ? 'NAV' : 'PLANT';
        li.innerHTML = `
            <span>${icon} <strong>${label}</strong>
                <span class="wp-coords">${wp.lat.toFixed(6)}, ${wp.lng.toFixed(6)}</span>
            </span>
            ${wp.status === 'pending'
                ? `<span class="wp-delete" onclick="removeWaypoint(${i})">✕</span>`
                : ''}
        `;
        list.appendChild(li);
    });

    // Show/hide mission buttons depending on state
    updateMissionButtons();
};

// =============================================================
// MISSION CONTROL BUTTONS
// =============================================================

function updateMissionButtons() {
    const btnExecute = document.getElementById('btn-execute');
    const btnPause   = document.getElementById('btn-pause');
    const btnAbort   = document.getElementById('btn-abort');

    if (!btnExecute) return;

    if (window.missionActive) {
        btnExecute.style.display = 'none';
        if (btnPause) btnPause.style.display  = 'inline-block';
        if (btnAbort) btnAbort.style.display  = 'inline-block';
    } else {
        btnExecute.style.display = 'block';
        if (btnPause) btnPause.style.display  = 'none';
        if (btnAbort) btnAbort.style.display  = 'none';
    }
}

// Send the mission queue to the Pi and start autonomous navigation
window.sendMissionToPi = function () {
    if (window.missionQueue.length === 0) {
        alert('Queue is empty.'); return;
    }
    if (!window.roverSocket || window.roverSocket.readyState !== WebSocket.OPEN) {
        alert('WebSocket not connected.'); return;
    }

    const pending = window.missionQueue.filter(wp => wp.status === 'pending');
    if (pending.length === 0) {
        alert('All waypoints are already completed.'); return;
    }

    window.roverSocket.send(JSON.stringify({
        command: 'start_mission',
        queue:   pending,
    }));

    window.missionActive = true;
    updateMissionButtons();
};

window.pauseMission = function () {
    if (!window.roverSocket || window.roverSocket.readyState !== WebSocket.OPEN) return;
    const btn = document.getElementById('btn-pause');
    const isPaused = btn && btn.dataset.paused === '1';

    if (isPaused) {
        window.roverSocket.send(JSON.stringify({ command: 'resume_mission' }));
        if (btn) { btn.innerText = '⏸ Pause'; btn.dataset.paused = '0'; }
    } else {
        window.roverSocket.send(JSON.stringify({ command: 'pause_mission' }));
        if (btn) { btn.innerText = '▶ Resume'; btn.dataset.paused = '1'; }
    }
};

window.abortMission = function () {
    if (!window.roverSocket || window.roverSocket.readyState !== WebSocket.OPEN) return;
    if (!confirm('Abort the current mission and return to manual control?')) return;
    window.roverSocket.send(JSON.stringify({ command: 'abort_mission' }));
    window.missionActive = false;
    updateMissionButtons();
};

// Called from telemetry.js when auto_wp_complete arrives
window.onWaypointComplete = function (completedIndex) {
    if (window.missionQueue[completedIndex]) {
        window.missionQueue[completedIndex].status = 'completed';
    }
    window.renderQueue();
    window.drawMapRoute();
};

// Called from telemetry.js when mission phase === 'complete'
window.onMissionComplete = function () {
    window.missionActive = false;
    window.missionQueue.forEach(wp => { wp.status = 'completed'; });
    window.renderQueue();
    window.drawMapRoute();
    updateMissionButtons();
    setTimeout(() => window.saveAndClearQueue('Completed'), 3000);
};

// =============================================================
// CLEAR / SAVE
// =============================================================

window.clearQueueOnly = function () {
    if (window.missionActive) { alert('Cannot clear queue while mission is active.'); return; }
    if (window.missionQueue.length === 0) { alert('Queue is already empty.'); return; }
    if (confirm('Clear queue without saving?')) {
        window.missionQueue = [];
        window.renderQueue();
        window.drawMapRoute();
    }
};

window.saveAndClearQueue = function (status = 'Manually Cleared') {
    if (window.missionQueue.length === 0) { alert('Queue is already empty.'); return; }
    window.saveMissionToLog(status);
    window.missionQueue = [];
    window.renderQueue();
    window.drawMapRoute();
};

// =============================================================
// MAP ROUTE DRAWING
// =============================================================

window.drawMapRoute = function () {
    if (typeof window.map === 'undefined') return;
    if (!window.routeLayer) window.routeLayer = L.layerGroup().addTo(window.map);
    window.routeLayer.clearLayers();

    const latlngs = window.missionQueue.map(wp => [wp.lat, wp.lng]);

    if (latlngs.length > 1) {
        L.polyline(latlngs, {
            color: 'white', dashArray: '10,10', weight: 3, opacity: 0.6
        }).addTo(window.routeLayer);

        // Distance labels between waypoints
        for (let i = 1; i < window.missionQueue.length; i++) {
            const p1 = L.latLng(window.missionQueue[i-1].lat, window.missionQueue[i-1].lng);
            const p2 = L.latLng(window.missionQueue[i].lat,   window.missionQueue[i].lng);
            const d  = p1.distanceTo(p2).toFixed(1);
            const mid = [(p1.lat + p2.lat)/2, (p1.lng + p2.lng)/2];
            L.marker(mid, {
                icon: L.divIcon({
                    className: 'distance-label-container',
                    html: `<span class="distance-label-text">${d} m</span>`,
                    iconSize: [60, 20], iconAnchor: [30, 10],
                }),
                interactive: false,
            }).addTo(window.routeLayer);
        }
    }

    window.missionQueue.forEach((wp, i) => {
        const color  = wp.status === 'completed' ? '#555'
                     : wp.type   === 'plant'     ? '#00e676' : '#2979ff';
        const radius = wp.type === 'plant' ? 10 : 7;
        const label  = wp.type === 'plant' ? '🌱' : `${i+1}`;

        L.circleMarker([wp.lat, wp.lng], {
            color: '#fff', weight: 2,
            fillColor: color, fillOpacity: 0.35, radius,
        })
        .bindPopup(`<b>WP ${i+1} — ${wp.type.toUpperCase()}</b><br>
                    ${wp.lat.toFixed(6)}, ${wp.lng.toFixed(6)}<br>
                    Status: ${wp.status}`)
        .addTo(window.routeLayer);

        // Sequence number label — transparent background so distance labels show through
        L.marker([wp.lat, wp.lng], {
            icon: L.divIcon({
                className: '',
                html: `<div style="
                    background: transparent;
                    color: ${color};
                    border-radius: 50%;
                    width: 18px; height: 18px;
                    display: flex; align-items: center; justify-content: center;
                    font-size: 0.7rem; font-weight: bold;
                    border: 2px solid ${color};
                    text-shadow: 0 0 4px rgba(0,0,0,0.9), 0 0 2px rgba(0,0,0,0.9);
                    box-shadow: 0 0 0 1px rgba(0,0,0,0.5);
                    margin-top: -9px; margin-left: -9px;">${label}</div>`,
                iconSize: [18, 18], iconAnchor: [9, 9],
            }),
            interactive: false,
        }).addTo(window.routeLayer);
    });
};