# Uphill: methodology

Uphill grades every walkable block in New York City from a federal lidar survey and finds the flattest or the hilliest walk between any two points. All figures below were computed on Nov. 23, 2026, by `build.py` in this repository. Rerun `fetch_dem.py` and `build.py` to reproduce them.

## Sources

**Streets.** The city's Street Centerline file (CSCL), [NYC Open Data dataset inkn-q76z](https://data.cityofnewyork.us/City-Government/Centerline/inkn-q76z), downloaded Nov. 23, 2026, as GeoJSON from `https://data.cityofnewyork.us/resource/inkn-q76z.geojson?$limit=200000`: 122,331 segments. Fields used: geometry, `rw_type` (roadway type), `nonped` (pedestrian restriction), `status`, `from_level_code` and `to_level_code` (13 means at ground level), `boroughcode` and `stname_label`.

**Elevation.** The U.S. Geological Survey's [3D Elevation Program](https://www.usgs.gov/3d-elevation-program) one-meter bare-earth model, requested from the National Map's [3DEPElevation ImageServer](https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer) (`exportImage`, bilinear resampling) in 61 tiles of 2,000 by 2,000 pixels. Each pixel is 0.000025 degrees on a side: about 2.8 meters north to south and 2.1 meters east to west. For every point in the city the one-meter model comes from a single survey, the [USGS New York CMGP Sandy lidar](https://prd-tnm.s3.amazonaws.com/index.html?prefix=StagedProducts/Elevation/metadata/NY_New_York_CMGP_SANDY_LiDAR_15/) (project NY_New_York_CMGP_SANDY_LiDAR_15). Its metadata gives collection dates from Aug. 6, 2013, to April 21, 2014. Elevations are meters above the NAVD 88 datum, shown on the page in feet (1 m = 3.28084 ft).

The script asks the ImageServer to report each tile's extent before downloading it, and stops if the extent differs from the request. An early run that asked for non-square pixels was silently stretched by the server, which misplaced every tile by up to 800 meters; square pixels fixed that.

**Picture only.** The shaded relief and the tilted 3D view use [Terrain Tiles on AWS](https://registry.opendata.aws/terrain-tiles/). They aren't used in any calculation. The basemap's water and buildings are CARTO vector tiles from OpenStreetMap data. Address search uses [NYC GeoSearch](https://geosearch.planninglabs.nyc/).

## What counts as walkable

Kept, if built (`status` 2) and not marked vehicles only (`nonped` V):

| `rw_type` | Meaning | Treatment |
|---|---|---|
| 1 | Street | graded |
| 10 | Alley | graded, counted as street |
| 5 | Boardwalk | graded, counted as path |
| 6 | Path or trail | graded, counted as path |
| 7 | Step street | graded, drawn as stairs |
| 3 | Bridge | connects the walking network, not graded |

Dropped: 9,397 segments of other types (highways, ramps, tunnels, driveways, ferry routes, U-turns, non-physical connectors), 2,541 marked vehicles only and 39 not built. Streets and paths whose level codes aren't both 13 (at grade) are treated like bridges: they connect the network but aren't graded.

Where two segments meet at exactly the same point but their level codes disagree, and one of them is a dead end there, the two are joined (473 cases). A real over-and-under crossing never leaves a dead end at the crossing point.

Blocks longer than 100 meters (328 feet) are cut into equal pieces no longer than that, so a hump in the middle of a long block shows up. That gives 173,294 pieces; 169,284 are graded.

Street miles (6,516) count graded streets and alleys only, not paths, boardwalks, step streets or bridges. Where the city's file draws a divided street as two lines, both count.

## How a grade is measured

For each line:

1. Sample the elevation every 5 meters along it, and every 5 meters for 60 meters past each end, continuing in the direction the line is heading at that end.
2. Fill any missing readings by linear interpolation.
3. Take a three-sample running median, which drops single bad readings.
4. Apply a **closing** over 120 meters: a running maximum, then a running minimum. It fills any dip narrower than 120 meters. The model is bare earth, with bridges removed, so a street that crosses a highway or railroad cut on a short bridge otherwise appears to plunge into the cut.
5. Apply an **opening** over 40 meters: a running minimum, then a running maximum. It shaves any bump narrower than 40 meters, for the rare overpass left in the ground model.
6. Read the filtered elevation at each piece's two ends.

The ends of 2,323 pieces moved more than 2 meters because of steps 4 and 5.

**Grade** = (elevation at the far end − elevation at the near end) ÷ the piece's length along the line. The page shows its absolute value in percent.

**Cliff check.** A piece whose filtered profile rises or falls more than 35% over any 10 meters is treated as a wall, a bridge abutment or the edge of a cut, and is left ungraded: 169 pieces, 4.6 miles. Step streets are exempt.

**Bands.**

| Band | Grade | Why the break |
|---|---|---|
| Flat | under 2% | |
| Gentle | 2% to 5% | |
| Noticeable | 5% to 8.33% | federal accessibility rules treat a walkway steeper than 1 in 20 (5%) as a ramp ([ADA 403.3](https://www.access-board.gov/ada/#ada-403)) |
| Steep | 8.33% to 12% | 1 in 12 (8.33%) is the steepest ramp allowed ([ADA 405.2](https://www.access-board.gov/ada/#ada-405)) |
| Very steep | 12% or more | |

## Stretches near highways and bridges

The filters don't catch everything, especially where a street runs alongside a sunken highway or meets a viaduct. Every graded piece that comes within 30 meters of a highway, ramp, bridge or tunnel segment, or of any segment off grade, is flagged (checked every 10 meters along both). That covers 471.9 street miles. Flagged pieces stay on the map, with a warning in their label, and count toward the totals. They are left off the steepest-streets list and the borough high points.

## Findings, as computed

- 6,516 graded street miles: 5,271.8 flat, 1,031.4 gentle, 176.6 noticeable, 30.0 steep, 6.3 very steep. Flat is 80.9%, so "four in five."
- 36.3 miles are steeper than 8.33% (steep plus very steep). Staten Island has 19.5 of them.
- Share of each borough's street miles that are not flat: Staten Island 33.5% of 969.6 miles, the Bronx 29.3% of 848.0, Manhattan 19.8% of 546.8, Queens 16.1% of 2,473.4, Brooklyn 9.7% of 1,678.2.
- The steepest streets: among named, graded, unflagged street pieces at least 40 meters long, the steepest piece of each street in each borough. Chestnut Place on Staten Island is first, at 20.0% (38 feet of rise in 190 feet).
- High points: the highest graded, unflagged street piece end in each borough. Staten Island, Elmhurst Avenue, 400 ft; the Bronx, Goodridge and Grosvenor avenues, 279 ft; Manhattan, Pinehurst Avenue, 260 ft; Queens, a local road the city's file names Grand Central Parkway, at the Nassau County line, 258 ft; Brooklyn, 17th Street and Prospect Park West, 180 ft.

## Checks against published figures

- [Todt Hill](https://en.wikipedia.org/wiki/Todt_Hill), the city's highest point, is 401 feet. The highest street reading, near its summit, is 400.
- [Bennett Park](https://en.wikipedia.org/wiki/Bennett_Park) holds Manhattan's highest natural point, 265 feet. The highest Manhattan street reading, on Pinehurst Avenue beside the park, is 260.
- [Duffy's Hill](https://en.wikipedia.org/wiki/Duffy%27s_Hill), Lexington Avenue between 102nd and 103rd streets, is reported at 12.6% (some sources say 14%). This method gives 12.1%.

## How walks are chosen

The walking network is the graph of pieces, joined where they share an end. Each click or address snaps to the nearest piece end within 600 meters. Routes are found with Dijkstra's algorithm. The page always shows the shortest walk, plus the flattest or the hilliest, whichever the reader picks:

- **Shortest:** cost = length.
- **Flattest:** cost = length × (1 + (grade ÷ 5%)²). A 2% block costs 1.16 times its length, a 5% block twice, a 10% block five times. Step streets cost 40 times their length, so the route avoids stairs unless there's no way around. Bridges and other ungraded pieces cost their length.
- **Hilliest:** the walk with the most total climb whose length is no more than the larger of 1.5 times the shortest walk and the shortest walk plus 400 meters. A cheaper cost for steep blocks alone barely changes routes (tested Nov. 24, 2026, on 93 sample walks in hilly and flat neighborhoods: fewer than one in six detoured, by about 3%), because a detour to a hill adds flat blocks that outweigh the discount. So the page searches outward from both ends, with each piece costing length × (0.4 + 0.6 ÷ (1 + (grade ÷ 5%)²)) (a 5% block 0.7 times its length, a 10% block 0.52 times), keeping the real length, rise and fall along every search path. Each corner both searches reach within the length limit is a candidate waypoint; its climb is the rise from the start to the corner plus the rise from the corner on to the end. The page takes the candidate with the most climb whose two halves share no block, so the walk never doubles back down a dead end. Step streets are treated like any other block. On the same 93 sample walks the hilliest walk had a median of twice the climb of the shortest, at a median of 1.45 times the length.

The route summary reports distance, total climb (the sum of rises along the route in the direction of travel), the steepest piece at least 20 meters long, and the number of step streets used. Staten Island has no walkable link to the other boroughs (the Verrazzano-Narrows Bridge is closed to pedestrians, and the ferry isn't in the network), so routes can't cross between them.

## Limits

- The survey dates from 2013 and 2014. Streets built or regraded since then are graded on the old ground.
- Grades follow the street's centerline. A sidewalk can differ, especially where it steps or ramps.
- A step street's grade is its overall rise over its length, not the pitch of the stairs.
- Short pieces carry more noise: a 10-centimeter error is a full point of grade over 10 meters.
- The flag for stretches near highways and bridges is a distance rule, not an inspection. Some flagged pieces are fine, and a few misread pieces away from highways may remain.

## Files

- `fetch_dem.py` downloads the elevation tiles into `raw/dem/` (not committed; about 720 MB).
- `grid.py` defines the tile grid.
- `build.py` reads `raw/centerline.geojson` and the tiles and writes `data/streets.json` (the network, packed as integer arrays) and `data/stats.json` (the findings). It stops if fewer than 110,000 centerline segments load, if fewer than 95% of at-grade pieces get an elevation, or if the two largest connected pieces of the network hold less than 93% of its nodes.
- `index.html` is the page.
