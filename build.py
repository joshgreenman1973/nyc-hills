"""Build the hill map's data: the grade of every walkable block in New York City.

Inputs (fetched separately, kept in raw/):
  raw/centerline.geojson  NYC Street Centerline (CSCL), NYC Open Data inkn-q76z
  raw/dem/*.tif           USGS 3DEP bare-earth elevation tiles (fetch_dem.py)

Outputs (data/):
  streets.json  every walkable segment, the elevation at each end and its
                geometry, packed as integer arrays for the map and the router
  stats.json    the findings the page quotes

Fails loudly if the street count, the share of segments with an elevation,
or the walking network's connectivity fall below floors.
"""
import json, math, os, sys, collections, datetime
import numpy as np
from PIL import Image
from numpy.lib.stride_tricks import sliding_window_view

from grid import TILE_PX, LAT_STEP, LON_STEP, tile_of, tile_bounds

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, 'raw')
DATA = os.path.join(HERE, 'data')

# Which centerline segments are walkable, and what kind they are.
# rw_type codes from the CSCL data dictionary.
KIND_STREET, KIND_PATH, KIND_STAIRS, KIND_RAISED = 0, 1, 2, 3
RW_KIND = {'1': KIND_STREET,   # street
           '10': KIND_STREET,  # alley
           '5': KIND_PATH,     # boardwalk
           '6': KIND_PATH,     # path or trail
           '7': KIND_STAIRS,   # step street
           '3': KIND_RAISED}   # bridge (only where pedestrians are allowed)
GROUND = '13'                  # level code for "at grade"
MAX_PIECE_M = 100.0            # long blocks are split so a hump in the middle shows
SIMPLIFY_M = 1.5               # Douglas-Peucker tolerance for drawing
BOROS = {'1': 'Manhattan', '2': 'Bronx', '3': 'Brooklyn', '4': 'Queens', '5': 'Staten Island'}

# Profile filtering. Each line is sampled every STEP_M metres, extended
# EXT_M past each end along its own direction, then:
#   1. a 3-sample median removes single bad readings;
#   2. a "closing" (running maximum, then running minimum, over FILL_M)
#      fills any dip narrower than FILL_M. The elevation model is bare
#      earth, so where a street crosses a highway or rail cut on a short
#      bridge, the model shows the cut, not the bridge. Closing bridges it.
#   3. an "opening" (running minimum, then maximum, over CUT_M) removes any
#      bump narrower than CUT_M, for the rare overpass left in the ground model.
STEP_M = 5.0
EXT_M = 60.0
FILL_M = 120.0
CUT_M = 40.0
# 4. any 10 m stretch of the filtered profile steeper than CLIFF is treated as
#    a wall, a bridge abutment or a cut edge, not a street, and the piece is
#    left ungraded. No paved New York street comes near this.
CLIFF = 0.35
# 5. pieces within NEAR_M of a highway, ramp, bridge, tunnel or any segment
#    off grade are flagged: the ground model can misread them, so they are
#    kept off the steepest-streets list and labeled on the map.
NEAR_M = 30.0

M_PER_DEG_LAT = 111_320.0
M_PER_DEG_LON = M_PER_DEG_LAT * math.cos(math.radians(40.70))
FT = 3.28084


def dist_m(a, b):
    return math.hypot((b[0] - a[0]) * M_PER_DEG_LON, (b[1] - a[1]) * M_PER_DEG_LAT)


