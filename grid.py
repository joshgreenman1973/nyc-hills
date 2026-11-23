"""The tile grid shared by fetch_dem.py and build.py."""
import math

# A box around the five boroughs.
BBOX = (-74.2600, 40.4950, -73.6990, 40.9160)  # west, south, east, north
TILE_PX = 2000
# Pixels must be square in degrees: the ImageServer silently stretches the
# box to keep them square, which would misplace every tile.
LAT_STEP = 2.5e-5           # degrees per pixel: about 2.8 m north-south
LON_STEP = 2.5e-5           # and 2.1 m east-west at New York's latitude
TILE_LAT = TILE_PX * LAT_STEP
TILE_LON = TILE_PX * LON_STEP


def tile_of(lon, lat):
    return (int(math.floor((lon - BBOX[0]) / TILE_LON)), int(math.floor((lat - BBOX[1]) / TILE_LAT)))


def tile_bounds(col, row):
    w = BBOX[0] + col * TILE_LON
    s = BBOX[1] + row * TILE_LAT
    return (round(w, 7), round(s, 7), round(w + TILE_LON, 7), round(s + TILE_LAT, 7))
