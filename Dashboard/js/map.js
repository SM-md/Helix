// =============================================================
// map.js — Leaflet map with offline ESRI satellite tile support
//
// Tiles are served from the Pi itself via FastAPI at /tiles/{z}/{x}/{y}.png
// Run download_tiles.py once (with WiFi) to pre-cache the area.
// After that the map works with no internet connection.
//
// Tile source: ESRI World Imagery (satellite)
//   — No API key required
//   — No rate limiting / IP blocks like OSM
//   — Satellite imagery is far more useful than road maps for
//     coastal mangrove planting navigation
// =============================================================

window.map = L.map('mission-map', {
    center: [10.265624, 123.819612],
    zoom:   15,
    maxZoom: 24,          // allows over-zoom past native tile limit
    zoomControl: true,
}).setView([10.265624, 123.819612], 15);

// ── Tile layer — all tiles routed through Pi proxy ───────────
// The Pi's FastAPI tile endpoint handles everything:
//   • Tile cached on disk  →  served instantly (works offline)
//   • Tile not cached      →  Pi fetches from ESRI, saves it,
//                             serves it (auto-builds cache)
//   • Fully offline + not cached  →  transparent empty tile
//
// The browser NEVER contacts OSM or ESRI directly, so there are
// zero 403 / policy-blocked error tiles in any situation.
L.tileLayer('/tiles/{z}/{x}/{y}.png', {
    maxZoom:     24,          // over-zoom: Leaflet stretches Z18 tiles up to Z24
    maxNativeZoom: 18,    // tells Leaflet not to request tiles beyond Z18
    minZoom:     10,
    attribution: 'Tiles © Esri — Esri, Maxar, GeoEye, Earthstar Geographics, CNES/Airbus DS, USDA, USGS, AeroGRID, IGN',
}).addTo(window.map);

// ── Map click — place waypoint selection marker ───────────────
// Uses circleMarker (pure canvas) instead of L.marker so it
// works fully offline — L.marker needs marker-icon.png which
// would 404 when the Pi has no internet connection.
let selectionMarker   = null;
let selectionLatLng   = null;   // last clicked position
const roverWpLayer    = L.layerGroup().addTo(window.map);

// Redraws the rover→waypoint line + distance label.
// Called whenever the rover moves OR a new map click happens.
function updateRoverToWpLine() {
    roverWpLayer.clearLayers();

    if (!selectionLatLng || !roverMarker) return;

    const rLatLng = roverMarker.getLatLng();
    const wLatLng = selectionLatLng;
    const distM   = rLatLng.distanceTo(wLatLng);

    // Format: metres under 1 km, otherwise km
    const distText = distM >= 1000
        ? (distM / 1000).toFixed(2) + ' km'
        : distM.toFixed(1) + ' m';

    // Dashed orange line rover → clicked waypoint
    L.polyline([rLatLng, wLatLng], {
        color:     '#ff9800',
        weight:    2,
        opacity:   0.85,
        dashArray: '8, 6',
    }).addTo(roverWpLayer);

    // Distance label at the midpoint
    const mid = [
        (rLatLng.lat + wLatLng.lat) / 2,
        (rLatLng.lng + wLatLng.lng) / 2,
    ];
    L.marker(mid, {
        icon: L.divIcon({
            className: '',
            html: `<div style="
                background:rgba(18,18,18,0.92);
                color:#ff9800;
                border:1px solid #ff9800;
                border-radius:5px;
                padding:2px 7px;
                font-size:0.78rem;
                font-weight:bold;
                font-family:monospace;
                white-space:nowrap;
                box-shadow:0 2px 6px rgba(0,0,0,0.6);
                transform:translate(-50%,-50%);
            ">\u{1F4CF} ${distText}</div>`,
            iconSize:   [1, 1],
            iconAnchor: [0, 0],
        }),
        interactive: false,
        zIndexOffset: 500,
    }).addTo(roverWpLayer);
}

window.map.on('click', function (e) {
    if (window.activeTab !== 'mission') return;
    document.getElementById('lat-input').value = e.latlng.lat.toFixed(7);
    document.getElementById('lng-input').value = e.latlng.lng.toFixed(7);

    selectionLatLng = e.latlng;

    if (selectionMarker) {
        selectionMarker.setLatLng(e.latlng);
    } else {
        selectionMarker = L.circleMarker(e.latlng, {
            radius:      10,
            color:       '#ffffff',
            weight:      2,
            fillColor:   '#ffea00',
            fillOpacity: 0.9,
        }).addTo(window.map);
    }

    updateRoverToWpLine();
});


