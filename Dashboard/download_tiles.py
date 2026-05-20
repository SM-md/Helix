#!/usr/bin/env python3
"""
download_tiles.py — Offline Satellite Map Tile Cache Builder
=============================================================
Run this ONCE on the Pi while connected to WiFi/internet.
Downloads ESRI World Imagery (satellite) tiles for your deployment
area and saves them into  static/tiles/{z}/{x}/{y}.png

IMPORTANT — Tile URL format difference:
  ESRI uses  /tile/{z}/{y}/{x}  (y and x are SWAPPED vs OSM)
  The tiles are saved as  static/tiles/{z}/{x}/{y}.png to match
  what FastAPI and Leaflet expect.  The swap only applies to the
  download URL, not the file path.

"""

import math
import time
import requests
from pathlib import Path

# ── CONFIGURE THIS FOR YOUR DEPLOYMENT AREA ──────────────────
#
#   Point 1: 10.329715, 123.881067  (north site)
#   Point 2: 10.201533, 123.758158  (south site)
#   Distance between them: ~19.6 km
#
# Center is the midpoint between both points.
# Buffer of 0.075° gives ~600 m of padding beyond each point.
#
CENTER_LAT  = 10.265624   # midpoint between your two sites
CENTER_LON  = 123.819612  # midpoint between your two sites
ZOOM_MIN    = 10          # zoomed-out overview
ZOOM_MAX    = 18          # max detail — zoom 18 shows ~0.6 m/pixel
BUFFER_DEG  = 0.075       # covers both points + ~600 m padding on all sides
OUTPUT_DIR  = Path("static/tiles")
# ─────────────────────────────────────────────────────────────

# ESRI World Imagery — note {y}/{x} order (not {x}/{y} like OSM)
TILE_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services"
    "/World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
HEADERS = {
    "User-Agent": "HelixMangroveRover/1.0",
    "Referer":    "https://www.arcgis.com/",
}
REQUEST_DELAY = 0.1


def deg2tile(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    """Convert lat/lon to OSM tile x/y at the given zoom level."""
    lat_r = math.radians(lat)
    n     = 2 ** zoom
    x     = int((lon + 180) / 360 * n)
    y     = int((1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 * n)
    return x, y


def tile_count_estimate() -> int:
    total = 0
    for z in range(ZOOM_MIN, ZOOM_MAX + 1):
        x_min, y_max = deg2tile(CENTER_LAT - BUFFER_DEG, CENTER_LON - BUFFER_DEG, z)
        x_max, y_min = deg2tile(CENTER_LAT + BUFFER_DEG, CENTER_LON + BUFFER_DEG, z)
        total += (x_max - x_min + 1) * (y_max - y_min + 1)
    return total


def download_tiles():
    lat_min = CENTER_LAT - BUFFER_DEG
    lat_max = CENTER_LAT + BUFFER_DEG
    lon_min = CENTER_LON - BUFFER_DEG
    lon_max = CENTER_LON + BUFFER_DEG

    estimate = tile_count_estimate()
    print(f"\n{'═'*50}")
    print(f"  Helix Offline Satellite Tile Downloader")
    print(f"  Source  : ESRI World Imagery (satellite)")
    print(f"  Center  : {CENTER_LAT}, {CENTER_LON}")
    print(f"  Buffer  : ±{BUFFER_DEG}° (~{BUFFER_DEG * 111:.1f} km)")
    print(f"  Zooms   : {ZOOM_MIN} – {ZOOM_MAX}")
    print(f"  Est. tiles: ~{estimate:,}")
    print(f"  Output  : {OUTPUT_DIR.resolve()}")
    print(f"{'═'*50}\n")

    total_downloaded = 0
    total_skipped    = 0
    total_failed     = 0

    for z in range(ZOOM_MIN, ZOOM_MAX + 1):
        x_min, y_max = deg2tile(lat_min, lon_min, z)
        x_max, y_min = deg2tile(lat_max, lon_max, z)

        tile_count = (x_max - x_min + 1) * (y_max - y_min + 1)
        print(f"\n[Z={z:2d}] {tile_count:5,} tiles  "
              f"x:[{x_min}–{x_max}]  y:[{y_min}–{y_max}]")

        for x in range(x_min, x_max + 1):
            for y in range(y_min, y_max + 1):
                # File is saved as {z}/{x}/{y}.png — standard OSM/Leaflet layout
                out_path = OUTPUT_DIR / str(z) / str(x) / f"{y}.png"

                if out_path.exists():
                    total_skipped += 1
                    continue

                out_path.parent.mkdir(parents=True, exist_ok=True)

                # ESRI URL uses {z}/{y}/{x} — y and x are swapped vs the file path
                url = TILE_URL.format(z=z, y=y, x=x)

                downloaded = False
                for attempt in range(3):
                    try:
                        r = requests.get(url, headers=HEADERS, timeout=15)
                        if r.status_code == 200:
                            out_path.write_bytes(r.content)
                            total_downloaded += 1
                            print(f"  ✓ {z}/{x}/{y}  ({len(r.content):,} B)", end="\r")
                            downloaded = True
                            break
                        else:
                            print(f"  ! HTTP {r.status_code} on {z}/{x}/{y} — retrying...")
                            time.sleep(1)
                    except Exception as e:
                        print(f"  ! Attempt {attempt+1}/3 failed for {z}/{x}/{y}: {e}")
                        time.sleep(1)

                if not downloaded:
                    print(f"  ✗ FAILED {z}/{x}/{y} after 3 attempts")
                    total_failed += 1

                time.sleep(REQUEST_DELAY)

    print(f"\n\n{'═'*50}")
    print(f"  Downloaded : {total_downloaded:,}")
    print(f"  Skipped    : {total_skipped:,}  (already cached)")
    print(f"  Failed     : {total_failed:,}")
    print(f"  Tiles dir  : {OUTPUT_DIR.resolve()}")
    if total_failed == 0:
        print("All tiles cached — map will work fully offline.")
    else:
        print(f"{total_failed} tiles missing — re-run script to retry failures.")
    print(f"{'═'*50}\n")


if __name__ == "__main__":
    download_tiles()