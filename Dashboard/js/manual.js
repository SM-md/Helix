// =============================================================
// manual.js — D-pad, keyboard controls, radar canvas, plant button
// =============================================================

// ── Speed slider ──────────────────────────────────────────────
const speedSlider = document.getElementById('speed-slider');
const speedVal    = document.getElementById('speed-val');

if (speedSlider) {
    speedSlider.addEventListener('input', function () {
        speedVal.innerText = this.value;
        // Update gradient fill
        this.style.background = `linear-gradient(to right, var(--gps-fixed) ${this.value}%, #444 ${this.value}%)`;
    });
}

function getSpeed() {
    return speedSlider ? parseInt(speedSlider.value, 10) : 100;
}

// ── Send manual drive command ─────────────────────────────────
function sendManual(direction) {
    if (!window.roverSocket || window.roverSocket.readyState !== WebSocket.OPEN) {
        // Show a brief warning in the GPS status bar so the operator knows
        // the socket is down — previously this failed silently.
        const el = document.getElementById('gps-status');
        if (el && el.innerText !== 'DISCONNECTED') {
            el.innerText = 'DISCONNECTED';
            el.className = 'stat-value no-fix';
        }
        console.warn('[MANUAL] WebSocket not connected — command dropped:', direction);
        return;
    }
    window.roverSocket.send(JSON.stringify({
        command:   'manual_override',
        direction: direction,
        speed:     getSpeed(),
    }));
}

// ── Send plant command ────────────────────────────────────────
window.sendPlantCmd = function () {
    if (!window.roverSocket || window.roverSocket.readyState !== WebSocket.OPEN) {
        alert('WebSocket not connected — cannot send plant command.');
        return;
    }

    const btn = document.getElementById('btn-manual-plant');

    window.roverSocket.send(JSON.stringify({ command: 'manual_plant' }));

    // Visual feedback while waiting for ACK
    if (btn) {
        btn.disabled = true;
        btn.classList.add('planting');
        btn.innerHTML = `<span class="plant-spinner"></span> PLANTING…`;
    }
};

// Called from telemetry.js when ESP32 sends "Planting sequence complete"
window.onPlantComplete = function () {
    const btn = document.getElementById('btn-manual-plant');
    if (btn) {
        btn.disabled = false;
        btn.classList.remove('planting');
        btn.innerHTML = '🌱 EXECUTE PLANTING';
    }
};

// ── D-pad buttons — pointer events (works on mobile too) ──────
const dirButtons = {
    'btn-up':    'forward',
    'btn-down':  'backward',
    'btn-left':  'left',
    'btn-right': 'right',
};

Object.entries(dirButtons).forEach(([id, dir]) => {
    const btn = document.getElementById(id);
    if (!btn) return;

    const press = (e) => {
        e.preventDefault();
        btn.classList.add('d-btn-active');
        sendManual(dir);
    };
    const release = (e) => {
        e.preventDefault();
        btn.classList.remove('d-btn-active');
        sendManual('stop');
    };

    btn.addEventListener('pointerdown',  press);
    btn.addEventListener('pointerup',    release);
    btn.addEventListener('pointerleave', release);
    btn.addEventListener('pointercancel',release);
});

// ── Keyboard controls (WASD / Arrow keys) ────────────────────
const keyMap = {
    'ArrowUp':    'forward',
    'ArrowDown':  'backward',
    'ArrowLeft':  'left',
    'ArrowRight': 'right',
    'w': 'forward', 'W': 'forward',
    's': 'backward','S': 'backward',
    'a': 'left',    'A': 'left',
    'd': 'right',   'D': 'right',
};

const btnForDir = {
    'forward':  document.getElementById('btn-up'),
    'backward': document.getElementById('btn-down'),
    'left':     document.getElementById('btn-left'),
    'right':    document.getElementById('btn-right'),
};

const heldKeys = new Set();

document.addEventListener('keydown', (e) => {
    const dir = keyMap[e.key];
    if (!dir || heldKeys.has(e.key)) return;
    heldKeys.add(e.key);
    if (btnForDir[dir]) btnForDir[dir].classList.add('d-btn-active');
    sendManual(dir);
});

document.addEventListener('keyup', (e) => {
    const dir = keyMap[e.key];
    if (!dir) return;
    heldKeys.delete(e.key);
    if (btnForDir[dir]) btnForDir[dir].classList.remove('d-btn-active');
    // Only send stop if no other movement key is still held
    const stillMoving = [...heldKeys].some(k => keyMap[k]);
    if (!stillMoving) sendManual('stop');
});

// =============================================================
// RADAR CANVAS
// =============================================================

const radarCanvas = document.getElementById('radarCanvas');
const ctx         = radarCanvas ? radarCanvas.getContext('2d') : null;
let   radarHeading = 0;
let   radarFront   = 9.9;
let   radarLeft    = 9.9;
let   radarRight   = 9.9;

const MAX_RANGE_M  = 5.0;   // metres shown at canvas edge
const W = radarCanvas ? radarCanvas.width  : 160;
const H = radarCanvas ? radarCanvas.height : 160;
const CX = W / 2, CY = H / 2;
const R  = (W / 2) - 8;     // radius of radar circle in px

function mToRadius(metres) {
    return Math.min(metres / MAX_RANGE_M, 1.0) * R;
}