def line_len(pts):
    return sum(dist_m(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def split_line(pts, n):
    """Cut a polyline into n pieces of equal length."""
    if n <= 1:
        return [pts]
    total = line_len(pts)
    targets = [total * k / n for k in range(1, n)]
    pieces, cur, acc, t = [], [pts[0]], 0.0, 0
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        seg = dist_m(a, b)
        while t < len(targets) and acc + seg >= targets[t] and seg > 0:
            f = (targets[t] - acc) / seg
            p = (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
            cur.append(p)
            pieces.append(cur)
            cur = [p]
            t += 1
        cur.append(b)
        acc += seg
    pieces.append(cur)
    return [p for p in pieces if len(p) >= 2]


def simplify(pts, tol):
    """Douglas-Peucker in local metres."""
    if len(pts) <= 2:
        return pts
    a, b = pts[0], pts[-1]
    ax, ay = a[0] * M_PER_DEG_LON, a[1] * M_PER_DEG_LAT
    bx, by = b[0] * M_PER_DEG_LON, b[1] * M_PER_DEG_LAT
    dx, dy = bx - ax, by - ay
    L = math.hypot(dx, dy)
    best, idx = -1, 0
    for i in range(1, len(pts) - 1):
        px, py = pts[i][0] * M_PER_DEG_LON, pts[i][1] * M_PER_DEG_LAT
        d = abs(dy * (px - ax) - dx * (py - ay)) / L if L > 0 else math.hypot(px - ax, py - ay)
        if d > best:
            best, idx = d, i
    if best <= tol:
        return [a, b]
    return simplify(pts[:idx + 1], tol)[:-1] + simplify(pts[idx:], tol)


class DEM:
    """Reads elevation (metres, NAVD88) from the cached tiles, many points at once."""

    def __init__(self):
        self.cache = collections.OrderedDict()

    def tile(self, key):
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        path = os.path.join(RAW, 'dem', 'dem_%d_%d.tif' % key)
        arr = np.array(Image.open(path), dtype=np.float32) if os.path.exists(path) else None
        if arr is not None:
            arr[(arr < -100) | (arr > 1000)] = np.nan
        self.cache[key] = arr
        if len(self.cache) > 16:
            self.cache.popitem(last=False)
        return arr

    def sample(self, lons, lats):
        out = np.full(len(lons), np.nan)
        keys = [tile_of(x, y) for x, y in zip(lons, lats)]
        for key in set(keys):
            arr = self.tile(key)
            if arr is None:
                continue
            m = np.array([k == key for k in keys])
            w, s, e, n = tile_bounds(*key)
            fx = (lons[m] - w) / LON_STEP - 0.5      # pixel centres; row 0 is north
            fy = (n - lats[m]) / LAT_STEP - 0.5
            x0 = np.clip(np.floor(fx).astype(int), 0, TILE_PX - 2)
            y0 = np.clip(np.floor(fy).astype(int), 0, TILE_PX - 2)
            tx = np.clip(fx - x0, 0, 1)
            ty = np.clip(fy - y0, 0, 1)
            v = ((arr[y0, x0] * (1 - tx) + arr[y0, x0 + 1] * tx) * (1 - ty) +
                 (arr[y0 + 1, x0] * (1 - tx) + arr[y0 + 1, x0 + 1] * tx) * ty)
            out[m] = v
        return out


def running(v, width, fn):
    """Running max or min over an odd window, edges padded with the end value."""
    h = width // 2
    p = np.pad(v, h, mode='edge')
    return fn(sliding_window_view(p, width), axis=1)


def odd(m):
    k = int(round(m / STEP_M))
    return k + 1 if k % 2 == 0 else k


FILL_W, CUT_W = odd(FILL_M), odd(CUT_M)


def profile(dem, pts):
    """Sample a line every STEP_M (plus EXT_M beyond each end), filter it, and
    return (distances, raw, filtered) for the part between the ends."""
    xy = np.array(pts)
    seg = np.hypot(np.diff(xy[:, 0]) * M_PER_DEG_LON, np.diff(xy[:, 1]) * M_PER_DEG_LAT)
    cum = np.concatenate([[0], np.cumsum(seg)])
    L = cum[-1]
    n = max(2, int(math.ceil(L / STEP_M)) + 1)
    s = np.linspace(0, L, n)
    lon = np.interp(s, cum, xy[:, 0])
    lat = np.interp(s, cum, xy[:, 1])
    # extensions along the end directions
    def ext(at, toward, sign):
        b = min(10.0, L)
        px, py = np.interp(at, cum, xy[:, 0]), np.interp(at, cum, xy[:, 1])
        qx, qy = np.interp(toward, cum, xy[:, 0]), np.interp(toward, cum, xy[:, 1])
        dx, dy = (px - qx) * M_PER_DEG_LON, (py - qy) * M_PER_DEG_LAT
        d = math.hypot(dx, dy) or 1.0
        k = int(EXT_M / STEP_M)
        t = np.arange(1, k + 1) * STEP_M
        return px + dx / d * t / M_PER_DEG_LON, py + dy / d * t / M_PER_DEG_LAT
    sx, sy = ext(0.0, min(10.0, L), -1)
    ex, ey = ext(L, max(L - 10.0, 0.0), 1)
    alon = np.concatenate([sx[::-1], lon, ex])
    alat = np.concatenate([sy[::-1], lat, ey])
    z = dem.sample(alon, alat)
    ok = ~np.isnan(z)
    inner = slice(len(sx), len(sx) + n)
    if ok[inner].sum() < 2:
        return s, None, None
    idx = np.arange(len(z))
    z = np.interp(idx, idx[ok], z[ok])           # fill holes
    raw = z[inner].copy()
    if len(z) >= 3:
        z = np.median(sliding_window_view(np.pad(z, 1, mode='edge'), 3), axis=1)
    z = running(running(z, FILL_W, np.max), FILL_W, np.min)     # closing: fill narrow dips
    z = running(running(z, CUT_W, np.min), CUT_W, np.max)       # opening: cut narrow bumps
    return s, raw, z[inner]


def main():
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(RAW, 'centerline.geojson')) as f:
        feats = json.load(f)['features']
    if len(feats) < 110_000:
        sys.exit('only %d centerline segments; expected about 122,000' % len(feats))

    names, name_index = [], {}

    def name_id(s):
        s = ' '.join((s or '').split())
        if s not in name_index:
            name_index[s] = len(names)
            names.append(s)
        return name_index[s]

    # 1. keep walkable lines
    lines, skipped = [], collections.Counter()
    structures = []   # highways, ramps, bridges, tunnels and anything off grade
    for feat in feats:
        p = feat['properties']
        rw = p.get('rw_type')
        if rw in ('2', '3', '4', '9') or (p.get('from_level_code') or GROUND) != GROUND or (p.get('to_level_code') or GROUND) != GROUND:
            structures.extend([(c[0], c[1]) for c in line] for line in feat['geometry']['coordinates'])
        if p.get('status') != '2':
            skipped['not built'] += 1; continue
        if rw not in RW_KIND:
            skipped['not walkable type'] += 1; continue
        if p.get('nonped') == 'V':
            skipped['vehicles only'] += 1; continue
        kind = RW_KIND[rw]
        fl, tl = p.get('from_level_code') or GROUND, p.get('to_level_code') or GROUND
        if kind in (KIND_STREET, KIND_PATH) and not (fl == GROUND and tl == GROUND):
            kind = KIND_RAISED
        nm = name_id(p.get('stname_label') or p.get('full_street_name'))
        boro = int(p.get('boroughcode') or 0)
        for line in feat['geometry']['coordinates']:
            pts = [(c[0], c[1]) for c in line]
            if len(pts) >= 2 and line_len(pts) >= 0.5:
                lines.append((kind, fl, tl, nm, boro, pts))
    lines.sort(key=lambda r: tile_of(*r[5][0]))      # so tiles load in order
    print('lines', len(lines), 'skipped', dict(skipped), flush=True)

    # Points every 10 m along every structure line, in a grid of ~50 m cells.
    CELL = 0.0005
    sgrid = collections.defaultdict(list)
    for pts in structures:
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            n = max(1, int(dist_m(a, b) / 10))
            for j in range(n + 1):
                x = a[0] + (b[0] - a[0]) * j / n
                y = a[1] + (b[1] - a[1]) * j / n
                sgrid[(int(x // CELL), int(y // CELL))].append((x, y))

    def near_structure(piece):
        for i in range(len(piece) - 1):
            a, b = piece[i], piece[i + 1]
            n = max(1, int(dist_m(a, b) / 10))
            for j in range(n + 1):
                x = a[0] + (b[0] - a[0]) * j / n
                y = a[1] + (b[1] - a[1]) * j / n
                cx, cy = int(x // CELL), int(y // CELL)
                for gx in (cx - 1, cx, cx + 1):
                    for gy in (cy - 1, cy, cy + 1):
                        for q in sgrid.get((gx, gy), ()):
                            if dist_m((x, y), q) < NEAR_M:
                                return True
        return False

    # 2. profiles, pieces and edges
    dem = DEM()
    node_index, node_pt, node_level = {}, [], []

    def node(pt, level):
        key = (round(pt[0], 6), round(pt[1], 6), level)
        i = node_index.get(key)
        if i is None:
            i = len(node_pt)
            node_index[key] = i
            node_pt.append((pt[0], pt[1]))
            node_level.append(level)
        return i

    edges = []   # (a, b, kind, name, boro, geom-without-ends, length_m, z_a, z_b, raw_a, raw_b)
    filled = cliffs = 0
    for k, (kind, fl, tl, nm, boro, pts) in enumerate(lines):
        L = line_len(pts)
        if kind == KIND_RAISED:
            pieces, lv, prof = [pts], [(fl, tl)], None
        else:
            s, raw, zf = profile(dem, pts)
            prof = (s, raw, zf) if zf is not None else None
            if kind == KIND_STAIRS:
                pieces, lv = [pts], [(fl, tl)]
            else:
                pieces = split_line(pts, max(1, math.ceil(L / MAX_PIECE_M)))
                lv = [(GROUND, GROUND)] * len(pieces)
        at = 0.0
        for piece, (la, lb) in zip(pieces, lv):
            pl = line_len(piece)
            a, b = node(piece[0], la), node(piece[-1], lb)
            if prof is not None:
                s, raw, zf = prof
                za, zb = float(np.interp(at, s, zf)), float(np.interp(at + pl, s, zf))
                ra, rb = float(np.interp(at, s, raw)), float(np.interp(at + pl, s, raw))
                if max(abs(za - ra), abs(zb - rb)) > 2.0:
                    filled += 1
                if kind != KIND_STAIRS:
                    m = (s >= at - 0.01) & (s <= at + pl + 0.01)
                    zz = zf[m]
                    if len(zz) >= 3 and np.max(np.abs(zz[2:] - zz[:-2])) / (2 * STEP_M) > CLIFF:
                        za = zb = float('nan')
                        cliffs += 1
            else:
                za = zb = ra = rb = float('nan')
            at += pl
            if a == b:
                continue
            g = simplify(piece, SIMPLIFY_M)[1:-1]
            near = kind != KIND_RAISED and near_structure(piece)
            edges.append((a, b, kind, nm, boro, g, pl, za, zb, ra, rb, near))
        if k % 40000 == 0:
            print('  profiles', k, '/', len(lines), flush=True)
    print('edges', len(edges), 'nodes', len(node_pt),
          'pieces whose end moved more than 2 m after filtering:', filled,
          'pieces left ungraded at a cliff:', cliffs, flush=True)

    # 3. Level codes sometimes disagree where two segments meet: one says the
    # shared point is at grade, the other says it's raised. A real over/under
    # crossing never leaves a dead end at the crossing point, so when a node is
    # a dead end and another node sits at exactly the same point, join them.
    deg = collections.Counter()
    for e in edges:
        deg[e[0]] += 1; deg[e[1]] += 1
    by_pt = collections.defaultdict(list)
    for (x, y, lv), i in node_index.items():
        by_pt[(x, y)].append(i)
    remap = {}
    for ids in by_pt.values():
        if len(ids) < 2:
            continue
        keep = sorted(ids, key=lambda i: (node_level[i] != GROUND, -deg[i]))[0]
        for i in ids:
            if i != keep and deg[i] == 1:
                remap[i] = keep
    edges = [(remap.get(e[0], e[0]), remap.get(e[1], e[1])) + e[2:] for e in edges]
    edges = [e for e in edges if e[0] != e[1]]
    print('joined %d dead ends to a node at the same point on another level' % len(remap))

    def grade(e):
        if e[2] == KIND_RAISED or math.isnan(e[7]) or math.isnan(e[8]) or e[6] <= 0:
            return None
        return (e[8] - e[7]) / e[6]

    graded = [e for e in edges if e[2] != KIND_RAISED]
    with_z = [e for e in graded if grade(e) is not None]
    share = len(with_z) / len(graded)
    print('graded share %.4f' % share)
    if share < 0.95:
        sys.exit('only %.1f%% of at-grade segments got an elevation' % (100 * share))

    # 4. connectivity
    parent = list(range(len(node_pt)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for e in edges:
        ra, rb = find(e[0]), find(e[1])
        if ra != rb:
            parent[ra] = rb
    used = set()
    for e in edges:
        used.add(e[0]); used.add(e[1])
    comp = collections.Counter(find(i) for i in used)
    top = comp.most_common(2)
    big = top[0][1] / len(used)
    # Staten Island has no walkable link to the rest of the city (the
    # Verrazzano is closed to pedestrians), so it is its own network.
    two = (top[0][1] + top[1][1]) / len(used)
    print('largest component share %.4f, two largest %.4f, pieces %d' % (big, two, len(comp)))
    if two < 0.93:
        sys.exit('walking network is fragmented: the two largest pieces hold %.1f%% of nodes' % (100 * two))

    # ---------- findings ----------
    bands = [(0, .02, 'flat'), (.02, .05, 'gentle'), (.05, .0833, 'noticeable'),
             (.0833, .12, 'steep'), (.12, 9, 'very steep')]
    by_boro = {b: collections.Counter() for b in BOROS}
    city = collections.Counter()
    for e in with_z:
        if e[2] != KIND_STREET:
            continue
        g = abs(grade(e))
        for lo, hi, lab in bands:
            if lo <= g < hi:
                by_boro[str(e[4])][lab] += e[6]
                city[lab] += e[6]
                break

    def summarize(c):
        tot = sum(c.values())
        return {'miles': round(tot / 1609.34, 1),
                'band_miles': {lab: round(c[lab] / 1609.34, 1) for _, _, lab in bands},
                'share': {lab: round(c[lab] / tot, 4) for _, _, lab in bands}}
    boro_stats = {BOROS[b]: summarize(c) for b, c in by_boro.items() if sum(c.values())}
    city_stats = summarize(city)

    # Steepest named streets: pieces at least 40 m long, so a short reading
    # error can't top the list; the steepest piece per street per borough.
    best = {}
    for e in with_z:
        if e[2] != KIND_STREET or e[6] < 40 or e[11]:
            continue
        nm = names[e[3]]
        if not nm or nm.startswith('UNNAMED'):
            continue
        g = abs(grade(e))
        key = (nm, e[4])
        if key not in best or g > best[key][0]:
            a, b = node_pt[e[0]], node_pt[e[1]]
            best[key] = (g, e[6], ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), abs(e[8] - e[7]),
                         abs(e[10] - e[9]) / e[6])
    steepest = sorted(best.items(), key=lambda kv: -kv[1][0])[:60]
    steep_list = [{'name': nm, 'boro': BOROS.get(str(bo), ''), 'grade': round(v[0] * 100, 1),
                   'raw_grade': round(v[4] * 100, 1),
                   'length_ft': round(v[1] * FT), 'rise_ft': round(v[3] * FT),
                   'lon': round(v[2][0], 5), 'lat': round(v[2][1], 5)} for (nm, bo), v in steepest]

    boro_steep = {}
    for (nm, bo), v in sorted(best.items(), key=lambda kv: -kv[1][0]):
        b = BOROS.get(str(bo))
        if b and b not in boro_steep:
            boro_steep[b] = {'name': nm, 'grade': round(v[0] * 100, 1), 'length_ft': round(v[1] * FT),
                             'rise_ft': round(v[3] * FT), 'lon': round(v[2][0], 5), 'lat': round(v[2][1], 5)}

    # Highest street point, overall and per borough.
    touch = collections.defaultdict(set)
    for e in with_z:
        touch[e[0]].add(e[3]); touch[e[1]].add(e[3])

    def node_name(i):
        hwy = lambda t: any(w in t for w in ('PKWY', 'EXPWY', 'EXPY', 'SERVICE RD', 'PARKWAY'))
        ns = sorted({names[n] for n in touch.get(i, ()) if names[n] and not names[n].startswith('UNNAMED')},
                    key=lambda t: (hwy(t), t))
        return ' and '.join(ns[:2])
    hi_by = {}
    for e in with_z:
        if e[2] != KIND_STREET or e[11]:
            continue
        b = BOROS.get(str(e[4]))
        for nd, zz in ((e[0], e[7]), (e[1], e[8])):
            if b and (b not in hi_by or zz > hi_by[b][1]):
                hi_by[b] = (nd, zz)
    boro_high = {b: {'name': node_name(i), 'ft': round(zz * FT),
                     'lon': round(node_pt[i][0], 5), 'lat': round(node_pt[i][1], 5)}
                 for b, (i, zz) in hi_by.items()}
    highest = max(boro_high.values(), key=lambda h: h['ft'])

    stats = {
        'built': datetime.date.today().isoformat(),
        'segments_in_source': len(feats),
        'lines_kept': len(lines),
        'edges': len(edges), 'graded_edges': len(with_z), 'nodes': len(used),
        'graded_share': round(share, 4), 'largest_component_share': round(big, 4),
        'pieces_filled': filled, 'pieces_cliff': cliffs,
        'near_structure_edges': sum(1 for e in edges if e[11]),
        'near_structure_miles': round(sum(e[6] for e in edges if e[11] and e[2] == KIND_STREET) / 1609.34, 1),
        'cliff_miles': round(sum(e[6] for e in graded if e[2] != KIND_STAIRS and grade(e) is None) / 1609.34, 1),
        'skipped': dict(skipped),
        'params': {'step_m': STEP_M, 'ext_m': EXT_M, 'fill_m': FILL_M, 'cut_m': CUT_M,
                   'max_piece_m': MAX_PIECE_M},
        'city': city_stats, 'boroughs': boro_stats,
        'steepest': steep_list, 'highest': highest, 'borough_highest': boro_high,
        'borough_steepest': boro_steep,
        'bands': [{'label': lab, 'lo': lo, 'hi': hi} for lo, hi, lab in bands],
    }
    with open(os.path.join(DATA, 'stats.json'), 'w') as f:
        json.dump(stats, f, indent=1)

    # ---------- pack streets.json ----------
    # Only nodes that edges use, renumbered.
    keep_nodes = sorted(used)
    renum = {old: new for new, old in enumerate(keep_nodes)}
    Q = 1e5  # coordinates in 1/100,000 of a degree (about 1 m)
    nodes_flat, px, py = [], 0, 0
    for i in keep_nodes:
        x, y = round(node_pt[i][0] * Q), round(node_pt[i][1] * Q)
        nodes_flat += [x - px, y - py]
        px, py = x, y
    zq = lambda v: -32768 if math.isnan(v) else round(v * 10)   # decimetres
    ea, eb, ek, en, eo, eg, za, zb = [], [], [], [], [], [], [], []
    ef = []
    for a, b, kind, nm, boro, g, L, z1, z2, _, _, near in edges:
        ea.append(renum[a]); eb.append(renum[b]); ek.append(kind); en.append(nm); eo.append(boro)
        ef.append(1 if near else 0)
        za.append(zq(z1)); zb.append(zq(z2))
        if g:
            flat, qx, qy = [], round(node_pt[a][0] * Q), round(node_pt[a][1] * Q)
            for lon, lat in g:
                x, y = round(lon * Q), round(lat * Q)
                flat += [x - qx, y - qy]
                qx, qy = x, y
            eg.append(flat)
        else:
            eg.append(0)
    out = {'q': Q, 'nodes': nodes_flat, 'a': ea, 'b': eb, 'k': ek, 'n': en, 'o': eo,
           'za': za, 'zb': zb, 'f': ef, 'g': eg, 'names': names}
    with open(os.path.join(DATA, 'streets.json'), 'w') as f:
        json.dump(out, f, separators=(',', ':'))
    print('wrote data/streets.json %.1f MB' % (os.path.getsize(os.path.join(DATA, 'streets.json')) / 1e6))
    print(json.dumps({'city': stats['city'], 'highest': highest}, indent=1))
    for s in steep_list[:30]:
        print(s)


if __name__ == '__main__':
    main()
