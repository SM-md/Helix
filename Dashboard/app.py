"""
app.py — Helix Autonomous Mangrove Planter — Backend
=====================================================
Architecture (all asyncio, no threads except blocking I/O calls):

  gps_loop()        — reads /dev/ttyAMA0, broadcasts GPS packets
  compass_loop()    — reads QMC5883L on I2C-1, updates global heading
  esp32_rx_loop()   — reads ESP32 serial, broadcasts voltage + ACK lines
  lidar_loop()      — reads YDLidar, broadcasts radar packets
  auto_nav_loop()   — autonomous waypoint navigation state machine
  websocket_endpoint() — receives commands from dashboard
"""

import asyncio
import json
import math
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pynmea2
import serial
import uvicorn
from smbus2 import SMBus
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

try:
    import PyLidar3
    LIDAR_AVAILABLE = True
except ImportError:
    LIDAR_AVAILABLE = False
    print("[WARN] PyLidar3 not installed — LiDAR disabled.")

# =============================================================
# PORT DISCOVERY
# =============================================================

def auto_discover_ports():
    esp = None
    lidar = None
    print("\n[SYS] Auto-discovering serial ports...")
    candidates = [
        '/dev/ttyUSB0', '/dev/ttyUSB1', '/dev/ttyUSB2',
        '/dev/ttyACM0', '/dev/ttyACM1',
    ]
    for port in candidates:
        if not os.path.exists(port):
            continue
        try:
            ser = serial.Serial(port, 115200, timeout=1)
            ser.setDTR(True)
            ser.setRTS(True)
            time.sleep(1.5)

            # Probe for YDLidar
            ser.write(b'\xA5\x60')
            time.sleep(0.5)
            data = ser.read(100)
            ser.write(b'\xA5\x65')

            if b'\xaa\x55' in data:
                print(f"  -> YDLidar  on {port}")
                lidar = port
            elif b'rst:' in data or b'boot:' in data or b'ESP32' in data:
                print(f"  -> ESP32    on {port} (boot msg)")
                esp = port
            else:
                ser.reset_input_buffer()
                ser.write(b'<S>')
                time.sleep(0.3)
                d2 = ser.read(100)
                if b'ACK' in d2 or b'<V,' in d2:
                    print(f"  -> ESP32    on {port} (ping ACK)")
                    esp = port
            ser.close()
        except Exception:
            pass

    if not esp:
        print("  -> [WARNING] ESP32 not found.")
    return esp, lidar

ESP32_PORT, LIDAR_PORT = auto_discover_ports()
GPS_PORT = '/dev/ttyAMA0'

# =============================================================
# SHARED STATE
# =============================================================

robot_mode        = "MANUAL"
active_websockets: list[WebSocket] = []
serial_buffer     = ""
radar_data        = {"front": 9.9, "left": 9.9, "right": 9.9}
mangrove_total    = 14
mangroves_planted = 0

gps_state = {
    "lat": 0.0,
    "lon": 0.0,
    "heading": 0.0, # Now driven purely by the Magnetic Compass
    "speed_knots": 0.0,
    "fix": False,
}

auto_state = {
    "active": False,
    "mission_queue": [],
    "current_index": 0,
    "phase": "idle",
    "is_planting": False,
}

# Set by esp32_rx_loop when "ACK: Planting sequence complete." is received.
# auto_nav_loop waits on this instead of a hardcoded sleep so the rover
# only moves on after the ESP32 confirms the full ~28-second sequence is done.
plant_ack_event: asyncio.Event = asyncio.Event()

cmd_queue: asyncio.Queue = asyncio.Queue()

# =============================================================
# ESP32 SERIAL PORT
# =============================================================

try:
    esp32 = serial.Serial(ESP32_PORT, 115200, timeout=0) if ESP32_PORT else None
    if esp32:
        print(f"[ESP32] Serial open on {ESP32_PORT}")
except Exception as e:
    print(f"[ESP32] Could not open serial: {e}")
    esp32 = None

def send_esp32(cmd: str):
    if esp32 and esp32.is_open:
        try:
            esp32.write(f"{cmd}\n".encode())
            esp32.flush()
        except Exception as e:
            print(f"[ESP32 TX] {e}")

# =============================================================
# HELPERS
# =============================================================