function drawRadarFrame() {
    if (!ctx) return;
    ctx.clearRect(0, 0, W, H);

    // Background
    ctx.fillStyle = '#000';
    ctx.beginPath(); ctx.arc(CX, CY, R, 0, Math.PI * 2); ctx.fill();

    // Range rings
    ctx.strokeStyle = '#1a3a1a';
    ctx.lineWidth = 1;
    [0.25, 0.5, 0.75, 1.0].forEach(f => {
        ctx.beginPath();
        ctx.arc(CX, CY, R * f, 0, Math.PI * 2);
        ctx.stroke();
    });

    // Cross-hairs
    ctx.strokeStyle = '#1a3a1a';
    ctx.lineWidth = 0.5;
    ctx.beginPath(); ctx.moveTo(CX - R, CY); ctx.lineTo(CX + R, CY); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(CX, CY - R); ctx.lineTo(CX, CY + R); ctx.stroke();

    // Outer ring
    ctx.strokeStyle = '#2979ff';
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(CX, CY, R, 0, Math.PI * 2); ctx.stroke();
}

function obstacleColor(dist) {
    if (dist < 0.2) return '#ff1744';   // danger — matches LiDAR block threshold
    if (dist < 1.0) return '#ffea00';   // caution
    return '#00e676';                   // clear
}

function drawObstacleBlip(angleDeg, distM, label) {
    if (!ctx) return;
    const clampedDist = Math.min(distM, MAX_RANGE_M);
    const r    = mToRadius(clampedDist);
    const rad  = (angleDeg - 90) * (Math.PI / 180);  // 0° = up
    const bx   = CX + r * Math.cos(rad);
    const by   = CY + r * Math.sin(rad);
    const col  = obstacleColor(distM);

    // Glow
    ctx.shadowBlur  = 12;
    ctx.shadowColor = col;

    // Blip dot
    ctx.fillStyle = col;
    ctx.beginPath();
    ctx.arc(bx, by, distM < 1.5 ? 6 : 4, 0, Math.PI * 2);
    ctx.fill();

    ctx.shadowBlur = 0;

    // Distance label
    ctx.fillStyle = col;
    ctx.font = 'bold 9px monospace';
    ctx.textAlign = 'center';
    ctx.fillText(distM < 9.9 ? distM.toFixed(1) + 'm' : '—', bx, by - 9);

    // Sector label
    ctx.fillStyle = '#aaa';
    ctx.font = '8px monospace';
    ctx.fillText(label, bx, by + 14);
}

function drawHeadingArrow(heading) {
    if (!ctx) return;
    const rad    = (heading - 90) * (Math.PI / 180);
    const tipLen = R * 0.55;
    const tx = CX + tipLen * Math.cos(rad);
    const ty = CY + tipLen * Math.sin(rad);

    // Arrow shaft
    ctx.strokeStyle = '#2979ff';
    ctx.lineWidth   = 2;
    ctx.shadowBlur  = 8;
    ctx.shadowColor = '#2979ff';
    ctx.beginPath();
    ctx.moveTo(CX, CY);
    ctx.lineTo(tx, ty);
    ctx.stroke();

    // Arrowhead
    const headLen = 8;
    const headAngle = Math.PI / 7;
    ctx.beginPath();
    ctx.moveTo(tx, ty);
    ctx.lineTo(
        tx - headLen * Math.cos(rad - headAngle),
        ty - headLen * Math.sin(rad - headAngle)
    );
    ctx.moveTo(tx, ty);
    ctx.lineTo(
        tx - headLen * Math.cos(rad + headAngle),
        ty - headLen * Math.sin(rad + headAngle)
    );
    ctx.stroke();
    ctx.shadowBlur = 0;

    // "N" north label
    const nr = (0 - 90) * (Math.PI / 180);
    const nx = CX + (R - 4) * Math.cos(nr);
    const ny = CY + (R - 4) * Math.sin(nr);
    ctx.fillStyle   = '#555';
    ctx.font        = 'bold 9px monospace';
    ctx.textAlign   = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('N', nx, ny);
    ctx.textBaseline = 'alphabetic';
}

// Draws the full radar scene
window.drawRadar = function (front, left, right) {
    radarFront = (front !== undefined) ? front : radarFront;
    radarLeft  = (left  !== undefined) ? left  : radarLeft;
    radarRight = (right !== undefined) ? right : radarRight;

    drawRadarFrame();

    // Obstacle blips — angles relative to rover body (heading = up)
    // Front = 0°, Left = 270° (−90°), Right = 90°
    drawObstacleBlip(0,    radarFront, 'FWD');
    drawObstacleBlip(270,  radarLeft,  'LFT');
    drawObstacleBlip(90,   radarRight, 'RGT');

    drawHeadingArrow(radarHeading);

    // Update label
    const lbl = document.getElementById('radar-gps-data');
    if (lbl) {
        const shield = (radarFront < 0.2 || radarLeft < 0.2 || radarRight < 0.2)
            ? '🚨 OBSTACLE NEAR' : 'SHIELD: ACTIVE';
        lbl.innerText = `HDG: ${radarHeading.toFixed(1)}° | ${shield}`;
    }
};

window.setRadarHeading = function (heading) {
    radarHeading = heading;
    window.drawRadar();
};

// Initial blank draw
window.drawRadar(9.9, 9.9, 9.9);

// Heartbeat refresh at 10 Hz to keep radar smooth even without LiDAR data
setInterval(() => { if (ctx) window.drawRadar(); }, 100);