"""Download the USGS 3DEP bare-earth elevation model for New York City in tiles.

Each tile is 2,000 x 2,000 pixels in plain latitude/longitude, about 2.5 m
per pixel, requested from the National Map's 3DEP ImageServer. Only tiles
that contain at least one street segment are fetched. Tiles already on disk
are skipped, so the script can be re-run after an interruption.

Fails loudly: any tile that can't be fetched after retries stops the run.
"""
import json, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor

from grid import TILE_PX, tile_bounds, tile_of, BBOX

RAW = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'raw')
OUT = os.path.join(RAW, 'dem')
URL = ('https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage'
       '?bbox={w},{s},{e},{n}&bboxSR=4326&imageSR=4326&size={px},{px}&format=tiff&pixelType=F32'
       '&compression=LZ77&noDataInterpretation=esriNoDataMatchAny'
       '&interpolation=RSP_BilinearInterpolation&f=json')


def needed_tiles():
    with open(os.path.join(RAW, 'centerline.geojson')) as f:
        gj = json.load(f)
    tiles = set()
    for feat in gj['features']:
        g = feat.get('geometry')
        if not g:
            continue
        lines = g['coordinates'] if g['type'] == 'MultiLineString' else [g['coordinates']]
        for line in lines:
            for lon, lat in line:
                tiles.add(tile_of(lon, lat))
    return sorted(tiles)


def fetch(tile):
    path = os.path.join(OUT, 'dem_%d_%d.tif' % tile)
    if os.path.exists(path) and os.path.getsize(path) > 100000:
        return tile, 'cached'
    w, s, e, n = tile_bounds(*tile)
    url = URL.format(w=w, s=s, e=e, n=n, px=TILE_PX)
    tmp = path + '.part'
    for attempt in range(5):
        # Ask for the export as JSON first: it reports the extent the server
        # actually used, which must match the request.
        r = subprocess.run(['curl', '-s', '-f', '-m', '180', url], capture_output=True, text=True)
        try:
            meta = json.loads(r.stdout)
            ext = meta['extent']
            got = (ext['xmin'], ext['ymin'], ext['xmax'], ext['ymax'])
            if max(abs(a - b) for a, b in zip(got, (w, s, e, n))) > 1e-6 or meta['width'] != TILE_PX or meta['height'] != TILE_PX:
                raise RuntimeError('server changed the tile extent for %s: %s' % (tile, got))
            r2 = subprocess.run(['curl', '-s', '-f', '-m', '180', '-o', tmp, meta['href']])
        except (ValueError, KeyError):
            r2 = None
        if r2 is not None and r2.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 100000:
            with open(tmp, 'rb') as fh:
                magic = fh.read(4)
            if magic in (b'II*\x00', b'MM\x00*'):
                os.replace(tmp, path)
                return tile, 'fetched'
        time.sleep(5 * (attempt + 1))
    raise RuntimeError('could not fetch DEM tile %s after 5 tries' % (tile,))


def main():
    os.makedirs(OUT, exist_ok=True)
    tiles = needed_tiles()
    print('tiles needed:', len(tiles), 'bbox', BBOX, flush=True)
    with ThreadPoolExecutor(4) as ex:
        for i, (tile, status) in enumerate(ex.map(fetch, tiles)):
            print(i + 1, tile, status, flush=True)
    missing = [t for t in tiles if not os.path.exists(os.path.join(OUT, 'dem_%d_%d.tif' % t))]
    if missing:
        sys.exit('missing tiles: %s' % missing)
    print('all tiles present')


if __name__ == '__main__':
    main()