async def broadcast(payload: dict):
    dead = []
    for ws in list(active_websockets):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in active_websockets:
            active_websockets.remove(ws)

def haversine_m(lat1, lon1, lat2, lon2) -> float:
    R = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a  = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def bearing_to(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x  = math.sin(dl) * math.cos(p2)
    y  = math.cos(p1)*math.sin(p2) - math.sin(p1)*math.cos(p2)*math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360

def angle_diff(cur: float, tgt: float) -> float:
    return (tgt - cur + 540) % 360 - 180

# =============================================================
# BACKGROUND TASK 1 — MAGNETIC COMPASS (QMC5883L)
# =============================================================

# Compass calibration constants
# Declination : +0.8 deg east (Cebu City)
# Mount offset: +115 deg (chip physically rotated vs rover forward axis)
# Verified against phone compass 2025 -- no hard-iron correction needed.
COMPASS_DECLINATION  = 0.8
COMPASS_MOUNT_OFFSET = 115

async def compass_loop():
    try:
        bus = SMBus(1)
        address = 0x0D

        # Wake up QMC5883L and set to Continuous Measurement
        bus.write_byte_data(address, 0x0B, 0x01)
        bus.write_byte_data(address, 0x09, 0x1D)
        print("[COMPASS] QMC5883L Compass Armed on I2C-1")
    except Exception as e:
        print(f"[COMPASS ERROR] {e} - Retrying...")
        return

    while True:
        try:
            # Read 6 raw bytes
            data = await asyncio.to_thread(bus.read_i2c_block_data, address, 0x00, 6)

            x = data[0] | (data[1] << 8)
            y = data[2] | (data[3] << 8)
            z = data[4] | (data[5] << 8)

            if x > 32767: x -= 65536
            if y > 32767: y -= 65536
            if z > 32767: z -= 65536

            heading_rad = math.atan2(y, x)
            heading_deg = math.degrees(heading_rad)
            if heading_deg < 0:
                heading_deg += 360.0
            heading_deg = (heading_deg + COMPASS_DECLINATION + COMPASS_MOUNT_OFFSET) % 360

            gps_state["heading"] = heading_deg
        except Exception:
            pass

        await asyncio.sleep(0.05) # Run at 20Hz

# =============================================================
# BACKGROUND TASK 2 — GPS READER (Coordinates only)
# =============================================================

async def gps_loop():
    try:
        gps_ser = await asyncio.to_thread(serial.Serial, GPS_PORT, 115200, timeout=0.1)
        print(f"[GPS] Connected on {GPS_PORT}")
    except Exception as e:
        print(f"[GPS] Failed to open {GPS_PORT}: {e}")
        return

    while True:
        try:
            line = await asyncio.to_thread(gps_ser.readline)
            line = line.decode('ascii', errors='replace').strip()

            if line.startswith(('$GPRMC', '$GNRMC')):
                msg = pynmea2.parse(line)
                if msg.status == 'A':
                    gps_state["lat"] = msg.latitude
                    gps_state["lon"] = msg.longitude
                    gps_state["fix"] = True
                    spd = float(msg.spd_over_grnd) if msg.spd_over_grnd else 0.0
                    gps_state["speed_knots"] = spd

                    await broadcast({
                        "type":    "gps",
                        "lat":     gps_state["lat"],
                        "lon":     gps_state["lon"],
                        "heading": gps_state["heading"], # Uses the live magnetic compass
                        "speed":   round(spd, 2),
                        "fix":     True,
                    })
        except pynmea2.ParseError: pass
        except Exception: await asyncio.sleep(0.1)

# =============================================================
# BACKGROUND TASK 3 — ESP32 RX READER
# =============================================================

async def esp32_rx_loop():
    """
    Reads lines from the ESP32 serial port without blocking the event loop.

    Two bugs fixed vs. the original:
      1. esp32.in_waiting and esp32.read() are synchronous / blocking.
         Running them bare inside an async function starves the entire
         asyncio event loop, which is why WebSocket commands were never
         processed and no telemetry appeared in the dashboard.
         Fix: every blocking serial call is now wrapped in
         asyncio.to_thread() so it runs in a thread pool executor.

      2. "Planting sequence complete" is buried inside an "ACK:" line
         so it was already forwarded, but only when the loop could
         actually run — which it couldn't due to bug 1 above.
    """
    global serial_buffer
    if not esp32:
        print("[ESP32-RX] No serial port — loop skipped.")
        return

    def _read_chunk() -> str:
        """Blocking helper: read whatever bytes are waiting."""
        waiting = esp32.in_waiting
        if waiting > 0:
            return esp32.read(waiting).decode("utf-8", errors="ignore")
        return ""

    while True:
        try:
            chunk = await asyncio.to_thread(_read_chunk)

            if chunk:
                serial_buffer += chunk

                while '\n' in serial_buffer:
                    line, serial_buffer = serial_buffer.split('\n', 1)
                    line = line.strip()
                    if not line:
                        continue

                    # ── Voltage packet: <V,12.34> ──────────────────
                    if line.startswith("<V,") and line.endswith(">"):
                        try:
                            v = float(line[3:-1])
                            await broadcast({"type": "telemetry", "voltage": v})
                        except ValueError:
                            pass

                    # ── ACK / ERROR / WARN log lines ───────────────
                    elif line.startswith(("ACK:", "[ERROR]", "[WARN]")):
                        await broadcast({"type": "esp32_log", "message": line})
                        # Signal the nav loop that planting is done so it
                        # can advance to the next waypoint immediately
                        # instead of waiting for a fixed 20-second timer.
                        if "Planting sequence complete" in line:
                            plant_ack_event.set()

        except Exception as e:
            print(f"[ESP32-RX] Error: {e}")
            await asyncio.sleep(0.5)

        # Yield to event loop — 20 ms gives ~50 reads/sec, plenty for 115 200 baud
        await asyncio.sleep(0.02)

# =============================================================
# BACKGROUND TASK 4 — LIDAR READER
# =============================================================

async def lidar_loop():
    global radar_data
    if not LIDAR_PORT or not LIDAR_AVAILABLE: return
    try:
        ser = serial.Serial(LIDAR_PORT, 115200, timeout=1)
        ser.setDTR(False)
        ser.write(b'\xA5\x65')
        time.sleep(0.5)
        ser.reset_input_buffer()
        lidar = PyLidar3.YdLidarX4(LIDAR_PORT)
        lidar._s = ser
        lidar._baudrate = 115200
        lidar._is_connected = True
        lidar._is_scanning  = False
        gen = lidar.StartScanning()
        print("[LIDAR] 270° shield active.")
    except Exception: return

    while True:
        try:
            scan = await asyncio.to_thread(next, gen)
            front = [scan.get(i, 0) for i in range(45, 135)]
            left  = [scan.get(i, 0) for i in range(315, 360)] + [scan.get(i, 0) for i in range(0, 45)]
            right = [scan.get(i, 0) for i in range(135, 225)]
            radar_data["front"] = min((d/1000 for d in front if d > 0), default=9.9)
            radar_data["left"]  = min((d/1000 for d in left  if d > 0), default=9.9)
            radar_data["right"] = min((d/1000 for d in right if d > 0), default=9.9)
            await broadcast({"type": "radar", **radar_data})
        except StopIteration: break
        except Exception: pass
        await asyncio.sleep(0.001)

# =============================================================
# BACKGROUND TASK 5 — COMMAND PROCESSOR
# =============================================================

async def command_processor():
    global robot_mode, mangroves_planted, mangrove_total, auto_state

    while True:
        data = await cmd_queue.get()
        cmd  = data.get("command")

        try:
            if cmd == "heartbeat": pass
            elif cmd == "emergency_stop":
                robot_mode = "MANUAL"
                auto_state["active"] = False
                send_esp32("<S>")
                await broadcast({"type": "mode", "mode": "MANUAL"})

            elif cmd == "set_mode":
                robot_mode = data.get("mode", "MANUAL")
                send_esp32("<S>")
                if robot_mode == "MANUAL": auto_state["active"] = False
                await broadcast({"type": "mode", "mode": robot_mode})

            elif cmd == "manual_override":
                # Bug fixed: previously this silently dropped drive commands
                # whenever robot_mode was not exactly "MANUAL". If the mode
                # badge got out of sync between client and server the D-pad
                # appeared to do nothing. Now we always process the command
                # and, if the rover is in AUTO, force it back to MANUAL first
                # so the operator can always regain manual control.
                if robot_mode != "MANUAL":
                    robot_mode = "MANUAL"
                    auto_state["active"] = False
                    await broadcast({"type": "mode", "mode": "MANUAL"})

                direction = data.get("direction", "stop")
                speed_raw = max(0, min(245, int(data.get("speed", 100) / 100 * 245)))

                # Left/right = full tank pivot: both motors at 245 PWM
                # in opposite directions for the tightest possible turn.
                PIVOT_SPEED = 245

                if direction == "forward" and radar_data["front"] < 0.2:
                    pass  # Obstacle blocking — do not drive forward
                elif direction == "forward":  send_esp32(f"<D,{speed_raw},{speed_raw}>")
                elif direction == "backward": send_esp32(f"<D,-{speed_raw},-{speed_raw}>")
                elif direction == "left":     send_esp32(f"<D,-{PIVOT_SPEED},{PIVOT_SPEED}>")  # left motor back, right motor fwd
                elif direction == "right":    send_esp32(f"<D,{PIVOT_SPEED},-{PIVOT_SPEED}>")  # left motor fwd, right motor back
                elif direction == "stop":     send_esp32("<S>")

            elif cmd == "manual_plant":
                # Force MANUAL mode if somehow out of sync
                if robot_mode != "MANUAL":
                    robot_mode = "MANUAL"
                    auto_state["active"] = False
                    send_esp32("<S>")
                    await broadcast({"type": "mode", "mode": "MANUAL"})

                send_esp32("<P>")
                mangroves_planted += 1
                # Wrap: once all mangroves are planted the revolver has
                # cycled through all 14 slots and is ready for a fresh
                # reload — reset the counter so it never goes negative.
                if mangroves_planted >= mangrove_total:
                    mangroves_planted = 0
                await broadcast({
                    "type": "planter",
                    "mangroves_remaining": mangrove_total - mangroves_planted,
                    "mangroves_planted":   mangroves_planted,
                    "mangrove_total":      mangrove_total,
                })

            elif cmd == "start_mission":
                queue = data.get("queue", [])
                if queue and not auto_state["is_planting"]:
                    robot_mode = "AUTO"
                    auto_state.update({
                        "mission_queue": queue, "current_index": 0,
                        "active": True, "phase": "turning", "is_planting": False,
                    })
                    await broadcast({"type": "mode", "mode": "AUTO"})
                    await broadcast({"type": "auto_status", "phase": "turning", "message": f"Mission started — {len(queue)} waypoints."})

            elif cmd == "pause_mission":
                auto_state["active"] = False
                send_esp32("<S>")
                await broadcast({"type": "auto_status", "phase": "paused", "message": "Mission paused."})

            elif cmd == "resume_mission":
                if robot_mode == "AUTO" and not auto_state["is_planting"]:
                    auto_state["active"] = True
                    auto_state["phase"]  = "turning"

            elif cmd == "abort_mission":
                robot_mode = "MANUAL"
                auto_state["active"] = False
                send_esp32("<S>")
                await broadcast({"type": "mode", "mode": "MANUAL"})
                await broadcast({"type": "auto_status", "phase": "idle", "message": "Mission aborted."})

            elif cmd == "reset_mangroves":
                mangroves_planted = 0
                await broadcast({"type": "planter", "mangroves_remaining": mangrove_total, "mangroves_planted": 0, "mangrove_total": mangrove_total})

            elif cmd == "set_mangrove_total":
                mangrove_total = int(data.get("total", mangrove_total))
                mangroves_planted = 0
                await broadcast({"type": "planter", "mangroves_remaining": mangrove_total, "mangroves_planted": 0, "mangrove_total": mangrove_total})

        except Exception as e: print(f"[CMD] Error processing '{cmd}': {e}")
        finally: cmd_queue.task_done()

# =============================================================
# BACKGROUND TASK 6 — AUTONOMOUS NAVIGATION
# =============================================================

ARRIVE_RADIUS_M = 2.0
TURN_SPEED      = 120
DRIVE_SPEED     = 245
HEADING_TOL_DEG = 8.0

async def auto_nav_loop():
   	global robot_mode, auto_state, mangroves_planted, mangrove_total

    while True:
        await asyncio.sleep(0.25)
        if robot_mode != "AUTO" or not auto_state["active"]: continue

        queue = auto_state["mission_queue"]
        idx   = auto_state["current_index"]

        if idx >= len(queue):
            auto_state["active"] = False
            auto_state["phase"]  = "complete"
            send_esp32("<S>")
            await broadcast({"type": "auto_status", "phase": "complete", "message": "Mission complete!"})
            continue

        if not gps_state["fix"]:
            await broadcast({"type": "auto_status", "phase": "waiting_gps", "message": "Waiting for GPS fix..."})
            continue

        wp          = queue[idx]
        dist        = haversine_m(gps_state["lat"], gps_state["lon"], wp["lat"], wp["lng"])
        req_bearing = bearing_to(gps_state["lat"], gps_state["lon"], wp["lat"], wp["lng"])

        # Uses real compass heading to calculate turning error!
        hdg_err     = angle_diff(gps_state["heading"], req_bearing)

        await broadcast({
            "type":       "auto_status",
            "phase":      auto_state["phase"],
            "wp_index":   idx,
            "wp_type":    wp["type"],
            "distance_m": round(dist, 1),
            "bearing":    round(req_bearing, 1),
            "heading":    round(gps_state["heading"], 1),
            "hdg_error":  round(hdg_err, 1),
        })

        if dist < ARRIVE_RADIUS_M:
            send_esp32("<S>")
            await asyncio.sleep(0.5)
            if wp["type"] == "plant" and not auto_state["is_planting"]:
                auto_state["phase"]       = "planting"
                auto_state["is_planting"] = True
                await broadcast({"type": "auto_status", "phase": "planting", "message": f"Planting at WP {idx+1}..."})
                send_esp32("<P>")
                mangroves_planted += 1
                # Wrap: revolver reloaded — never let the display go negative
                if mangroves_planted >= mangrove_total:
                    mangroves_planted = 0
                await broadcast({"type": "planter", "mangroves_remaining": mangrove_total - mangroves_planted, "mangroves_planted": mangroves_planted, "mangrove_total": mangrove_total})

                # Wait for the ESP32 ACK ("ACK: Planting sequence complete.")
                # rather than a fixed 20s sleep. The actual sequence takes
                # ~27.8 s (1500 + 300 + 25000 + 1000 ms). A 45-second
                # safety timeout prevents the rover from getting permanently
                # stuck if the ACK is ever lost or the ESP32 resets.
                plant_ack_event.clear()
                try:
                    await asyncio.wait_for(plant_ack_event.wait(), timeout=45.0)
                except asyncio.TimeoutError:
                    print(f"[AUTO] WARNING: No plant ACK after 45 s — continuing anyway (WP {idx+1})")

                auto_state["is_planting"] = False
            auto_state["current_index"] += 1
            auto_state["phase"] = "turning"
            await broadcast({"type": "auto_wp_complete", "completed_index": idx})
            continue

        if radar_data["front"] < 0.2:
            send_esp32("<S>")
            auto_state["phase"] = "blocked"
            await broadcast({"type": "auto_status", "phase": "blocked", "message": "Obstacle ahead — waiting..."})
            await asyncio.sleep(1.0)
            continue

        if abs(hdg_err) > HEADING_TOL_DEG:
            auto_state["phase"] = "turning"
            if hdg_err > 0: send_esp32(f"<D,{TURN_SPEED},0>")
            else: send_esp32(f"<D,0,{TURN_SPEED}>")
            await asyncio.sleep(0.2)
            continue

        auto_state["phase"] = "driving"
        send_esp32(f"<D,{DRIVE_SPEED},{DRIVE_SPEED}>")

# =============================================================
# OFFLINE TILE SERVER & FASTAPI SETUP
# =============================================================

# CRITICAL: Use __file__ so TILE_DIR always resolves relative to
# app.py itself — not to whatever directory you launch from.
# Path("static/tiles") was the old buggy version that caused the
# black map when launched from the wrong working directory.
TILE_DIR = Path(__file__).parent / "static" / "tiles"
TILE_DIR.mkdir(parents=True, exist_ok=True)
print(f"[TILE] Cache directory: {TILE_DIR.resolve()}")
print(f"[TILE] Tiles on disk:   {sum(1 for _ in TILE_DIR.rglob('*.png'))}")

# ESRI World Imagery — only contacted when a tile is NOT on disk.
# Note: ESRI URL uses /tile/{z}/{y}/{x} — y and x are SWAPPED
ESRI_TILE_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services"
    "/World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
ESRI_HEADERS = {
    "User-Agent": "HelixMangroveRover/1.0",
    "Referer":    "https://www.arcgis.com/",
}

# Transparent 1×1 PNG fallback — only used when completely offline
# AND the tile was never downloaded. Should never appear if
# download_tiles.py was run successfully before field deployment.
EMPTY_TILE = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
    b'\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
    b'\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01'
    b'\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(compass_loop(),      name="compass")
    asyncio.create_task(gps_loop(),          name="gps")
    asyncio.create_task(esp32_rx_loop(),     name="esp32_rx")
    asyncio.create_task(lidar_loop(),        name="lidar")
    asyncio.create_task(command_processor(), name="cmd_proc")
    asyncio.create_task(auto_nav_loop(),     name="auto_nav")
    yield

app = FastAPI(lifespan=lifespan)

_BASE = Path(__file__).parent
app.mount("/css",     StaticFiles(directory=str(_BASE / "css")),     name="css")
app.mount("/js",      StaticFiles(directory=str(_BASE / "js")),      name="js")
app.mount("/leaflet", StaticFiles(directory=str(_BASE / "leaflet")), name="leaflet")

@app.get("/tiles/{z}/{x}/{y}.png")
async def serve_tile(z: int, x: int, y: int):
    """
    Tile server with offline-first priority.

    Flow:
      1. Tile on disk  →  serve instantly, no network touch at all
      2. Tile missing + online  →  fetch ESRI (2s timeout), cache, serve
      3. Tile missing + offline →  return transparent tile immediately

    """
    p = TILE_DIR / str(z) / str(x) / f"{y}.png"

    # ── Cache hit — serve from disk instantly, no network ─────
    if p.exists():
        return Response(
            p.read_bytes(),
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )

    # ── Cache miss — try ESRI with a SHORT timeout ────────────
    # 2 second timeout means offline failures are detected fast.
    # The old 10s timeout caused 20 tiles × 10s = 200s black map.
    url = ESRI_TILE_URL.format(z=z, y=y, x=x)
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(url, headers=ESRI_HEADERS)

        if r.status_code == 200:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(r.content)
            print(f"[TILE] fetched+cached {z}/{x}/{y}")
            return Response(
                r.content,
                media_type="image/png",
                headers={"Cache-Control": "no-store"},
            )
        else:
            print(f"[TILE] ESRI {r.status_code} for {z}/{x}/{y}")

    except Exception:
        # Offline or unreachable — fail silently and fast
        pass

    # ── Not cached + offline — transparent tile ───────────────
    # no-store ensures the browser asks again next time rather than
    # permanently caching the empty tile
    return Response(
        EMPTY_TILE,
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )

@app.get("/")
async def serve_dashboard():
    return FileResponse(str(Path(__file__).parent / "index.html"))

@app.get("/calibrate")
async def serve_calibrate():
    # Calibration tool served on same host — auto-connects to WS, no IP entry needed
    return FileResponse(str(Path(__file__).parent / "calibrate.html"))

# =============================================================
# WEBSOCKET
# =============================================================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_websockets.append(websocket)
    print(f"[WS] Client connected ({len(active_websockets)} total)")

    try:
        await websocket.send_json({"type": "mode", "mode": robot_mode})
        await websocket.send_json({
            "type": "planter",
            "mangroves_remaining": mangrove_total - mangroves_planted,
            "mangroves_planted":   mangroves_planted,
            "mangrove_total":      mangrove_total,
        })
        await websocket.send_json({
            "type":    "gps",
            "lat":     gps_state["lat"],
            "lon":     gps_state["lon"],
            "heading": gps_state["heading"],
            "speed":   gps_state["speed_knots"],
            "fix":     gps_state["fix"],
        })
        # Send last-known radar so the display isn't blank on reconnect
        await websocket.send_json({"type": "radar", **radar_data})
        # Ask ESP32 to send a fresh voltage reading immediately
        send_esp32("<V?>")
    except Exception:
        pass

    try:
        async for raw in websocket.iter_text():
            try:
                data = json.loads(raw)
                await cmd_queue.put(data)
            except json.JSONDecodeError: pass
    except WebSocketDisconnect: pass
    except Exception as e: print(f"[WS] Unexpected error: {e}")
    finally:
        if websocket in active_websockets: active_websockets.remove(websocket)
        print(f"[WS] Client disconnected ({len(active_websockets)} remaining)")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)