// ── Live rover position marker ────────────────────────────────
let roverMarker   = null;
let roverHeading  = 0;
let roverPolyline = [];    // trail of last N positions
const TRAIL_LEN   = 30;
let trailLayer    = L.layerGroup().addTo(window.map);

// Custom SVG rover icon — arrow rotates with heading
function makeRoverIcon(heading) {
    const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" width="36" height="36" viewBox="0 0 36 36">
      <g transform="rotate(${heading}, 18, 18)">
        <polygon points="18,2 28,32 18,26 8,32" fill="#2979ff" stroke="white" stroke-width="2"/>
      </g>
    </svg>`;
    return L.divIcon({
        html:        svg,
        className:   '',
        iconSize:    [36, 36],
        iconAnchor:  [18, 18],
    });
}

window.updateRoverPosition = function (lat, lng, heading) {
    roverHeading = heading !== undefined ? heading : roverHeading;

    if (roverMarker) {
        roverMarker.setLatLng([lat, lng]);
        roverMarker.setIcon(makeRoverIcon(roverHeading));
    } else {
        roverMarker = L.marker([lat, lng], {
            icon:        makeRoverIcon(roverHeading),
            zIndexOffset: 1000,
        }).addTo(window.map);
        window.map.panTo([lat, lng]);
    }

    // Draw GPS trail
    roverPolyline.push([lat, lng]);
    if (roverPolyline.length > TRAIL_LEN) roverPolyline.shift();
    trailLayer.clearLayers();
    if (roverPolyline.length > 1) {
        L.polyline(roverPolyline, {
            color: '#2979ff', weight: 2, opacity: 0.5, dashArray: '4,4'
        }).addTo(trailLayer);
    }

    // Update rover→waypoint distance line whenever rover moves
    updateRoverToWpLine();
};

// ── Force Leaflet to render correctly after page load ─────────
// Leaflet calculates its tile grid at init time, before the
// browser has finished painting the flex layout. Fire
// invalidateSize() multiple times with increasing delays to
// guarantee it catches the real container size.
// Also explicitly pan to the center so tiles are requested.
document.addEventListener('DOMContentLoaded', () => {
    [50, 150, 300, 600, 1000].forEach(ms => {
        setTimeout(() => {
            if (!window.map) return;
            window.map.invalidateSize({ animate: false });
            // Force tile load by panning to center
            window.map.panTo([10.265624, 123.819612], { animate: false });
        }, ms);
    });
});

// ── Auto-nav status overlay on map ───────────────────────────
const statusDiv = L.control({ position: 'bottomleft' });
statusDiv.onAdd = function () {
    const d = L.DomUtil.create('div', 'map-auto-status');
    d.id = 'map-auto-status';
    d.style.cssText = `
        background: rgba(18,18,18,0.88);
        border: 1px solid #2979ff;
        border-radius: 8px;
        padding: 8px 14px;
        color: white;
        font-family: monospace;
        font-size: 0.82rem;
        line-height: 1.6;
        display: none;
        min-width: 220px;
    `;
    return d;
};
statusDiv.addTo(window.map);

window.updateAutoStatus = function (data) {
    const el = document.getElementById('map-auto-status');
    if (!el) return;

    if (!data || data.phase === 'idle' || data.phase === 'complete') {
        el.style.display = 'none';
        return;
    }

    const phaseColour = {
        turning:     '#ffea00',
        driving:     '#00e676',
        planting:    '#00e676',
        blocked:     '#ff1744',
        paused:      '#ffea00',
        waiting_gps: '#aaaaaa',
    }[data.phase] || '#ffffff';

    const phaseLabel = (data.phase || '').toUpperCase();
    const wpLabel    = data.wp_index !== undefined
        ? `WP ${data.wp_index + 1} (${(data.wp_type || '').toUpperCase()})`
        : '';

    el.style.display = 'block';
    el.innerHTML = `
        <span style="color:${phaseColour};font-weight:bold">⬤ ${phaseLabel}</span>
        ${wpLabel ? `<br>Target: ${wpLabel}` : ''}
        ${data.distance_m !== undefined ? `<br>Distance: <b>${data.distance_m} m</b>` : ''}
        ${data.bearing    !== undefined ? `<br>Bearing: ${data.bearing}°` : ''}
        ${data.heading    !== undefined ? `<br>Heading: ${data.heading}°` : ''}
        ${data.hdg_error  !== undefined ? `<br>Turn err: ${data.hdg_error > 0 ? '+' : ''}${data.hdg_error}°` : ''}
        ${data.message    ? `<br><span style="color:#aaa">${data.message}</span>` : ''}
    `;
};